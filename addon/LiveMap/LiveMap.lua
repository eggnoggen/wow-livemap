-- LiveMap: sends your map position to the WoW LiveMap window (e.g. on a second monitor).
--
-- 1) Map export (once): asks WoW which image tiles make up every map and saves the list in this
--    addon's saved variables, so the LiveMap window can read sharp map pictures from your own game files.
-- 2) Position strip: draws a tiny strip of colored squares in the top-left corner of the screen that
--    encodes which map you're on, where you are and which way you face. The LiveMap window reads those
--    pixels from the screen. That's all - nothing else leaves the game, and nothing is automated.
--
-- Strip layout: 22 blocks of 2x2 pixels, left to right. Each color channel uses only 4 levels
-- (0, 85, 170, 255 = 2 bits) so brightness/contrast settings can't scramble it.
--   blocks 1-2: reference colors [255,0,255] [85,255,170]
--   blocks 3-22: 15 bytes, 6 bits per block: map hi, map lo, facing, x hi, x lo, y hi, y lo, seq,
--                continent hi, continent lo, cx hi, cx lo, cy hi, cy lo, checksum

local EXPORT_VERSION = 1
local BLOCK = 2          -- block size in screen pixels
local NBLOCKS = 22
local LEVELS = { 0, 85, 170, 255 }
local HZ = 10            -- updates per second

LiveMapDB = LiveMapDB or {}

local function msg(s) print("|cff33ccffLiveMap:|r " .. s) end

-------------------------------------------------------------------------------
-- Map export
-------------------------------------------------------------------------------

local function exportMaps()
    if not (C_Map and C_Map.GetMapInfo and C_Map.GetMapArtLayers and C_Map.GetMapArtLayerTextures) then
        msg("this client can't list map images; export skipped.")
        return
    end
    local maps, n = {}, 0
    for id = 1, 3000 do
        local info = C_Map.GetMapInfo(id)
        if info and info.name and info.name ~= "" then
            local ok, layers = pcall(C_Map.GetMapArtLayers, id)
            if ok and layers and layers[1] then
                local L = layers[1]
                local okT, tiles = pcall(C_Map.GetMapArtLayerTextures, id, 1)
                if okT and tiles and #tiles > 0 then
                    maps[id] = { name = info.name, type = info.mapType, parent = info.parentMapID,
                                 w = L.layerWidth, h = L.layerHeight, tw = L.tileWidth, th = L.tileHeight,
                                 tiles = tiles }
                    n = n + 1
                end
            end
        end
    end
    LiveMapExport = { version = EXPORT_VERSION, maps = maps }
    msg(("exported %d maps. They're saved at your next /reload or logout."):format(n))
end

-------------------------------------------------------------------------------
-- Position strip
-------------------------------------------------------------------------------

local strip = CreateFrame("Frame", "LiveMapStrip", nil)
strip:SetFrameStrata("TOOLTIP")
strip:SetFrameLevel(10000)
strip:SetPoint("TOPLEFT", nil, "TOPLEFT", 0, 0)
local blocks = {}
for i = 1, NBLOCKS do
    local t = strip:CreateTexture(nil, "OVERLAY")
    t:SetColorTexture(0, 0, 0, 1)
    blocks[i] = t
end

-- size the strip so 1 unit = 1 screen pixel, whatever the UI scale
local function layout()
    local _, h = GetPhysicalScreenSize()
    if not h or h <= 0 then return end
    strip:SetScale(768 / h)
    strip:SetSize(BLOCK * NBLOCKS, BLOCK)
    for i, t in ipairs(blocks) do
        t:ClearAllPoints()
        t:SetPoint("TOPLEFT", strip, "TOPLEFT", (i - 1) * BLOCK, 0)
        t:SetSize(BLOCK, BLOCK)
        if t.SetSnapToPixelGrid then t:SetSnapToPixelGrid(false); t:SetTexelSnappingBias(0) end
    end
end

local function setBlock(i, r, g, b)
    blocks[i]:SetColorTexture(r / 255, g / 255, b / 255, 1)
end

local function playerPos(mapID)
    local pos = mapID and mapID > 0 and C_Map.GetPlayerMapPosition(mapID, "player")
    if pos then
        local px, py = pos:GetXY()
        if px and py and px >= 0 and px <= 1 and py >= 0 and py <= 1 then
            return math.floor(px * 65534 + 0.5), math.floor(py * 65534 + 0.5)
        end
    end
    return 65535, 65535
end

local seq = 0
local function update()
    local mapID = C_Map.GetBestMapForUnit("player") or 0
    local x, y = playerPos(mapID)
    local facing = 0
    if GetPlayerFacing then
        local f = GetPlayerFacing()
        if f then facing = math.floor(f / (2 * math.pi) * 256) % 256 end
    end
    -- the continent this map belongs to
    local cont, m = 0, mapID
    while m and m > 0 do
        local info = C_Map.GetMapInfo(m)
        if not info then break end
        if info.mapType == Enum.UIMapType.Continent then cont = m; break end
        m = info.parentMapID
    end
    local cx, cy = playerPos(cont)
    seq = (seq + 1) % 256
    local bytes = { math.floor(mapID / 256) % 256, mapID % 256, facing,
                    math.floor(x / 256), x % 256, math.floor(y / 256), y % 256, seq,
                    math.floor(cont / 256) % 256, cont % 256,
                    math.floor(cx / 256), cx % 256, math.floor(cy / 256), cy % 256 }
    local sum = 0
    for _, v in ipairs(bytes) do sum = sum + v end
    bytes[15] = sum % 256
    -- 120 bits -> 60 two-bit symbols, 3 per block
    local syms = {}
    for _, v in ipairs(bytes) do
        for shift = 6, 0, -2 do syms[#syms + 1] = math.floor(v / 2 ^ shift) % 4 end
    end
    setBlock(1, 255, 0, 255)
    setBlock(2, 85, 255, 170)
    for i = 0, 19 do
        setBlock(3 + i, LEVELS[syms[i * 3 + 1] + 1], LEVELS[syms[i * 3 + 2] + 1], LEVELS[syms[i * 3 + 3] + 1])
    end
end

local elapsed = 0
strip:SetScript("OnUpdate", function(_, dt)
    elapsed = elapsed + dt
    if elapsed >= 1 / HZ then elapsed = 0; update() end
end)

-------------------------------------------------------------------------------
-- Events & commands
-------------------------------------------------------------------------------

local ev = CreateFrame("Frame")
ev:RegisterEvent("PLAYER_LOGIN")
ev:RegisterEvent("DISPLAY_SIZE_CHANGED")
ev:RegisterEvent("UI_SCALE_CHANGED")
ev:SetScript("OnEvent", function(_, e)
    layout()
    if e == "PLAYER_LOGIN" then
        if LiveMapDB.hidden then strip:Hide() end
        if not LiveMapExport or LiveMapExport.version ~= EXPORT_VERSION then
            C_Timer.After(5, exportMaps)
        end
    end
end)

SLASH_LIVEMAP1 = "/livemap"
SlashCmdList.LIVEMAP = function(arg)
    arg = (arg or ""):lower()
    if arg == "export" then
        exportMaps()
    elseif arg == "off" then
        LiveMapDB.hidden = true; strip:Hide(); msg("position strip off (the LiveMap window stops updating).")
    elseif arg == "on" then
        LiveMapDB.hidden = false; strip:Show(); msg("position strip on.")
    else
        msg("/livemap on | off - show/hide the corner strip   /livemap export - re-export the map list")
    end
end
