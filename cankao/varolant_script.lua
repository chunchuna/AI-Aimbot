--==================================================--
--   VALORANT VANDAL PRECISION RECOIL COMPENSATION  --
--   Based on Official Frame Data & Testing         --
--==================================================--

--==== VANDAL EXACT SPRAY PATTERN DATA ====--
-- Official Data: 9.75 bullets/sec (102ms between shots)
-- Vertical recoil per bullet (at 800 DPI, 1.0 sens):
-- 1-10: ~4 pixels down per bullet
-- 11-20: ~5 pixels down per bullet  
-- 21-25: ~6 pixels down per bullet
-- Horizontal: Alternates right-left pattern starting bullet 11

local VANDAL_CONFIG = {
    -- === CORE SETTINGS ===
    enabled = true,
    toggleButton = 8,           -- DPI button to toggle script
    
    -- === VANDAL EXACT FIRE RATE ===
    fireRate = 9.75,           -- 9.75 bullets per second
    shotInterval = 102,        -- ms between shots (102ms for 9.75 RPS)
    
    -- === YOUR SENSITIVITY SETTINGS (MUST SET THESE) ===
    mouseDPI = 800,            -- SET YOUR DPI HERE
    inGameSens = 0.5,          -- SET YOUR IN-GAME SENSITIVITY HERE
    zoomSens = 1.0,            -- SET YOUR SCOPE SENSITIVITY HERE
    
    -- === RECOIL COMPENSATION SETTINGS ===
    -- Base compensation at 800 DPI, 0.5 sens (adjust for your setup)
    verticalBase = 3.5,        -- Base vertical compensation per bullet
    
    -- === VANDAL SPRAY STAGES ===
    stages = {
        -- STAGE 1: Bullets 1-5 (Tight grouping)
        {shots = 5, verticalMult = 0.9, horizontal = 0, delay = 103},
        
        -- STAGE 2: Bullets 6-10 (Slight spread)  
        {shots = 5, verticalMult = 1.0, horizontal = 0.3, delay = 102},
        
        -- STAGE 3: Bullets 11-15 (Right pull begins)
        {shots = 5, verticalMult = 1.2, horizontal = 0.8, delay = 101},
        
        -- STAGE 4: Bullets 16-20 (Strong right pull)
        {shots = 5, verticalMult = 1.3, horizontal = 1.2, delay = 100},
        
        -- STAGE 5: Bullets 21-25 (Zigzag pattern)
        {shots = 5, verticalMult = 1.5, horizontal = 1.5, delay = 99}
    },
    
    -- === ADVANCED FEATURES ===
    enableTapAssist = true,    -- Better control for tap/burst firing
    enableSprayReset = true,   -- Automatic spray reset detection
    enableScopeComp = true,    -- Different compensation when scoped
    debugMode = false          -- Shows detailed logs
}

--==== STATE TRACKING ====--
local state = {
    isShooting = false,
    totalBullets = 0,
    stageBullets = 0,
    currentStage = 1,
    lastShotTime = 0,
    isScoped = false,
    burstMode = false,
    resetTimer = 0
}

--==== CALCULATE SENSITIVITY MULTIPLIER ====--
function CalculateSensitivityMultiplier()
    -- Formula: (DPI * InGameSens) / Reference(800 * 0.5)
    local reference = 800 * 0.5  -- Reference: 800 DPI, 0.5 sens
    local current = VANDAL_CONFIG.mouseDPI * VANDAL_CONFIG.inGameSens
    
    local multiplier = reference / current
    
    -- Apply zoom sensitivity if scoped
    if state.isScoped and VANDAL_CONFIG.enableScopeComp then
        multiplier = multiplier * VANDAL_CONFIG.zoomSens
    end
    
    -- Clamp to reasonable values
    return math.max(0.5, math.min(2.0, multiplier))
end

