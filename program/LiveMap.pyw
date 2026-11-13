#!/usr/bin/env python3
"""WoW Forever LiveMap - a sharp, live map of where you are, in its own window (e.g. on a second monitor).

How it works: the LiveMap addon draws a tiny strip of colored squares in the top-left corner of the
game that encodes your map, position and facing. This program reads those pixels from the screen
(screen capture only - it never touches the game, its memory, or sends any input) and draws the map.
Map pictures are extracted from YOUR OWN game files the first time you visit each zone.

Close with the window's close button or Esc.
"""
import base64, ctypes, ctypes.util, glob, io, json, math, os, sys, time
import tkinter as tk
from tkinter import filedialog, messagebox


def _ensure_pillow():
    """Pillow (image library) is installed automatically the first time, from PyPI via pip."""
    try:
        import PIL  # noqa: F401
        return
    except ImportError:
        pass
    import subprocess
    root = tk.Tk()
    root.title("WoW LiveMap")
    tk.Label(root, text="First start: installing the Pillow image library...\nThis takes a minute.",
             padx=30, pady=20).pack()
    root.update()
    flags = 0x08000000 if sys.platform == "win32" else 0          # CREATE_NO_WINDOW
    exe = sys.executable.replace("pythonw.exe", "python.exe")
    r = subprocess.run([exe, "-m", "pip", "install", "--user", "pillow"], capture_output=True, text=True,
                       creationflags=flags)
    root.destroy()
    if r.returncode != 0:
        messagebox.showerror("WoW LiveMap", "Couldn't install Pillow automatically.\n\nOpen a command prompt and run:\n"
                             "py -m pip install pillow\n\n" + (r.stderr or "")[-500:])
        sys.exit(1)
    import site, importlib
    importlib.invalidate_caches()
    if site.getusersitepackages() not in sys.path:
        sys.path.append(site.getusersitepackages())


_ensure_pillow()
from PIL import Image, ImageGrab

IS_WIN = sys.platform == "win32"
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import luasv

if IS_WIN:
    try:   # real pixel coordinates on scaled (high-DPI) Windows displays
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "WoWLiveMap")
CACHE = os.path.join(DATA_DIR, "maps")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
MAP_VERSION = 2
NBLOCKS = 22
VERSION = "1.0.0"
FPS = 10
BG, FG, GOLD = "#0b0c10", "#e8e2cf", "#ffd100"
DEFAULT_GEOMETRY = "1000x800+60+60"


# --- settings & finding WoW ---------------------------------------------------------

def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(s, f, indent=1)


def is_wow_folder(p):
    return bool(p) and os.path.isdir(os.path.join(p, "_forever_")) and os.path.isdir(os.path.join(p, "Data"))


def guess_wow_folder():
    cands = []
    if IS_WIN:
        try:
            import winreg
            for key in (r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft",
                        r"SOFTWARE\Blizzard Entertainment\World of Warcraft"):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                        p = winreg.QueryValueEx(k, "InstallPath")[0]
                        cands += [p, os.path.dirname(p.rstrip("\\/"))]
                except OSError:
                    pass
        except ImportError:
            pass
        for drive in "CDEFGH":
            cands += [rf"{drive}:\Program Files (x86)\World of Warcraft", rf"{drive}:\Program Files\World of Warcraft",
                      rf"{drive}:\World of Warcraft", rf"{drive}:\Games\World of Warcraft"]
    else:
        cands += glob.glob(os.path.expanduser("~/Games/*/drive_c/Program Files (x86)/World of Warcraft"))
        cands += ["/mnt/games/World of Warcraft"] + glob.glob("/mnt/*/World of Warcraft") + glob.glob("/media/*/*/World of Warcraft")
    for p in cands:
        if is_wow_folder(p):
            return p
    return None


def wow_folder(settings):
    p = settings.get("wow_folder")
    if is_wow_folder(p):
        return p
    p = guess_wow_folder()
    if not p:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("WoW LiveMap", "Please pick your 'World of Warcraft' folder\n(the one that contains '_forever_').")
        while True:
            p = filedialog.askdirectory(title="Pick your World of Warcraft folder")
            if not p:
                sys.exit(0)
            if os.path.basename(p.rstrip("/\\")) == "_forever_":
                p = os.path.dirname(p.rstrip("/\\"))
            if is_wow_folder(p):
                break
            messagebox.showerror("WoW LiveMap", "That folder doesn't contain '_forever_'. Try again.")
        root.destroy()
    settings["wow_folder"] = p
    save_settings(settings)
    return p


