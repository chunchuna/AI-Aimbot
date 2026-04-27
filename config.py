# Portion of screen to be captured (This forms a square/rectangle around the center of screen)
screenShotHeight = 320
screenShotWidth = 320

# Use "left" or "right" for the mask side depending on where the interfering object is, useful for 3rd player models or large guns
useMask = False
maskSide = "left"
maskWidth = 80
maskHeight = 200

# Autoaim mouse movement amplifier
aaMovementAmp = .4

# Person Class Confidence
confidence = 0.4

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
aaActivateKey = 0x02

# Aim target: "head", "body", or "nearest"
# "head" - aims at the top of the bounding box (head area)
# "body" - aims at the center mass of the bounding box
# "nearest" - aims at the nearest point of the bounding box to the crosshair
aaTargetPart = "head"

# Aim smoothing factor (1.0 = instant snap, higher = smoother/slower)
# Recommended: 2.0 ~ 5.0 for natural movement
aaSmoothFactor = 3.0

# Aim FOV (field of view) - only aim at targets within this pixel radius from crosshair
# Set to 0 to disable FOV limit (aim at any target on screen)
aaFOV = 150

# Displays the Corrections per second in the terminal
cpsDisplay = True

# Set to True if you want to get the visuals
visuals = False

# Smarter selection of people
centerOfScreen = True

# ONNX ONLY - Choose 1 of the 3 below
# 1 - CPU
# 2 - AMD
# 3 - NVIDIA
onnxChoice = 1