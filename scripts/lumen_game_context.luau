--[[
    Lumen game-context collector for the carbonx-api-bridge control loop.

    Paste this into Lumen's editor (or auto-execute) and run it. Every few
    seconds it pushes a compact snapshot of the current game to the bridge:
      POST {BRIDGE_URL}/v1/game-context
    { game, players, files }   -- files are decompiled scripts/instances.

    The bridge injects the snapshot into the system prompt of every /v1/chat
    and /v1/stream request, so the chat panel answers questions about the
    game, its players, and its code. See the "Game context (Lumen)" section
    of the bridge README for the payload contract and limits.

    Requires: BRIDGE_URL (default http://127.0.0.1:8787) and TOKEN (an admin
    token shown in the panel, or one you generated with `--add-token`).
]]

local CONFIG = {
    BRIDGE_URL = "http://127.0.0.1:8787",
    TOKEN = "PASTE-YOUR-BRIDGE-TOKEN-HERE",
    PUSH_INTERVAL = 10,
    READY_TIMEOUT = 15,
    MAX_FILES = 25,
    PER_FILE_CHARS = 6000,
    TOTAL_FILE_CHARS = 30000,
    MAX_PLAYERS = 50,
    HEALTH_ON_BOOT = false,
}

local SCRIPT_CLASSES = {
    Script = true,
    LocalScript = true,
    ModuleScript = true,
}

-- ---------------------------------------------------------------------------
-- JSON encoder (pure Luau; no HttpService dependency inside the executor)
-- ---------------------------------------------------------------------------