def export_file(wow):
    """The newest LiveMap save file across all accounts."""
    files = glob.glob(os.path.join(wow, "_forever_", "WTF", "Account", "*", "SavedVariables", "LiveMap.lua"))
    return max(files, key=os.path.getmtime) if files else None


# --- map pictures ---------------------------------------------------------------------

class Maps:
    def __init__(self, wow):
        self.wow = wow
        self.index, self.sv_time = {}, None
        self._storage, self._overlays = None, None
        os.makedirs(CACHE, exist_ok=True)
        self.reload_index()

    def storage(self):
        if self._storage is None:
            import wowfiles
            self._storage = wowfiles.Storage(self.wow)
        return self._storage

    def reload_index(self):
        sv = export_file(self.wow)
        t = os.path.getmtime(sv) if sv else None
        if t == self.sv_time:
            return
        self.sv_time = t
        idx_file = os.path.join(CACHE, "index.json")
        try:
            with open(idx_file) as f:
                cached = json.load(f)
            if cached.get("sv_time") == t and cached.get("sv") == sv:
                self.index = cached["maps"]
                return
        except Exception:
            pass
        if not sv:
            self.index = {}
            return
        data = luasv.load(sv).get("LiveMapExport") or {}
        maps = {}
        for mid, m in (data.get("maps") or {}).items():
            tiles = m.get("tiles") or {}
            maps[str(mid)] = {"name": m.get("name"), "w": m.get("w"), "h": m.get("h"),
                              "tw": m.get("tw"), "th": m.get("th"), "tiles": [tiles[k] for k in sorted(tiles)]}
        self.index = maps
        with open(idx_file, "w") as f:
            json.dump({"sv": sv, "sv_time": t, "maps": maps}, f)

    def name(self, mid):
        return (self.index.get(str(mid)) or {}).get("name") or (f"map {mid}" if mid else "")

    def overlays(self):
        """Detail overlays (forests, towns, roads) from Leatrix Maps' reveal list, if installed."""
        if self._overlays is not None:
            return self._overlays
        import re
        self._overlays = {}
        path = os.path.join(self.wow, "_forever_", "Interface", "AddOns", "Leatrix_Maps", "Leatrix_Maps_Reveal.lua")
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            return self._overlays
        for m in re.finditer(r"--\[\[(\d+):[^\]]*\]\]\s*\[\d+\]\s*=\s*\{(.*?)\},\s*$", text, re.M):
            items = []
            for k, v in re.findall(r'\["(\d+:\d+:\d+:\d+)"\]\s*=\s*"([\d,\s]+)"', m.group(2)):
                w, h, x, y = map(int, k.split(":"))
                items.append((w, h, x, y, [int(f) for f in v.replace(" ", "").split(",") if f]))
            self._overlays[int(m.group(1))] = items
        return self._overlays

    def image(self, mid):
        path = os.path.join(CACHE, f"{mid}_v{MAP_VERSION}.png")
        if os.path.exists(path):
            return Image.open(path).convert("RGB")
        m = self.index.get(str(mid))
        if not m or not m["tiles"]:
            return None
        st = self.storage()
        w, h, tw, th = int(m["w"]), int(m["h"]), int(m["tw"]), int(m["th"])
        cols = max(1, math.ceil(w / tw))
        img = Image.new("RGB", (w, h), BG)
        for i, fid in enumerate(m["tiles"]):
            try:
                img.paste(Image.open(io.BytesIO(st.read(int(fid)))).convert("RGB"), ((i % cols) * tw, (i // cols) * th))
            except Exception:
                pass
        for ow, oh, ox, oy, fids in self.overlays().get(int(mid), []):
            ocols = max(1, math.ceil(ow / 256))
            for i, fid in enumerate(fids):
                try:
                    t = Image.open(io.BytesIO(st.read(int(fid)))).convert("RGBA")
                    img.paste(t, (ox + (i % ocols) * 256, oy + (i // ocols) * 256), t)
                except Exception:
                    pass
        img.save(path)
        return img

    def arrow(self):
        path = os.path.join(CACHE, "arrow.png")
        if not os.path.exists(path):
            data = self.storage().read_name("Interface\\WorldMap\\WorldMapArrow.blp")
            Image.open(io.BytesIO(data)).convert("RGBA").save(path)
        return Image.open(path).convert("RGBA")


# --- reading the screen ----------------------------------------------------------------

class _XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.POINTER(ctypes.c_ubyte)),
                ("byte_order", ctypes.c_int), ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
                ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int),
                ("bits_per_pixel", ctypes.c_int)]


class Screen:
    """Grabs screen areas in absolute desktop coordinates (all monitors)."""

    def __init__(self):
        self.x11 = None
        if not IS_WIN:
            try:
                x = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
                x.XOpenDisplay.restype = ctypes.c_void_p
                x.XOpenDisplay.argtypes = [ctypes.c_char_p]
                x.XDefaultRootWindow.restype = ctypes.c_ulong
                x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
                x.XGetImage.restype = ctypes.POINTER(_XImage)
                x.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
                x.XDestroyImage.argtypes = [ctypes.c_void_p]
                x.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
                x.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
                # never let an X error kill the program (Xlib's default handler exits)
                self._handler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda d, e: 0)
                x.XSetErrorHandler(self._handler)
                self.dpy = x.XOpenDisplay(None)
                self.root = x.XDefaultRootWindow(self.dpy)
                self.sw = x.XDisplayWidth(self.dpy, 0)
                self.sh = x.XDisplayHeight(self.dpy, 0)
                self.x11 = x
            except Exception:
                self.x11 = None
        if IS_WIN:
            u = ctypes.windll.user32
            self.vx, self.vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)    # virtual screen origin

    def grab(self, x0, y0, w, h):
        if self.x11:
            # keep the grab inside the screen
            x0, y0 = max(0, min(x0, self.sw - 1)), max(0, min(y0, self.sh - 1))
            w, h = max(1, min(w, self.sw - x0)), max(1, min(h, self.sh - y0))
            img = self.x11.XGetImage(self.dpy, self.root, x0, y0, w, h, 0xFFFFFFFF, 2)
            if not img:
                raise OSError("screen grab failed")
            im = img.contents
            raw = ctypes.string_at(im.data, im.bytes_per_line * h)
            out = Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX", im.bytes_per_line, 1).copy()
            self.x11.XDestroyImage(img)
            return out
        if IS_WIN:
            # with all_screens=True, PIL's bbox is relative to the virtual screen's top-left
            bx, by = x0 - self.vx, y0 - self.vy
            return ImageGrab.grab(bbox=(bx, by, bx + w, by + h), all_screens=True).convert("RGB")
        return ImageGrab.grab(bbox=(x0, y0, x0 + w, y0 + h)).convert("RGB")

    def everything(self):
        """Whole desktop, plus the desktop coordinates of its top-left corner."""
        if self.x11:
            return self.grab(0, 0, self.sw, self.sh), (0, 0)
        if IS_WIN:
            return ImageGrab.grab(all_screens=True).convert("RGB"), (self.vx, self.vy)
        return ImageGrab.grab().convert("RGB"), (0, 0)


class Beacon:
    """Finds the addon's colored strip anywhere on screen and decodes it."""

    def __init__(self, screen):
        self.screen = screen
        self.origin, self.block = None, None
        self.last_search = 0

    def locate(self):
        """Look for the magenta reference square, then measure the square size."""
        self.last_search = time.time()
        img, (ox, oy) = self.screen.everything()
        r, g, b = img.split()
        mask = Image.merge("RGB", (r.point(lambda v: 255 if v > 140 else 0),
                                   g.point(lambda v: 255 if v < 70 else 0),
                                   b.point(lambda v: 255 if v > 140 else 0))).convert("L").point(lambda v: 255 if v == 255 else 0)
        data, W = mask.tobytes(), img.width
        pos = data.find(b"\xff")
        tries = 0
        while pos != -1 and tries < 4000:
            tries += 1
            x, y = pos % W, pos // W
            run = 1
            while x + run < W and data[pos + run] == 255:
                run += 1
            if 1 <= run <= 8:
                self.origin, self.block = (ox + x, oy + y), run
                if self.read() is not None:
                    return True
            pos = data.find(b"\xff", pos + run)
        self.origin = None
        return False

    def read(self):
        if not self.origin:
            return None
        bs = self.block
        x0, y0 = self.origin
        shot = self.screen.grab(x0, y0, int(bs * NBLOCKS) + 1, int(bs) + 1)

        def px(i, row):
            return shot.getpixel((min(shot.width - 1, int((i + 0.5) * bs)), min(shot.height - 1, int((row + 0.5) * bs))))

        r1, r2 = px(0, 0), px(1, 0)                  # references [255,0,255] and [85,255,170]
        lv = (r1[1], r2[0], r2[2], (r1[0] + r1[2] + r2[1]) / 3)
        if not (lv[0] + 25 < lv[1] < lv[2] - 25 and lv[2] + 25 < lv[3]) or abs(r1[0] - r1[2]) > 10:
            return None
        syms = []
        for i in range(2, NBLOCKS):
            syms += [min(range(4), key=lambda k: abs(c - lv[k])) for c in px(i, 0)]
        data = []
        for i in range(15):
            v = 0
            for k in range(4):
                v = v * 4 + syms[i * 4 + k]
            data.append(v)
        if sum(data[:14]) % 256 != data[14]:
            return None
        mid = data[0] * 256 + data[1]
        facing = data[2] / 256 * 2 * math.pi
        x, y = data[3] * 256 + data[4], data[5] * 256 + data[6]
        cont = data[8] * 256 + data[9]
        cx, cy = data[10] * 256 + data[11], data[12] * 256 + data[13]
        return (mid, None if x == 65535 else (x / 65534, y / 65534), facing,
                cont, None if cx == 65535 else (cx / 65534, cy / 65534))


# --- window -------------------------------------------------------------------------------

def to_photo(img, alpha=False):
    buf = io.BytesIO()
    if alpha:
        img.save(buf, "PNG")
        return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
    img.convert("RGB").save(buf, "PPM")
    return tk.PhotoImage(data=buf.getvalue(), format="PPM")


class Panel:
    def __init__(self, app, top, map_h, tag):
        self.app, self.top, self.height, self.tag = app, top, map_h + 44, tag
        self.title = app.canvas.create_text(20, top, anchor="nw", fill=GOLD, font=("Georgia", 22, "bold"), text="")
        self.note = app.canvas.create_text(20, top + 48, anchor="nw", fill=FG, font=("Segoe UI", 13), text="")
        self.mid, self.photo, self.rect = None, None, None

    def show(self, mid):
        if mid == self.mid:
            return
        self.mid = mid
        c, app = self.app.canvas, self.app
        c.delete(self.tag + "img")
        c.itemconfigure(self.title, text=app.maps.name(mid))
        try:
            img = app.maps.image(mid) if mid else None
        except Exception as e:
            img = None
            c.itemconfigure(self.note, text=f"Couldn't read the map from the game files: {e}")
            self.rect = None
            return
        if img is None:
            self.rect = None
            c.itemconfigure(self.note, text=("Do a /reload in WoW once so the addon's map list gets saved."
                                             if not app.maps.index else "No map picture for this area." if mid else ""))
            return
        c.itemconfigure(self.note, text="")
        scale = min(app.tw / img.width, (self.height - 46) / img.height)
        w, h = int(img.width * scale), int(img.height * scale)
        self.photo = to_photo(img.resize((w, h), Image.LANCZOS))
        x0, y0 = (app.tw - w) // 2, self.top + 44
        c.create_image(x0, y0, image=self.photo, anchor="nw", tags=self.tag + "img")
        c.tag_lower(self.tag + "img")
        self.rect = (x0, y0, w, h)

    def arrow(self, pos, facing, size):
        c = self.app.canvas
        c.delete(self.tag + "arrow")
        if pos and self.rect:
            x0, y0, w, h = self.rect
            photo = self.app.arrow_photo(size, facing)
            if photo:
                c.create_image(x0 + pos[0] * w, y0 + pos[1] * h, image=photo, anchor="center", tags=self.tag + "arrow")


class _NoPanel:
    mid = None
    def show(self, mid): pass
    def arrow(self, *a): pass


class App:
    def __init__(self):
        self.settings = load_settings()
        self.wow = wow_folder(self.settings)
        self.maps = Maps(self.wow)
        self.beacon = Beacon(Screen())
        self.root = tk.Tk()
        self.root.title(f"WoW LiveMap {VERSION}")
        self.root.configure(bg=BG)
        self.root.geometry(self.settings.get("geometry") or DEFAULT_GEOMETRY)
        self.root.minsize(400, 300)
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.bind("<Escape>", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.zone, self.cont = _NoPanel(), _NoPanel()
        self.size, self.status_text, self.state, self._arrows = None, "", None, {}
        self.missed = 0
        self.root.update_idletasks()
        self.layout()
        self.canvas.bind("<Configure>", self.on_resize)
        self.set_status("Looking for WoW... (LiveMap addon enabled, game visible on screen)")
        self.root.after(200, self.tick)
        self.root.mainloop()

    def close(self):
        self.settings["geometry"] = self.root.geometry()
        save_settings(self.settings)
        self.root.destroy()

    def on_resize(self, e):
        if (e.width, e.height) != self.size:
            if getattr(self, "_job", None):
                self.root.after_cancel(self._job)
            self._job = self.root.after(250, self.layout)

    def layout(self):
        self._job = None
        self.root.update_idletasks()
        self.tw, self.th = max(400, self.canvas.winfo_width()), max(300, self.canvas.winfo_height())
        self.size = (self.tw, self.th)
        old_zone, old_cont = self.zone.mid, self.cont.mid
        self.canvas.delete("all")
        map_h = int(self.tw * 668 / 1002)
        both = self.th >= 2 * (map_h + 52) + 50
        if not both:
            map_h = min(map_h, self.th - 100)
        self.zone = Panel(self, 8, map_h, "zone")
        y = 8 + map_h + 52
        self.cont = Panel(self, y, map_h, "cont") if both else _NoPanel()
        if both:
            y += map_h + 52
        self.status = self.canvas.create_text(20, y, anchor="nw", fill=FG, font=("Segoe UI", 16, "bold"),
                                              text=self.status_text, width=self.tw - 40)
        if old_zone:
            self.zone.show(old_zone)
        if old_cont:
            self.cont.show(old_cont)
        self.state = None

    def set_status(self, text):
        if text != self.status_text:
            self.status_text = text
            self.canvas.itemconfigure(self.status, text=text)

    def arrow_photo(self, size, facing):
        step = int(round(math.degrees(facing) / 5)) % 72
        key = (size, step)
        if key not in self._arrows:
            if not hasattr(self, "_arrow_src"):
                try:
                    self._arrow_src = self.maps.arrow()
                except Exception:
                    self._arrow_src = None
            if self._arrow_src is None:
                return None
            img = self._arrow_src.resize((size, size), Image.LANCZOS).rotate(step * 5, resample=Image.BICUBIC)
            self._arrows[key] = to_photo(img, alpha=True)
        return self._arrows[key]

    def tick(self):
        t0 = time.time()
        try:
            b = self.beacon.read()
            if b is None and time.time() - self.beacon.last_search > 3:
                if self.beacon.locate():
                    b = self.beacon.read()
        except Exception:
            b = None
        if b:
            self.missed = 0
            mid, pos, facing, cont, cpos = b
            if (mid and str(mid) not in self.maps.index) or (cont and str(cont) not in self.maps.index):
                self.maps.reload_index()
            self.zone.show(mid)
            self.cont.show(cont if cont != mid else None)
            state = (mid, cont, pos and (round(pos[0], 4), round(pos[1], 4)), round(facing, 2))
            if state != self.state:
                self.state = state
                self.zone.arrow(pos, facing, 44)
                self.cont.arrow(cpos, facing, 32)
                self.set_status(f"{pos[0] * 100:.1f}, {pos[1] * 100:.1f}" if pos else "No position here (instance?)")
        else:
            self.missed += 1
            if self.missed == 20:
                self.set_status("Can't see WoW right now - the game must be visible on screen with the LiveMap "
                                "addon enabled (not minimized or on a loading screen).")
        self.root.after(max(10, int(1000 / FPS - (time.time() - t0) * 1000)), self.tick)


if __name__ == "__main__":
    App()
