import torch
import numpy as np
import cv2
import time
import win32api
import win32con
import pandas as pd
import gc
from utils.general import (cv2, non_max_suppression, xyxy2xywh)

# Could be do with
# from config import *
# But we are writing it out for clarity for new devs
from config import aaMovementAmp, useMask, maskWidth, maskHeight, aaQuitKey, screenShotHeight, confidence, headshot_mode, cpsDisplay, visuals, centerOfScreen, aaActivateKey, aaTargetPart, aaSmoothFactor, aaFOV
from config import mouseMovementMethod, aaDeadZone, stickyAimEnabled, stickyAimFrames, stickyAimTrackRadius
from utils.ddxoft_mouse import ddxoft_instance
import math
import gameSelection

def move_mouse(dx, dy):
    """Move mouse using configured method (ddxoft or win32)."""
    if mouseMovementMethod == "ddxoft" and ddxoft_instance.is_loaded:
        ddxoft_instance.move_relative(dx, dy)
    else:
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, dx, dy, 0, 0)

def main():
    # ===== Initialize ddxoft if configured =====
    if mouseMovementMethod == "ddxoft":
        print("[ddxoft] Attempting to load ddxoft virtual input driver...")
        if ddxoft_instance.load():
            print("[ddxoft] Driver loaded successfully. Using ddxoft for mouse movement.")
        else:
            print("[ddxoft] Failed to load driver. Falling back to win32 mouse_event.")

    # ===== DEBUG: Print loaded config values =====
    print("===== AI Aimbot Config =====")
    print(f"  aaActivateKey      = {hex(aaActivateKey)} (type: {type(aaActivateKey)})")
    print(f"  aaTargetPart       = {aaTargetPart}")
    print(f"  aaSmoothFactor     = {aaSmoothFactor}")
    print(f"  aaMovementAmp      = {aaMovementAmp}")
    print(f"  aaFOV              = {aaFOV}")
    print(f"  confidence         = {confidence}")
    print(f"  centerOfScreen     = {centerOfScreen}")
    print(f"  visuals            = {visuals}")
    print(f"  headshot_mode      = {headshot_mode}")
    print(f"  mouseMovement      = {mouseMovementMethod}")
    print(f"  aaDeadZone         = {aaDeadZone}")
    print(f"  stickyAimEnabled   = {stickyAimEnabled}")
    print(f"  stickyAimFrames    = {stickyAimFrames}")
    print(f"  stickyAimTrackRadius = {stickyAimTrackRadius}")
    print("=============================")

    # External Function for running the game selection menu (gameSelection.py)
    camera, cWidth, cHeight = gameSelection.gameSelection()

    # Used for forcing garbage collection
    count = 0
    sTime = time.time()

    # Loading Yolo5 Small AI Model, for better results use yolov5m or yolov5l
    model = torch.hub.load('ultralytics/yolov5', 'yolov5s',
                           pretrained=True, force_reload=True)
    stride, names, pt = model.stride, model.names, model.pt

    if torch.cuda.is_available():
        model.half()

    # Used for colors drawn on bounding boxes
    COLORS = np.random.uniform(0, 255, size=(1500, 3))

    # Sticky aim state
    sticky_target = None       # (x, y, width, height) of locked target
    sticky_miss_frames = 0     # consecutive frames target not found

    # Main loop Quit if Q is pressed
    last_mid_coord = None
    debugTimer = time.time()
    debugKeyPressed = False
    with torch.no_grad():
        while win32api.GetAsyncKeyState(ord(aaQuitKey)) == 0:

            # Getting Frame
            npImg = np.array(camera.get_latest_frame())

            from config import maskSide # "temporary" workaround for bad syntax
            if useMask:
                maskSide = maskSide.lower()
                if maskSide == "right":
                    npImg[-maskHeight:, -maskWidth:, :] = 0
                elif maskSide == "left":
                    npImg[-maskHeight:, :maskWidth, :] = 0
                else:
                    raise Exception('ERROR: Invalid maskSide! Please use "left" or "right"')

            # Normalizing Data
            im = torch.from_numpy(npImg)
            if im.shape[2] == 4:
                # If the image has an alpha channel, remove it
                im = im[:, :, :3,]

            im = torch.movedim(im, 2, 0)
            if torch.cuda.is_available():
                im = im.half()
                im /= 255
            if len(im.shape) == 3:
                im = im[None]

            # Detecting all the objects
            results = model(im, size=screenShotHeight)

            # Suppressing results that dont meet thresholds
            pred = non_max_suppression(
                results, confidence, confidence, 0, False, max_det=1000)

            # Converting output to usable cords
            targets = []
            for i, det in enumerate(pred):
                s = ""
                gn = torch.tensor(im.shape)[[0, 0, 0, 0]]
                if len(det):
                    for c in det[:, -1].unique():
                        n = (det[:, -1] == c).sum()  # detections per class
                        s += f"{n} {names[int(c)]}, "  # add to string

                    for *xyxy, conf, cls in reversed(det):
                        targets.append((xyxy2xywh(torch.tensor(xyxy).view(
                            1, 4)) / gn).view(-1).tolist() + [float(conf)])  # normalized xywh

            targets = pd.DataFrame(
                targets, columns=['current_mid_x', 'current_mid_y', 'width', "height", "confidence"])

            center_screen = [cWidth, cHeight]

            # DEBUG: Log detection count every second
            keyStateRaw = win32api.GetAsyncKeyState(aaActivateKey)
            keyPressed = bool(keyStateRaw & 0x8000)
            if time.time() - debugTimer > 1:
                print(f"[DEBUG] targets_detected={len(targets)} | key_raw={keyStateRaw} key_pressed={keyPressed} | aaActivateKey={hex(aaActivateKey)}")
                debugTimer = time.time()
                if keyPressed and not debugKeyPressed:
                    print("[DEBUG] >>> Aim key PRESSED (first detection)")
                debugKeyPressed = keyPressed

            # If there are people in the center bounding box
            if len(targets) > 0:
                if (centerOfScreen):
                    # Compute the distance from the center
                    targets["dist_from_center"] = np.sqrt((targets.current_mid_x - center_screen[0])**2 + (targets.current_mid_y - center_screen[1])**2)

                    # Sort the data frame by distance from center
                    targets = targets.sort_values("dist_from_center")

                # Filter targets by FOV if enabled
                targets_before_fov = len(targets)
                if aaFOV > 0 and "dist_from_center" in targets.columns:
                    targets = targets[targets["dist_from_center"] <= aaFOV]

                if targets_before_fov > 0 and len(targets) == 0:
                    if time.time() - debugTimer < 0.1:
                        print(f"[DEBUG] All {targets_before_fov} targets filtered out by FOV ({aaFOV})! Closest was outside range.")

                if len(targets) > 0:
                    # ===== Sticky Aim: select target =====
                    chosen_idx = 0  # default: closest to center (already sorted)
                    goto_visuals = False

                    if stickyAimEnabled and sticky_target is not None:
                        # Try to find the locked target in current detections
                        best_match_idx = -1
                        best_match_dist = float('inf')
                        st_x, st_y, st_w, st_h = sticky_target

                        for idx in range(len(targets)):
                            row = targets.iloc[idx]
                            dx = row.current_mid_x - st_x
                            dy = row.current_mid_y - st_y
                            dist = math.sqrt(dx * dx + dy * dy)

                            # Also check size similarity (within 60% ratio)
                            if st_w > 0 and row.width > 0:
                                size_ratio = min(row.width, st_w) / max(row.width, st_w)
                                if size_ratio < 0.4:
                                    continue

                            if dist < stickyAimTrackRadius and dist < best_match_dist:
                                best_match_dist = dist
                                best_match_idx = idx

                        if best_match_idx >= 0:
                            # Found our locked target - keep tracking it
                            chosen_idx = best_match_idx
                            sticky_miss_frames = 0
                        else:
                            # Locked target not found this frame
                            sticky_miss_frames += 1
                            if sticky_miss_frames >= stickyAimFrames:
                                # Target truly gone - switch to closest
                                chosen_idx = 0
                                sticky_target = None
                                sticky_miss_frames = 0
                            else:
                                # Grace period: skip aiming this frame to avoid snapping
                                goto_visuals = True

                    if not goto_visuals:
                        xMid = targets.iloc[chosen_idx].current_mid_x
                        yMid = targets.iloc[chosen_idx].current_mid_y
                        box_width = targets.iloc[chosen_idx].width
                        box_height = targets.iloc[chosen_idx].height

                        # Update sticky aim target
                        if stickyAimEnabled:
                            sticky_target = (xMid, yMid, box_width, box_height)
                            sticky_miss_frames = 0

                        # Calculate aim point based on target part selection
                        if aaTargetPart == "head":
                            # Aim at the top portion of bounding box (head)
                            headshot_offset = box_height * 0.38
                        elif aaTargetPart == "body":
                            # Aim at center mass
                            headshot_offset = box_height * 0.1
                        elif aaTargetPart == "nearest":
                            # Aim at nearest point on bounding box to crosshair
                            headshot_offset = 0
                        else:
                            # Fallback: use headshot_mode from config
                            if headshot_mode:
                                headshot_offset = box_height * 0.38
                            else:
                                headshot_offset = box_height * 0.2

                        mouseMove = [xMid - cWidth, (yMid - headshot_offset) - cHeight]

                        # Apply smoothing: divide movement by smooth factor for gradual aiming
                        smoothedX = mouseMove[0] * aaMovementAmp / aaSmoothFactor
                        smoothedY = mouseMove[1] * aaMovementAmp / aaSmoothFactor

                        # Moving the mouse only when the aim key is held down
                        # Default: right mouse button (0x02)
                        keyState = win32api.GetAsyncKeyState(aaActivateKey)
                        keyIsDown = bool(keyState & 0x8000)

                        if keyIsDown:
                            moveX = round(smoothedX)
                            moveY = round(smoothedY)

                            # Dead zone: skip movement if offset is too small (prevents jitter)
                            moveMagnitude = math.sqrt(moveX * moveX + moveY * moveY)
                            if moveMagnitude >= aaDeadZone:
                                move_mouse(moveX, moveY)

                        last_mid_coord = [xMid, yMid]
                else:
                    # No targets in FOV
                    if stickyAimEnabled:
                        sticky_miss_frames += 1
                        if sticky_miss_frames >= stickyAimFrames:
                            sticky_target = None
                            sticky_miss_frames = 0
                    last_mid_coord = None

            else:
                # No detections at all
                if stickyAimEnabled:
                    sticky_miss_frames += 1
                    if sticky_miss_frames >= stickyAimFrames:
                        sticky_target = None
                        sticky_miss_frames = 0
                last_mid_coord = None

            # See what the bot sees
            if visuals:
                # Loops over every item identified and draws a bounding box
                for i in range(0, len(targets)):
                    halfW = round(targets["width"][i] / 2)
                    halfH = round(targets["height"][i] / 2)
                    midX = targets['current_mid_x'][i]
                    midY = targets['current_mid_y'][i]
                    (startX, startY, endX, endY) = int(
                        midX + halfW), int(midY + halfH), int(midX - halfW), int(midY - halfH)

                    idx = 0

                    # draw the bounding box and label on the frame
                    label = "{}: {:.2f}%".format(
                        "Human", targets["confidence"][i] * 100)
                    cv2.rectangle(npImg, (startX, startY), (endX, endY),
                                  COLORS[idx], 2)
                    y = startY - 15 if startY - 15 > 15 else startY + 15
                    cv2.putText(npImg, label, (startX, y),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[idx], 2)

            # Forced garbage cleanup every second
            count += 1
            if (time.time() - sTime) > 1:
                if cpsDisplay:
                    print("CPS: {}".format(count))
                count = 0
                sTime = time.time()

                # Uncomment if you keep running into memory issues
                # gc.collect(generation=0)

            # See visually what the Aimbot sees
            if visuals:
                cv2.imshow('Live Feed', npImg)
                if (cv2.waitKey(1) & 0xFF) == ord('q'):
                    exit()
    camera.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exception(e)
        print("ERROR: " + str(e))
        print("Ask @Wonder for help in our Discord in the #ai-aimbot channel ONLY: https://discord.gg/rootkitorg")