local function escString(s)
    s = tostring(s)
    local out = {}
    for i = 1, #s do
        local b = string.byte(s, i)
        local c = string.char(b)
        if c == "\\" then
            out[#out + 1] = "\\\\"
        elseif c == '"' then
            out[#out + 1] = '\\"'
        elseif b == 10 then
            out[#out + 1] = "\\n"
        elseif b == 13 then
            out[#out + 1] = "\\r"
        elseif b == 9 then
            out[#out + 1] = "\\t"
        elseif b < 32 then
            out[#out + 1] = string.format("\\u%04x", b)
        else
            out[#out + 1] = c
        end
    end
    return table.concat(out)
end

local function jsonEncode(value, depth)
    depth = depth or 0
    if depth > 24 then return "null" end
    local t = type(value)
    if value == nil then return "null" end
    if t == "string" then return '"' .. escString(value) .. '"' end
    if t == "number" then return tostring(value) end
    if t == "boolean" then return value and "true" or "false" end
    if t ~= "table" then return '"' .. escString(tostring(value)) .. '"' end

    local isArray, maxIndex = true, 0
    for k in pairs(value) do
        if type(k) ~= "number" then
            isArray = false
        elseif k > maxIndex then
            maxIndex = k
        end
    end
    if isArray and maxIndex > 0 then
        local parts = {}
        for i = 1, maxIndex do
            parts[i] = jsonEncode(value[i], depth + 1)
        end
        return "[" .. table.concat(parts, ",") .. "]"
    end
    local parts = {}
    for k, v in pairs(value) do
        if type(k) == "string" then
            parts[#parts + 1] = '"' .. escString(k) .. '":' .. jsonEncode(v, depth + 1)
        end
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

-- ---------------------------------------------------------------------------
-- Small safe helpers (Lumen mirrors the Roblox globals game/workspace/Players)
-- ---------------------------------------------------------------------------

local function safe(fn, ...)
    local ok, result = pcall(fn, ...)
    if ok then return result end
    return nil
end

local function getService(name)
    local envService = safe(function() return _G[name] end)
    if type(envService) == "table" and envService.ClassName then
        return envService
    end
    local fromGame = safe(function() return game:GetService(name) end)
    if fromGame then return fromGame end
    if name == "Workspace" then
        return workspace
    end
    return nil
end

local function getLocalPlayer()
    local players = getService("Players")
    if not players then return nil end
    return safe(function() return players.LocalPlayer end)
end

local function fullPath(inst)
    local full = safe(function() return inst:GetFullName() end)
    return tostring(full or (safe(function() return inst.Name end) or "?"))
end

local function clip(value, limit)
    value = tostring(value or "")
    if #value <= limit then return value end
    return value:sub(1, limit)
end

local function waitForGame()
    local started = os.clock()
    while os.clock() - started < CONFIG.READY_TIMEOUT do
        local players = getService("Players")
        local lp = players and players.LocalPlayer
        local rs = getService("ReplicatedStorage")
        if rs and lp then
            return true
        end
        task.wait(0.25)
    end
    return false
end

-- ---------------------------------------------------------------------------
-- Snapshot collection
-- ---------------------------------------------------------------------------

local function capturePlayers()
    local players = getService("Players")
    if not players then return {} end
    local lp = safe(function() return players.LocalPlayer end)
    local list = safe(function() return players:GetPlayers() end) or {}
    local out = {}
    for i = 1, math.min(#list, CONFIG.MAX_PLAYERS) do
        local p = list[i]
        local char = safe(function() return p.Character end)
        local hum = char and safe(function() return char:FindFirstChildOfClass("Humanoid") end)
        out[i] = {
            name = clip(safe(function() return p.Name end), 120),
            display_name = clip(safe(function() return p.DisplayName end), 120),
            user_id = tostring(safe(function() return p.UserId end) or ""),
            team = clip(safe(function() return p.Team and p.Team.Name end), 120),
            health = hum and math.floor((safe(function() return hum.Health end) or 0) * 10) / 10 or nil,
            is_local = p == lp,
        }
    end
    return out
end

local function collectFiles()
    local lp = getLocalPlayer()
    local roots = {}
    local function add(name, inst)
        if inst and type(inst) == "table" then
            table.insert(roots, inst)
        end
    end
    add("ReplicatedFirst", getService("ReplicatedFirst"))
    add("ReplicatedStorage", getService("ReplicatedStorage"))
    if lp then
        add("PlayerScripts", safe(function() return lp:FindFirstChild("PlayerScripts") end))
        add("PlayerGui", safe(function() return lp:FindFirstChild("PlayerGui") end))
    end
    add("Workspace", getService("Workspace"))

    local out = {}
    local seen = {}
    local budget = CONFIG.TOTAL_FILE_CHARS
    for _, root in ipairs(roots) do
        if #out >= CONFIG.MAX_FILES or budget <= 0 then break end
        local ok, descendants = pcall(function() return root:GetDescendants() end)
        if ok and descendants then
            for _, inst in ipairs(descendants) do
                if #out >= CONFIG.MAX_FILES or budget <= 0 then break end
                if not seen[inst] then
                    seen[inst] = true
                    local okC, className = pcall(function() return inst.ClassName end)
                    if okC and SCRIPT_CLASSES[className] then
                        local source = safe(function() return inst.Source end)
                        if not source or #source == 0 then
                            local okD, decompiled = pcall(decompile, inst)
                            if okD and type(decompiled) == "string" then
                                source = decompiled
                            end
                        end
                        if source and #source > 0 then
                            if #source > CONFIG.PER_FILE_CHARS then
                                source = source:sub(1, CONFIG.PER_FILE_CHARS) .. "\n[truncated]"
                            end
                            if budget <= 0 then break end
                            table.insert(out, {
                                path = fullPath(inst),
                                content = source,
                            })
                            budget = budget - #source
                        end
                    end
                end
            end
        end
    end
    return out
end

local function capturePayload()
    local players = capturePlayers()
    local files = collectFiles()
    if #players == 0 and #files == 0 then
        return nil
    end
    return {
        game = clip(safe(function() return game.Name end), 200),
        players = players,
        files = files,
    }
end

-- ---------------------------------------------------------------------------
-- HTTP push via Lumen's request() (yields; see getlumen.net/docs)
-- ---------------------------------------------------------------------------

local function push(payload)
    local response
    local ok, err = pcall(function()
        response = request({
            Url = CONFIG.BRIDGE_URL .. "/v1/game-context",
            Method = "POST",
            Headers = {
                ["Content-Type"] = "application/json",
                ["Authorization"] = "Bearer " .. CONFIG.TOKEN,
            },
            Body = jsonEncode(payload),
        })
    end)
    if not ok then
        return false, "request raised: " .. tostring(err)
    end
    if not response then
        return false, "no response"
    end
    if response.Error then
        return false, response.Error
    end
    if response.StatusCode ~= 200 then
        return false, response.StatusCode .. " " .. clip(response.Body or "", 300)
    end
    return true, response.Body or ""
end

local function healthCheck()
    local response
    local ok = pcall(function()
        response = request({
            Url = CONFIG.BRIDGE_URL .. "/health",
            Method = "GET",
        })
    end)
    if ok and response and not response.Error then
        print("[game-context] bridge " .. CONFIG.BRIDGE_URL .. " is up (" .. clip(response.Body or "", 160) .. ")")
        return true
    end
    warn("[game-context] cannot reach " .. CONFIG.BRIDGE_URL .. " - is the bridge running? (" .. tostring(response and response.Error or "no response") .. ")")
    return false
end

-- ---------------------------------------------------------------------------
-- Main loop (spawned so a bad push never kills the script)
-- ---------------------------------------------------------------------------

task.spawn(function()
    if CONFIG.HEALTH_ON_BOOT then
        healthCheck()
    end
    if not waitForGame() then
        warn("[game-context] game not ready within " .. CONFIG.READY_TIMEOUT .. "s - pushing anyway")
    end

    local lastPushAt = 0
    while true do
        local tickOk, tickErr = pcall(function()
            local now = os.clock()
            if now - lastPushAt < CONFIG.PUSH_INTERVAL then return end
            local payload = capturePayload()
            if payload then
                local ok, detail = push(payload)
                lastPushAt = now
                local gameName = payload.game or "?"
                if ok then
                    print(string.format("[game-context] pushed %s | players: %d | files: %d",
                        gameName, #payload.players, #payload.files))
                else
                    warn("[game-context] push failed: " .. tostring(detail))
                end
            end
        end)
        if not tickOk then
            warn("[game-context] loop error: " .. tostring(tickErr))
        end
        task.wait(math.max(1, CONFIG.PUSH_INTERVAL))
    end
end)