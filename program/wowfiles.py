"""Pure-Python reader for World of Warcraft's local game data (CASC), just enough for LiveMap:
read a file by FileDataID or by path from your own WoW install. No compiled code needed.

Pieces: .build.info -> build config -> encoding file (content key -> encoded key)
        -> local index (encoded key -> data archive + offset) -> BLTE decoding (zlib)
        root file: FileDataID / path hash -> content key
"""
import bisect, glob, hashlib, os, struct, zlib


# --- path hashing (Bob Jenkins' lookup3 'hashlittle2', as used by WoW) --------------------

def _rot(x, k):
    return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF


def _hashlittle2(data, pc=0, pb=0):
    length = len(data)
    a = b = c = (0xDEADBEEF + length + pc) & 0xFFFFFFFF
    c = (c + pb) & 0xFFFFFFFF
    i = 0
    while length > 12:
        a = (a + int.from_bytes(data[i:i + 4], "little")) & 0xFFFFFFFF
        b = (b + int.from_bytes(data[i + 4:i + 8], "little")) & 0xFFFFFFFF
        c = (c + int.from_bytes(data[i + 8:i + 12], "little")) & 0xFFFFFFFF
        a = (a - c) & 0xFFFFFFFF; a ^= _rot(c, 4); c = (c + b) & 0xFFFFFFFF
        b = (b - a) & 0xFFFFFFFF; b ^= _rot(a, 6); a = (a + c) & 0xFFFFFFFF
        c = (c - b) & 0xFFFFFFFF; c ^= _rot(b, 8); b = (b + a) & 0xFFFFFFFF
        a = (a - c) & 0xFFFFFFFF; a ^= _rot(c, 16); c = (c + b) & 0xFFFFFFFF
        b = (b - a) & 0xFFFFFFFF; b ^= _rot(a, 19); a = (a + c) & 0xFFFFFFFF
        c = (c - b) & 0xFFFFFFFF; c ^= _rot(b, 4); b = (b + a) & 0xFFFFFFFF
        length -= 12
        i += 12
    if length == 0:
        return c, b
    tail = data[i:] + b"\0" * (12 - length)
    a = (a + int.from_bytes(tail[0:4], "little")) & 0xFFFFFFFF
    b = (b + int.from_bytes(tail[4:8], "little")) & 0xFFFFFFFF
    c = (c + int.from_bytes(tail[8:12], "little")) & 0xFFFFFFFF
    c ^= b; c = (c - _rot(b, 14)) & 0xFFFFFFFF
    a ^= c; a = (a - _rot(c, 11)) & 0xFFFFFFFF
    b ^= a; b = (b - _rot(a, 25)) & 0xFFFFFFFF
    c ^= b; c = (c - _rot(b, 16)) & 0xFFFFFFFF
    a ^= c; a = (a - _rot(c, 4)) & 0xFFFFFFFF
    b ^= a; b = (b - _rot(a, 14)) & 0xFFFFFFFF
    c ^= b; c = (c - _rot(b, 24)) & 0xFFFFFFFF
    return c, b


def path_hash(path):
    """WoW's 64-bit file name hash (uppercase, backslashes)."""
    c, b = _hashlittle2(path.upper().replace("/", "\\").encode())
    return (c << 32) | b


# --- BLTE ----------------------------------------------------------------------------------

def blte_decode(data):
    if data[:4] != b"BLTE":
        raise ValueError("not BLTE data")
    header_size = struct.unpack(">I", data[4:8])[0]
    if header_size == 0:
        return _blte_chunk(data[8:])
    count = int.from_bytes(data[9:12], "big")
    pos, out = header_size, []
    for k in range(count):
        comp, _decomp = struct.unpack(">II", data[12 + k * 24:20 + k * 24])
        out.append(_blte_chunk(data[pos:pos + comp]))
        pos += comp
    return b"".join(out)


def _blte_chunk(chunk):
    mode = chunk[:1]
    if mode == b"N":
        return chunk[1:]
    if mode == b"Z":
        return zlib.decompress(chunk[1:])
    if mode == b"F":
        return blte_decode(chunk[1:])
    if mode == b"E":
        raise ValueError("encrypted file")
    raise ValueError(f"unsupported BLTE chunk type {mode!r}")


# --- the storage ---------------------------------------------------------------------------

