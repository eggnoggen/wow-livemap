#!/usr/bin/env python3
"""Builds the release download dist/WoWLiveMap-<version>.zip (text files only)."""
import os, re, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(ROOT, "program", "LiveMap.pyw")).read()).group(1)
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
out = os.path.join(ROOT, "dist", f"WoWLiveMap-{version}.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for f in ("LiveMap.pyw", "wowfiles.py", "luasv.py"):
        z.write(os.path.join(ROOT, "program", f), f"WoWLiveMap/{f}")
    for f in ("README.md", "LICENSE", "CHANGELOG.md"):
        z.write(os.path.join(ROOT, f), f"WoWLiveMap/{f}")
    for f in sorted(os.listdir(os.path.join(ROOT, "addon", "LiveMap"))):
        z.write(os.path.join(ROOT, "addon", "LiveMap", f), f"WoWLiveMap/LiveMap/{f}")
print(out, os.path.getsize(out) // 1024, "KB")
