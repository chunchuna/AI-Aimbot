# Portion of screen to be captured (This forms a square/rectangle around the center of screen)
screenShotHeight = 320
screenShotWidth = 320

# Use "left" or "right" for the mask side depending on where the interfering object is, useful for 3rd player models or large guns
useMask = False
maskSide = "left"
maskWidth = 80
maskHeight = 200

# Autoaim mouse movement amplifier
aaMovementAmp = 2.0

# Person Class Confidence
confidence = 0.35

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

# Aim target: "head", "body", or "nearest"
# "head" - aims at the top of the bounding box (head area)
# "body" - aims at the center mass of the bounding box
# "nearest" - aims at the nearest point of the bounding box to the crosshair
aaTargetPart = "head"

# Aim smoothing factor (1.0 = instant snap, higher = smoother/slower)
# Recommended: 2.0 ~ 5.0 for natural movement
aaSmoothFactor = 2.6

# Aim FOV (field of view) - only aim at targets within this pixel radius from crosshair
# Set to 0 to disable FOV limit (aim at any target on screen)
aaFOV = 225

# Crosshair Y offset (pixels) to align AI crosshair with game crosshair
# Negative = aim higher, Positive = aim lower
crosshairYOffset = -10

# Screen capture FPS (30-500)
captureFPS = 266

# Recoil compensation weapon (选择武器名称, "关闭 (Off)" = disabled)
recoilWeapon = "关闭 (Off)"

# Recoil compensation strength multiplier (1.0 = standard, adjust for sensitivity)
recoilStrength = 8.5

# Displays the Corrections per second in the terminal
cpsDisplay = True

# Set to True if you want to get the visuals
visuals = True

# Smarter selection of people
centerOfScreen = True

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