class Storage:
    def __init__(self, wow_folder, product="wowf"):
        self.data_dir = os.path.join(wow_folder, "Data")
        build_key = self._build_key(wow_folder, product)
        cfg = self._read_config(build_key)
        self._load_indexes()
        enc_ekey = bytes.fromhex(cfg["encoding"].split()[1])
        self._load_encoding(blte_decode(self._read_ekey(enc_ekey)))
        self._root = blte_decode(self._read_ckey(bytes.fromhex(cfg["root"].split()[0])))
        self._fdid, self._names = None, None

    # .build.info + build config
    def _build_key(self, wow_folder, product):
        with open(os.path.join(wow_folder, ".build.info"), encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
        cols = [c.split("!")[0] for c in lines[0].split("|")]
        rows = [dict(zip(cols, l.split("|"))) for l in lines[1:]]
        for r in rows:
            if r.get("Product") == product and r.get("Active", "1") == "1":
                return r["Build Key"]
        for r in rows:
            if r.get("Product") == product:
                return r["Build Key"]
        raise OSError(f"no '{product}' build found in .build.info")

    def _read_config(self, key):
        path = os.path.join(self.data_dir, "config", key[:2], key[2:4], key)
        cfg = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                if " = " in line:
                    k, v = line.split(" = ", 1)
                    cfg[k.strip()] = v.strip()
        return cfg

    # local indexes: encoded key (first 9 bytes) -> (archive, offset, size); sorted, binary-searched
    def _load_indexes(self):
        newest = {}
        for p in glob.glob(os.path.join(self.data_dir, "data", "*.idx")):
            name = os.path.basename(p)
            bucket, version = int(name[:2], 16), int(name[2:10], 16)
            if bucket not in newest or version > newest[bucket][0]:
                newest[bucket] = (version, p)
        self._idx = {}
        for bucket, (_, p) in newest.items():
            with open(p, "rb") as f:
                raw = f.read()
            header_hash_size = struct.unpack("<I", raw[:4])[0]
            pos = (8 + header_hash_size + 15) & ~15
            entries_size = struct.unpack("<I", raw[pos:pos + 4])[0]
            ent = raw[pos + 8:pos + 8 + entries_size]
            n = len(ent) // 18
            keys = [ent[i * 18:i * 18 + 9] for i in range(n)]
            self._idx[bucket] = (keys, ent)

    @staticmethod
    def _bucket(ekey9):
        x = 0
        for b in ekey9:
            x ^= b
        return (x & 0xF) ^ (x >> 4)

    def _read_ekey(self, ekey):
        k9 = ekey[:9]
        keys, ent = self._idx[self._bucket(k9)]
        i = bisect.bisect_left(keys, k9)
        if i == len(keys) or keys[i] != k9:
            raise KeyError("file not in local storage")
        rec = ent[i * 18:i * 18 + 18]
        loc = int.from_bytes(rec[9:14], "big")
        archive, offset = loc >> 30, loc & 0x3FFFFFFF
        size = struct.unpack("<I", rec[14:18])[0]
        with open(os.path.join(self.data_dir, "data", f"data.{archive:03d}"), "rb") as f:
            f.seek(offset + 30)              # skip the 30-byte local header
            return f.read(size - 30)

    # encoding: content key -> encoded key (paged, binary-searched)
    def _load_encoding(self, enc):
        if enc[:2] != b"EN":
            raise ValueError("bad encoding file")
        ce_page_kb = struct.unpack(">H", enc[5:7])[0]
        ce_pages = struct.unpack(">I", enc[9:13])[0]
        espec_size = struct.unpack(">I", enc[18:22])[0]
        table = 22 + espec_size
        self._enc = enc
        self._enc_first = [enc[table + i * 32:table + i * 32 + 16] for i in range(ce_pages)]
        self._enc_pages = table + ce_pages * 32
        self._enc_page_size = ce_page_kb * 1024

    def _ckey_to_ekey(self, ckey):
        i = bisect.bisect_right(self._enc_first, ckey) - 1
        if i < 0:
            raise KeyError("unknown content key")
        pos = self._enc_pages + i * self._enc_page_size
        end = pos + self._enc_page_size
        enc = self._enc
        while pos + 22 <= end:
            count = enc[pos]
            if count == 0:
                break
            ck = enc[pos + 6:pos + 22]
            if ck == ckey:
                return enc[pos + 22:pos + 38]
            pos += 22 + 16 * count
        raise KeyError("unknown content key")

    def _read_ckey(self, ckey):
        return self._read_ekey(self._ckey_to_ekey(ckey))

    # root: FileDataID / path hash -> content key
    def _parse_root(self):
        r = self._root
        fdid, names = {}, {}
        pos = 0
        if r[:4] in (b"TSFM", b"MFST"):
            a, b = struct.unpack("<II", r[4:12])
            if a < 100:                                   # newer header: size, version, counts
                header_size, version = a, b
                pos = header_size
            else:
                version, pos = 1, 12
        else:
            raise ValueError("unsupported root format")
        n_total = len(r)
        while pos + 12 <= n_total:
            if version >= 2:
                count, locale, f1, f2 = struct.unpack("<IIII", r[pos:pos + 16])
                f3 = r[pos + 16]
                flags = f1 | f2 | (f3 << 17)
                pos += 17
            else:
                count, flags, locale = struct.unpack("<III", r[pos:pos + 12])
                pos += 12
            deltas = struct.unpack(f"<{count}i", r[pos:pos + 4 * count])
            pos += 4 * count
            ckeys_at = pos
            pos += 16 * count
            has_names = not (flags & 0x10000000)
            names_at = pos
            if has_names:
                pos += 8 * count
            if not (locale & 0x2 or locale == 0xFFFFFFFF) or (flags & 0x80):   # enUS / all; skip low-violence
                continue
            fid = -1
            for k in range(count):
                fid = fid + 1 + deltas[k]
                if fid not in fdid:
                    fdid[fid] = ckeys_at + 16 * k
            if has_names:
                for k, h in enumerate(struct.unpack(f"<{count}Q", r[names_at:names_at + 8 * count])):
                    names.setdefault(h, ckeys_at + 16 * k)
        self._fdid, self._names = fdid, names

    def read(self, file_id):
        if self._fdid is None:
            self._parse_root()
        at = self._fdid.get(int(file_id))
        if at is None:
            raise KeyError(f"FileDataID {file_id} not found")
        return blte_decode(self._read_ckey(self._root[at:at + 16]))

    def read_name(self, path):
        if self._names is None:
            self._parse_root()
        at = self._names.get(path_hash(path))
        if at is None:
            raise KeyError(f"{path} not found")
        return blte_decode(self._read_ckey(self._root[at:at + 16]))

    def close(self):
        pass
