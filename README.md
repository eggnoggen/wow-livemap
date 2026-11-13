# WoW LiveMap

A sharp, live map of where you are in **World of Warcraft: Forever**, in its own window — put it on a
second monitor, or anywhere you like, at any size.

- Zone map and continent map, with WoW's own player arrow, updated 10 times a second
- Resizable: a tall window shows zone + continent, a short one just the zone
- Works with any monitor setup, windowed or full screen
- Fully detailed maps (forests, towns, roads) if you also use the [Leatrix Maps](https://www.curseforge.com/wow/addons/leatrix-maps) addon

<!-- screenshot goes here -->

## How it works

LiveMap has two parts:

1. **The LiveMap addon** (in `addon/`) draws a tiny strip of colored squares in the top-left corner of
   the game. The colors encode three things: which map you're on, where you are on it, and which way
   you're facing. On first login it also saves a list of which picture files make up each zone map.
2. **The LiveMap window** (in `program/`) reads that strip off your screen — the same way a screen
   recorder would — and draws the map. The map pictures are read straight from your own WoW
   installation (read-only), so nothing from Blizzard is included in this download.

It never touches the game, its memory, or your input, and it doesn't automate anything. It is **not** an
official Blizzard tool, so use it at your own discretion.

Everything is plain, readable text: Python and Lua. There are no `.exe` or `.dll` files.

| File | What it does |
|---|---|
| `program/LiveMap.pyw` | The map window: finds the strip on screen, decodes it, draws the map |
| `program/wowfiles.py` | Reads map pictures from your local WoW install (pure Python, read-only) |
| `program/luasv.py` | Reads the addon's saved map list |
| `addon/LiveMap/LiveMap.lua` | The in-game addon: map list export + the position strip |

## Install (Windows)

1. Install **Python** from [python.org/downloads](https://www.python.org/downloads/). In the installer,
   tick **"Add python.exe to PATH"**, then click **Install Now**.
2. Download the latest `WoWLiveMap-x.y.z.zip` from [Releases](../../releases) and unzip it, for example into Documents.
3. Copy the `LiveMap` addon folder into `World of Warcraft\_forever_\Interface\AddOns\`.
4. Start WoW, make sure **LiveMap** is ticked in the AddOns list, log in, wait about 10 seconds, then type `/reload` once.
   This saves the zone map list.
5. Double-click **`LiveMap.pyw`**. The first start installs the Pillow image library automatically, which takes about a minute.
   It may ask you to pick your `World of Warcraft` folder.
   If double-clicking does nothing, right-click it and choose **Open with → Python**.

Linux also works (tested with WoW under Wine). Run `python3 LiveMap.pyw`. You need Pillow and Tk:
`sudo apt install python3-pil python3-tk`.

## Using it

- WoW must be **visible** on screen, not minimized and not on a loading screen, and don't cover the corner strip.
- If it says it can't see WoW, switch WoW's Display Mode to **Windowed (Fullscreen)**.
- `/livemap off` hides the strip and the map stops updating. `/livemap on` brings it back.
- The first visit to each zone takes a moment while its map is read from your game files. After that it's instant.
- Close with the window's **X** or **Esc**. It remembers its size and position.

## Antivirus

Earlier test builds that were packed as an `.exe` got flagged by antivirus as a false positive. That's common with
homemade Python `.exe` files. Releases now contain only text files that you can read in any editor. If you want
to double-check, upload the zip to [VirusTotal](https://www.virustotal.com).

## License

MIT, see [LICENSE](LICENSE). World of Warcraft and its map art belong to Blizzard Entertainment. This project
includes none of it, and it isn't affiliated with or endorsed by Blizzard.
