# Portion of screen to be captured (This forms a square/rectangle around the center of screen)
screenShotHeight = 320
screenShotWidth = 320

# Use "left" or "right" for the mask side depending on where the interfering object is, useful for 3rd player models or large guns
useMask = False
maskSide = "left"
maskWidth = 80
maskHeight = 200

# Autoaim mouse movement amplifier
aaMovementAmp = 0.6

# Person Class Confidence
confidence = 0.3

# What key to press to quit and shutdown the autoaim
aaQuitKey = "Q"

# If you want to main slightly upwards towards the head
headshot_mode = True

# Aim assist trigger key
# Default is right mouse button (0x02)
# Common options:
#   0x02 = Right mouse button
#   0x01 = Left mouse button
#   0x05 = X1 mouse button (side button)
#   0x06 = X2 mouse button (side button)
#   0x10 = Shift key
#   0x11 = Ctrl key
aaActivateKey = 0x2

# Secondary aim activation key (0=disabled)
# Common: 0x05=X1 side button, 0x06=X2 side button
aaSecondaryKey = 0x10

# Aim target: "head", "body", or "nearest"
# "head" - aims at the top of the bounding box (head area)
# "body" - aims at the center mass of the bounding box
# "nearest" - aims at the nearest point of the bounding box to the crosshair
aaTargetPart = "head"

# Team filter for enemy identification (requires multi-class model like cs2_320)
# "all" = aim at all targets, "ct" = I am CT (aim at T), "t" = I am T (aim at CT)
aaTeamFilter = "all"

# Aim mode: "aimbot" = full auto-aim (takes over mouse), "assist" = aim assist (additive pull, you keep mouse control)
aaAimMode = "aimbot"

# Aim smoothing factor (1.0 = instant snap, higher = smoother/slower)
# Recommended: 2.0 ~ 5.0 for natural movement
aaSmoothFactor = 1.4

# Aim FOV (field of view) - only aim at targets within this pixel radius from crosshair
# Set to 0 to disable FOV limit (aim at any target on screen)
aaFOV = 137

# Crosshair Y offset (pixels) to align AI crosshair with game crosshair
# Negative = aim higher, Positive = aim lower
crosshairYOffset = -8

# Screen capture FPS (30-500)
captureFPS = 292

# Recoil compensation weapon (选择武器名称, "关闭 (Off)" = disabled)
recoilWeapon = "M4A4"

# Recoil compensation strength multiplier (1.0 = standard, adjust for sensitivity)
recoilStrength = 1.0

# Recoil smoothness (1=instant/robotic, 3~5=natural hand feel, 8=very smooth)
recoilSmooth = 8

# Recoil trigger key (only apply recoil while this key is held)
# Default: 0x01 = Left mouse button (shooting key)
# Set to match your fire key to avoid recoil during grenades etc.
recoilKey = 1

# Toggle hotkeys (press once to enable, press again to disable)
# F5=0x74, F6=0x75, F7=0x76, F8=0x77, etc.
aimToggleKey = 116
recoilToggleKey = 36
triggerToggleKey = 123

# Rigid recoil mode (FullExternal-style dedicated thread)
# Weapon for rigid recoil ("关闭 (Off)" = disabled)
rigidWeapon = "AK-47"
# CS2 in-game sensitivity (MUST match your game setting for accurate recoil control)
cs2Sensitivity = 1.08
# Smoothness: steps=sub-moves per bullet (1=instant, 2~3=moderate, 5+=smooth)
rigidSteps = 2
# Delay between sub-steps in ms (recommended: 1step=100, 2steps=25, 5steps=4)
rigidDelay1 = 100
# Extra delay after all sub-steps in ms (usually 0)
rigidDelay2 = 8

# Anti-flash (自动背闪) settings
# Delay in seconds to stay turned away (0.2~3.0, default 0.5)
antiflashDelay = 0.5
# Minimum confidence to trigger anti-flash (0.1~1.0, default 0.5)
antiflashConf = 0.5

# Color detection mode (找色模式) — use HSV color instead of AI model
colorPreset = "青色 (Cyan)"
colorHLow = 80
colorSLow = 100
colorVLow = 100
colorHHigh = 100
colorSHigh = 255
colorVHigh = 255
colorSmooth = 0.05
colorMinArea = 5

# Triggerbot settings
# Delay in ms before firing when crosshair is on target (0=instant, 50~150=natural)
triggerDelay = 0

# Displays the Corrections per second in the terminal
cpsDisplay = True

# Set to True if you want to get the visuals
visuals = True

# Smarter selection of people
centerOfScreen = True

# Selected detection model (ONNX filename)
selectedModel = "models\GO_YOLOV11n_敌我_256_头身.onnx"

# ONNX ONLY - Choose 1 of the 3 below
# 1 - CPU
# 2 - AMD
# 3 - NVIDIA
onnxChoice = 1

# ============ Mouse Movement Method ============
# "win32" - Default win32api.mouse_event (may be detected by anti-cheat)
# "ddxoft" - ddxoft virtual input driver (requires ddxoft.dll + run as admin)
mouseMovementMethod = "win32"

# ============ Dead Zone ============
# Minimum pixel distance before mouse moves. Prevents jitter when on target.
# Set to 0 to disable. Recommended: 3~8
aaDeadZone = 5

# ============ Sticky Aim (Anti Target Switching) ============
# When enabled, locks onto the closest target and won't switch to others
# until the current target disappears for several consecutive frames.
stickyAimEnabled = True

# How many consecutive frames the current target must be missing before switching
# Higher = more sticky (won't switch easily), Lower = faster switching
# Recommended: 5~15
stickyAimFrames = 8

# Maximum pixel distance to consider a detection as the "same" target between frames
# Larger = more forgiving for fast-moving targets, Smaller = more precise tracking
# Recommended: 50~150
stickyAimTrackRadius = 100