"""Minimal parser for WoW SavedVariables files (Lua table literals)."""
import re

_TOKEN = re.compile(r'''\s*(?:
    (?P<comment>--[^\n]*) |
    (?P<str>"(?:\\.|[^"\\])*") |
    (?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?) |
    (?P<name>[A-Za-z_][A-Za-z0-9_]*) |
    (?P<sym>[{}\[\]=,;])
)''', re.X)


def _tokens(text):
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                return
            raise ValueError(f"cannot parse near: {text[pos:pos + 40]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "comment":
            continue
        yield kind, m.group(kind)


def _unescape(s):
    return bytes(s[1:-1], "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")


class _Parser:
    def __init__(self, text):
        self.toks = list(_tokens(text))
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.toks[self.i]
        if value is not None and tok[1] != value:
            raise ValueError(f"expected {value}, got {tok}")
        self.i += 1
        return tok

    def value(self):
        kind, v = self.take()
        if kind == "str":
            return _unescape(v)
        if kind == "num":
            return float(v) if any(c in v for c in ".eE") else int(v)
        if kind == "name":
            return {"true": True, "false": False, "nil": None}.get(v, v)
        if v == "{":
            return self.table()
        raise ValueError(f"unexpected {v}")

    def table(self):
        out, n = {}, 1
        while self.peek()[1] != "}":
            if self.peek()[1] == "[":
                self.take("[")
                key = self.value()
                self.take("]")
                self.take("=")
                out[key] = self.value()
            elif self.peek()[0] == "name" and self.toks[self.i + 1][1] == "=":
                key = self.take()[1]
                self.take("=")
                out[key] = self.value()
            else:
                out[n] = self.value()
                n += 1
            if self.peek()[1] in (",", ";"):
                self.take()
        self.take("}")
        return out


def load(path):
    """Returns {global_name: value} for a SavedVariables file."""
    with open(path, encoding="utf-8", errors="replace") as f:
        p = _Parser(f.read())
    result = {}
    while p.i < len(p.toks):
        name = p.take()[1]
        p.take("=")
        result[name] = p.value()
    return result