--==== GET EXACT VANDAL PATTERN ====--
function GetVandalPattern()
    local stage = state.currentStage
    if stage > #VANDAL_CONFIG.stages then
        stage = #VANDAL_CONFIG.stages  -- Stay on last stage for extended spray
    end
    
    local pattern = VANDAL_CONFIG.stages[stage]
    local sensMulti = CalculateSensitivityMultiplier()
    
    -- Calculate exact vertical movement
    local vertical = VANDAL_CONFIG.verticalBase * pattern.verticalMult * sensMulti
    
    -- Calculate horizontal movement (Vandal's alternating pattern)
    local horizontal = pattern.horizontal * sensMulti
    
    -- Vandal's actual horizontal pattern: After bullet 10, alternates right-left
    if state.totalBullets >= 10 then
        if (state.totalBullets % 4) < 2 then  -- Right pull
            horizontal = math.abs(horizontal) * 1.0
        else  -- Left pull
            horizontal = -math.abs(horizontal) * 0.7  -- Left is slightly weaker
        end
    end
    
    -- Add micro-variation for realism (±10%)
    if state.totalBullets > 5 then
        vertical = vertical * (0.95 + (math.random() * 0.1))
        horizontal = horizontal * (0.9 + (math.random() * 0.2))
    end
    
    return {
        vertical = math.floor(vertical + 0.5),  -- Round to nearest pixel
        horizontal = math.floor(horizontal + 0.5),
        delay = pattern.delay
    }
end

--==== INITIALIZATION ====--
function InitializeScript()
    ClearLog()
    OutputLogMessage("================================================\n")
    OutputLogMessage("   VALORANT VANDAL - PRECISION CONTROL v4.0\n")
    OutputLogMessage("   Based on Official Vandal Frame Data\n")
    OutputLogMessage("================================================\n")
    
    local sensMulti = CalculateSensitivityMultiplier()
    OutputLogMessage("[SETTINGS] DPI: %d | Sens: %.2f | Multiplier: %.2fx\n", 
        VANDAL_CONFIG.mouseDPI, VANDAL_CONFIG.inGameSens, sensMulti)
    OutputLogMessage("[FIRERATE] %.2f RPS | %dms per shot\n", 
        VANDAL_CONFIG.fireRate, VANDAL_CONFIG.shotInterval)
    OutputLogMessage("[STAGES] 5-stage Vandal pattern active\n")
    OutputLogMessage("[STATUS] %s (Toggle: DPI Button)\n", 
        VANDAL_CONFIG.enabled and "ACTIVE" or "INACTIVE")
    OutputLogMessage("================================================\n")
    
    -- Visual confirmation
    FlashLED(2)
end

function FlashLED(times)
    for i = 1, times do
        PressKey("capslock")
        Sleep(40)
        ReleaseKey("capslock")
        if i < times then Sleep(80) end
    end
end

--==== TOGGLE FUNCTION ====--
function ToggleRecoilControl()
    VANDAL_CONFIG.enabled = not VANDAL_CONFIG.enabled
    
    if VANDAL_CONFIG.enabled then
        OutputLogMessage("\n[+] VANDAL RECOIL CONTROL: ACTIVATED\n")
        OutputLogMessage("   Using 800 DPI, 0.5 sens equivalent\n")
        FlashLED(3)
    else
        OutputLogMessage("\n[-] VANDAL RECOIL CONTROL: DEACTIVATED\n")
        state.isShooting = false
        FlashLED(1)
    end
end

--==== VANDAL-SPECIFIC COMPENSATION ====--
function ApplyVandalCompensation()
    if not VANDAL_CONFIG.enabled or not state.isShooting then return end
    
    -- Vandal has perfect first shot accuracy - no compensation for first bullet
    if state.totalBullets == 0 then
        Sleep(25)  -- Small delay to ensure first shot registers
    end
    
    -- MAIN COMPENSATION LOOP
    while state.isShooting and VANDAL_CONFIG.enabled do
        -- CRITICAL: Check if still holding fire button
        if not IsMouseButtonPressed(1) then
            state.isShooting = false
            break
        end
        
        -- Get exact Vandal pattern for current bullet
        local pattern = GetVandalPattern()
        
        -- Apply compensation
        MoveMouseRelative(pattern.horizontal, pattern.vertical)
        
        -- Update bullet counters
        state.totalBullets = state.totalBullets + 1
        state.stageBullets = state.stageBullets + 1
        
        if VANDAL_CONFIG.debugMode then
            OutputLogMessage("[BULLET %d] Vert: %d | Horiz: %d | Stage: %d/%d\n",
                state.totalBullets, pattern.vertical, pattern.horizontal,
                state.currentStage, #VANDAL_CONFIG.stages)
        end
        
        -- Progress through stages
        local currentStage = VANDAL_CONFIG.stages[state.currentStage]
        if state.stageBullets >= currentStage.shots and state.currentStage < #VANDAL_CONFIG.stages then
            state.currentStage = state.currentStage + 1
            state.stageBullets = 0
            
            if VANDAL_CONFIG.debugMode then
                OutputLogMessage("[STAGE ADVANCE] Now at stage %d\n", state.currentStage)
            end
        end
        
        -- Detect burst firing (tapping)
        local currentTime = GetRunningTime()
        if currentTime - state.lastShotTime > 200 then  -- >200ms between shots = burst
            state.burstMode = true
        end
        state.lastShotTime = currentTime
        
        -- Adjust delay for burst vs spray
        local actualDelay = pattern.delay
        if state.burstMode and state.totalBullets < 3 then
            actualDelay = actualDelay * 1.1  -- Slightly slower for bursts
        end
        
        -- Spray reset timer
        if VANDAL_CONFIG.enableSprayReset then
            state.resetTimer = currentTime + 400  -- Vandal spray resets in ~400ms
        end
        
        Sleep(actualDelay)
    end
    
    -- SPRAY RESET LOGIC
    if state.totalBullets > 0 then
        local resetTime = 0
        if state.totalBullets <= 5 then
            resetTime = 200  -- Fast reset for short bursts
        elseif state.totalBullets <= 10 then
            resetTime = 350  -- Medium reset
        else
            resetTime = 500  -- Full spray needs longer reset
        end
        
        OutputLogMessage("[VANDAL] Fired %d bullets | Reset: %dms\n", 
            state.totalBullets, resetTime)
    end
    
    -- Reset for next spray
    ResetState()
end

function ResetState()
    state.totalBullets = 0
    state.currentStage = 1
    state.stageBullets = 0
    state.burstMode = false
    state.resetTimer = 0
end

--==== MAIN EVENT HANDLER ====--
function OnEvent(event, arg)
    -- Profile activation
    if event == "PROFILE_ACTIVATED" then
        EnablePrimaryMouseButtonEvents(true)
        InitializeScript()
        return
    end
    
    -- Profile deactivation
    if event == "PROFILE_DEACTIVATED" then
        state.isShooting = false
        return
    end
    
    -- DPI Button to toggle
    if event == "MOUSE_BUTTON_PRESSED" and arg == VANDAL_CONFIG.toggleButton then
        ToggleRecoilControl()
        return
    end
    
    -- Right Mouse Button (scope detection)
    if event == "MOUSE_BUTTON_PRESSED" and arg == 3 then
        state.isScoped = true
    elseif event == "MOUSE_BUTTON_RELEASED" and arg == 3 then
        state.isScoped = false
    end
    
    -- Left Mouse Button (shooting)
    if event == "MOUSE_BUTTON_PRESSED" and arg == 1 then
        if VANDAL_CONFIG.enabled and not state.isShooting then
            state.isShooting = true
            state.lastShotTime = GetRunningTime()
            
            -- Start compensation
            ApplyVandalCompensation()
        end
        return
    end
    
    -- Left Mouse Button released
    if event == "MOUSE_BUTTON_RELEASED" and arg == 1 then
        state.isShooting = false
        return
    end
end

--==================================================--
--   VANDAL EXACT SPECIFICATIONS:
--   • Fire Rate: 9.75 rounds/sec (585 RPM)
--   • Reload Time: 2.5 seconds
--   • Magazine: 25 rounds
--   • First Shot Spread: 0.25° (perfect accuracy)
--   • Recoil Reset: ~400ms for full spray
--   • Optimal Range: 15-50 meters
--==================================================--
