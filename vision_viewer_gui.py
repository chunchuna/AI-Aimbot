import json
import os
import random
import re
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

import cv2
import numpy as np
import pygetwindow
import win32process

try:
    import bettercam
except ImportError:
    bettercam = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    import torch
    from utils.general import non_max_suppression
except ImportError:
    torch = None
    non_max_suppression = None

try:
    import win32api
    import win32con
except ImportError:
    win32api = None
    win32con = None

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor DPI Aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()     # fallback: System DPI Aware
    except Exception:
        pass

import winsound

try:
    import win32com.client as win32com_client
    import pythoncom
except ImportError:
    win32com_client = None
    pythoncom = None

import ctypes.wintypes

# ---- PID Controller for aim tracking ----
class AimPID:
    """Dual-axis PID controller for mouse aim tracking.

    Why PID instead of pure P-controller:
    - P only: moveX = error * Kp → always has steady-state error on moving targets
    - P+I: integral term accumulates error over time → eliminates steady-state error
           → can lock onto any speed target with zero offset
    - P+I+D: derivative term dampens overshoot when error changes rapidly
           → smooth arrival, no oscillation

    Anti-windup: integral is clamped to prevent runaway accumulation
    when the target is far away or briefly lost.
    """

    def __init__(self):
        self._integral_x = 0.0
        self._integral_y = 0.0
        self._prev_error_x = 0.0
        self._prev_error_y = 0.0
        self._prev_t = 0.0
        self._initialized = False
        self._frame_count = 0       # for soft-start ramp

    def reset(self):
        """Reset PID state (call on target switch or aim key release)."""
        self._integral_x = 0.0
        self._integral_y = 0.0
        self._prev_error_x = 0.0
        self._prev_error_y = 0.0
        self._prev_t = 0.0
        self._initialized = False
        self._frame_count = 0

    def compute(self, error_x, error_y, kp, ki, kd):
        """Compute PID output for current error.

        Args:
            error_x, error_y: pixel offset from crosshair to target
            kp: proportional gain (like old amp/smooth)
            ki: integral gain (0 = pure P, 0.01~0.5 typical)
            kd: derivative gain (0 = no damping, 0.01~0.3 typical)

        Returns:
            (move_x, move_y): mouse movement in pixels
        """
        now = time.perf_counter()

        if not self._initialized:
            self._prev_error_x = error_x
            self._prev_error_y = error_y
            self._prev_t = now
            self._initialized = True
            self._frame_count = 1
            # First frame: soft-start (20% of P-only) to avoid snap
            return error_x * kp * 0.2, error_y * kp * 0.2

        self._frame_count += 1

        dt = now - self._prev_t
        if dt <= 0:
            dt = 1e-4
        self._prev_t = now

        RAMP_FRAMES = 10  # soft-start duration

        # --- Integral (accumulated error) ---
        # Only accumulate when: (a) past soft-start, AND (b) error is small.
        # Large errors (> 30px) are handled by P alone — accumulating integral
        # during the initial approach would cause overshoot / fling.
        # Integral's job is to fix persistent SMALL tracking offsets on moving targets.
        err_mag = (error_x**2 + error_y**2) ** 0.5
        if self._frame_count > RAMP_FRAMES and err_mag < 30.0:
            self._integral_x += error_x * dt
            self._integral_y += error_y * dt

        # Anti-windup clamp
        INTEGRAL_MAX = 50.0
        self._integral_x = max(-INTEGRAL_MAX, min(INTEGRAL_MAX, self._integral_x))
        self._integral_y = max(-INTEGRAL_MAX, min(INTEGRAL_MAX, self._integral_y))

        # Direction reversal with large error: overshoot, halve integral
        if error_x * self._prev_error_x < 0 and abs(error_x) > 10.0:
            self._integral_x *= 0.5
        if error_y * self._prev_error_y < 0 and abs(error_y) > 10.0:
            self._integral_y *= 0.5

        # Decay integral when very close to target (< 5px)
        if err_mag < 5.0:
            self._integral_x *= 0.7
            self._integral_y *= 0.7

        # --- Derivative (frame-based, NOT divided by dt) ---
        deriv_x = error_x - self._prev_error_x
        deriv_y = error_y - self._prev_error_y
        self._prev_error_x = error_x
        self._prev_error_y = error_y

        # --- PID output ---
        move_x = kp * error_x + ki * self._integral_x + kd * deriv_x
        move_y = kp * error_y + ki * self._integral_y + kd * deriv_y

        # Soft-start ramp: gradually increase output over first N frames
        # P-only during ramp (integral blocked above), prevents fling on lock-on
        if self._frame_count <= RAMP_FRAMES:
            ramp = self._frame_count / float(RAMP_FRAMES)
            move_x *= ramp
            move_y *= ramp

        return move_x, move_y


# ---- SendInput struct definitions for rigid recoil (module-level for performance) ----
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", _MOUSEINPUT)]

_MOUSEEVENTF_MOVE = 0x0001

# ---- Transparent fullscreen overlay using Win32 API + UpdateLayeredWindow ----
class OverlayWindow:
    """A transparent, click-through, always-on-top fullscreen overlay.

    Uses UpdateLayeredWindow with per-pixel alpha (32-bit BGRA DIB) so the
    overlay is visible even over borderless-fullscreen games.  DWM composites
    the layered window on top of everything when HWND_TOPMOST is set.
    """

    # Win32 constants
    WS_EX_LAYERED     = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOPMOST     = 0x00000008
    WS_EX_TOOLWINDOW  = 0x00000080
    WS_EX_NOACTIVATE  = 0x08000000
    WS_POPUP           = 0x80000000
    HWND_TOPMOST       = -1
    SWP_NOMOVE         = 0x0002
    SWP_NOSIZE         = 0x0001
    SWP_NOACTIVATE     = 0x0010
    SWP_SHOWWINDOW     = 0x0040
    SW_SHOWNOACTIVATE  = 4
    AC_SRC_OVER        = 0x00
    AC_SRC_ALPHA       = 0x01
    ULW_ALPHA          = 0x02
    DIB_RGB_COLORS     = 0
    BI_RGB             = 0

    _CLASS_REGISTERED = False
    _CLASS_NAME = "AimOverlayWnd"

    def __init__(self):
        self._hwnd = None
        self._cached_dib = None
        self._cached_mem_dc = None
        self._cached_old_bmp = None
        self._cached_arr = None
        user32 = ctypes.windll.user32
        # Use virtual screen metrics (multi-monitor aware)
        self._left = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        self._top = user32.GetSystemMetrics(77)    # SM_YVIRTUALSCREEN
        self._width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        self._height = user32.GetSystemMetrics(79) # SM_CYVIRTUALSCREEN
        if self._width <= 0 or self._height <= 0:
            # Fallback to primary monitor
            self._left = 0
            self._top = 0
            self._width = user32.GetSystemMetrics(0)
            self._height = user32.GetSystemMetrics(1)
        self._topmost_counter = 0
        self._create_window()
        self._create_dib_cache()

    # ---- window creation ----
    def _create_window(self):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long,
                                     ctypes.wintypes.HWND, ctypes.c_uint,
                                     ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        user32.DefWindowProcW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint,
                                          ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_long
        self._wndproc_ref = WNDPROC(lambda hwnd, msg, wp, lp:
                                    user32.DefWindowProcW(hwnd, msg, wp, lp))

        if not OverlayWindow._CLASS_REGISTERED:
            class WNDCLASSEXW(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                            ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                            ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                            ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                            ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_wchar_p),
                            ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hInstance
            wc.lpszClassName = self._CLASS_NAME
            wc.hbrBackground = 0
            user32.RegisterClassExW(ctypes.byref(wc))
            OverlayWindow._CLASS_REGISTERED = True

        ex_style = (self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT |
                    self.WS_EX_TOPMOST | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE)
        self._hwnd = user32.CreateWindowExW(
            ex_style, self._CLASS_NAME, "AimOverlay",
            self.WS_POPUP,
            self._left, self._top, self._width, self._height,
            None, None, hInstance, None)

        if not self._hwnd:
            print("[OVERLAY] CreateWindowExW failed!")
            return

        user32.ShowWindow(self._hwnd, self.SW_SHOWNOACTIVATE)
        # Force topmost
        user32.SetWindowPos(self._hwnd, self.HWND_TOPMOST,
                            0, 0, 0, 0,
                            self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE)
        print(f"[OVERLAY] Created hwnd={self._hwnd} size={self._width}x{self._height} offset=({self._left},{self._top})")

    # ---- pre-allocate DIB for reuse across frames ----
    def _create_dib_cache(self):
        """Create a persistent 32-bit BGRA DIB section for UpdateLayeredWindow."""
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        screen_dc = user32.GetDC(0)
        self._screen_dc = screen_dc
        self._cached_mem_dc = gdi32.CreateCompatibleDC(screen_dc)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint), ("biWidth", ctypes.c_int),
                        ("biHeight", ctypes.c_int), ("biPlanes", ctypes.c_ushort),
                        ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint),
                        ("biSizeImage", ctypes.c_uint), ("biXPelsPerMeter", ctypes.c_int),
                        ("biYPelsPerMeter", ctypes.c_int), ("biClrUsed", ctypes.c_uint),
                        ("biClrImportant", ctypes.c_uint)]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = self._width
        bmi.biHeight = -self._height  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = self.BI_RGB

        ppvBits = ctypes.c_void_p()
        self._cached_dib = gdi32.CreateDIBSection(
            self._cached_mem_dc, ctypes.byref(bmi), self.DIB_RGB_COLORS,
            ctypes.byref(ppvBits), None, 0)

        if not self._cached_dib or not ppvBits:
            print("[OVERLAY] Failed to create DIB section!")
            self._cached_arr = None
            return

        self._cached_old_bmp = gdi32.SelectObject(self._cached_mem_dc, self._cached_dib)
        buf = (ctypes.c_uint8 * (self._width * self._height * 4)).from_address(ppvBits.value)
        self._cached_arr = np.ctypeslib.as_array(buf).reshape((self._height, self._width, 4))

        # Pre-build UpdateLayeredWindow structs (reused every frame)
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]
        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                        ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]

        self._pt_src = POINT(0, 0)
        self._pt_dst = POINT(self._left, self._top)
        self._sz = SIZE(self._width, self._height)
        self._blend = BLENDFUNCTION(self.AC_SRC_OVER, 0, 255, self.AC_SRC_ALPHA)
        print(f"[OVERLAY] DIB cache created: {self._width}x{self._height} BGRA")

    # ---- per-pixel-alpha update via UpdateLayeredWindow ----
    # Color name → BGRA tuple mapping for dot colors
    DOT_COLORS = {
        "red": (0, 0, 255, 255), "green": (0, 255, 0, 255), "cyan": (255, 255, 0, 255),
        "white": (255, 255, 255, 255), "yellow": (0, 255, 255, 255), "magenta": (255, 0, 255, 255),
    }

    def draw(self, detections, capture_region=None, box_thickness=2,
             box_style="full", corner_len=15, show_dot=False, dot_size=4,
             dot_style="circle", dot_color="red", hide_label=False):
        """Draw detection boxes using UpdateLayeredWindow (per-pixel alpha).
        detections: list of dicts with 'xyxy', optional '_color', '_label', '_aim_x', '_aim_y'
        capture_region: (left, top, right, bottom) of the captured screen region
        box_thickness: line width for bounding boxes (1-6)
        box_style: 'full' = complete rectangle, 'corners' = only draw corner brackets
        corner_len: length of corner lines when box_style='corners'
        show_dot: whether to draw aim point dot
        dot_size: radius of the aim dot
        dot_style: 'circle', 'cross', or 'diamond'
        dot_color: color name for the aim dot
        """
        if not self._hwnd or self._cached_arr is None:
            return
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        arr = self._cached_arr
        mem_dc = self._cached_mem_dc
        h, w = self._height, self._width

        # Re-assert TOPMOST periodically (every ~30 draw calls ≈ once per second)
        self._topmost_counter += 1
        if self._topmost_counter >= 30:
            self._topmost_counter = 0
            user32.SetWindowPos(self._hwnd, self.HWND_TOPMOST,
                                0, 0, 0, 0,
                                self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE)

        # Clear to fully transparent (BGRA 0,0,0,0)
        arr[:] = 0

        # Capture region offset (detection coords are relative to capture region)
        ox, oy = 0, 0
        if capture_region:
            ox, oy = capture_region[0] - self._left, capture_region[1] - self._top

        t = max(1, int(box_thickness))
        dot_bgra = self.DOT_COLORS.get(dot_color, (0, 0, 255, 255))

        for d in detections:
            x1, y1, x2, y2 = d["xyxy"]
            sx1 = max(0, int(x1 + ox))
            sy1 = max(0, int(y1 + oy))
            sx2 = min(w - 1, int(x2 + ox))
            sy2 = min(h - 1, int(y2 + oy))
            if sx1 >= sx2 or sy1 >= sy2:
                continue
            color_bgr = d.get("_color", (0, 0, 255))  # BGR tuple
            b, g, r = int(color_bgr[0]), int(color_bgr[1]), int(color_bgr[2])
            a = 255
            pixel = [b, g, r, a]

            if box_style == "corners":
                cl = min(corner_len, (sx2 - sx1) // 2, (sy2 - sy1) // 2)
                # Top-left corner
                arr[sy1:min(sy1+t, sy2), sx1:sx1+cl] = pixel
                arr[sy1:sy1+cl, sx1:min(sx1+t, sx2)] = pixel
                # Top-right corner
                arr[sy1:min(sy1+t, sy2), max(sx1, sx2-cl):sx2] = pixel
                arr[sy1:sy1+cl, max(sx1, sx2-t):sx2] = pixel
                # Bottom-left corner
                arr[max(sy1, sy2-t):sy2, sx1:sx1+cl] = pixel
                arr[max(sy1, sy2-cl):sy2, sx1:min(sx1+t, sx2)] = pixel
                # Bottom-right corner
                arr[max(sy1, sy2-t):sy2, max(sx1, sx2-cl):sx2] = pixel
                arr[max(sy1, sy2-cl):sy2, max(sx1, sx2-t):sx2] = pixel
            else:
                # Full rectangle
                arr[sy1:min(sy1+t, sy2), sx1:sx2] = pixel  # top
                arr[max(sy1, sy2-t):sy2, sx1:sx2] = pixel  # bottom
                arr[sy1:sy2, sx1:min(sx1+t, sx2)] = pixel  # left
                arr[sy1:sy2, max(sx1, sx2-t):sx2] = pixel  # right

            # Draw aim dot if enabled and aim point is provided
            if show_dot and "_aim_x" in d and "_aim_y" in d:
                dx = int(d["_aim_x"] + ox)
                dy = int(d["_aim_y"] + oy)
                ds = dot_size
                if dot_style == "circle":
                    # Draw filled circle using distance check
                    y_lo = max(0, dy - ds)
                    y_hi = min(h, dy + ds + 1)
                    x_lo = max(0, dx - ds)
                    x_hi = min(w, dx + ds + 1)
                    if y_hi > y_lo and x_hi > x_lo:
                        yy = np.arange(y_lo, y_hi).reshape(-1, 1)
                        xx = np.arange(x_lo, x_hi).reshape(1, -1)
                        mask = ((xx - dx)**2 + (yy - dy)**2) <= ds**2
                        region = arr[y_lo:y_hi, x_lo:x_hi]
                        region[mask] = dot_bgra
                elif dot_style == "cross":
                    # Horizontal line
                    cx1 = max(0, dx - ds)
                    cx2 = min(w, dx + ds + 1)
                    cy1 = max(0, dy - 1)
                    cy2 = min(h, dy + 2)
                    arr[cy1:cy2, cx1:cx2] = dot_bgra
                    # Vertical line
                    vy1 = max(0, dy - ds)
                    vy2 = min(h, dy + ds + 1)
                    vx1 = max(0, dx - 1)
                    vx2 = min(w, dx + 2)
                    arr[vy1:vy2, vx1:vx2] = dot_bgra
                elif dot_style == "diamond":
                    for iy in range(-ds, ds + 1):
                        span = ds - abs(iy)
                        py = dy + iy
                        if py < 0 or py >= h:
                            continue
                        lx = max(0, dx - span)
                        rx = min(w, dx + span + 1)
                        if rx > lx:
                            arr[py, lx:rx] = dot_bgra

            # Draw label text using GDI on mem_dc (supports Unicode)
            label = d.get("_label", "")
            if label and not hide_label:
                cr = r | (g << 8) | (b << 16)
                gdi32.SetTextColor(mem_dc, cr)
                gdi32.SetBkMode(mem_dc, 1)  # TRANSPARENT background
                txt = ctypes.create_unicode_buffer(label)
                lbl_y = max(0, sy1 - 16)
                rc = ctypes.wintypes.RECT(sx1, lbl_y, sx2 + 100, sy1)
                user32.DrawTextW(mem_dc, txt, -1, ctypes.byref(rc), 0)
                # DrawTextW writes RGB but leaves alpha=0 → fix alpha for text pixels
                lbl_h = min(16, sy1 - lbl_y) if sy1 > lbl_y else 0
                if lbl_h > 0:
                    text_region = arr[lbl_y:lbl_y + lbl_h, sx1:min(sx2 + 100, w)]
                    mask = (text_region[:, :, 0].astype(np.uint16) +
                            text_region[:, :, 1].astype(np.uint16) +
                            text_region[:, :, 2].astype(np.uint16)) > 0
                    text_region[:, :, 3] = np.where(mask, 255, text_region[:, :, 3])

        # Commit to screen via UpdateLayeredWindow
        user32.UpdateLayeredWindow(self._hwnd, self._screen_dc,
                                   ctypes.byref(self._pt_dst), ctypes.byref(self._sz),
                                   mem_dc, ctypes.byref(self._pt_src),
                                   0, ctypes.byref(self._blend), self.ULW_ALPHA)

    def clear(self):
        """Clear overlay (draw transparent frame)."""
        self.draw([])

    def destroy(self):
        """Destroy the overlay window and free cached GDI resources."""
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        if self._cached_mem_dc:
            if self._cached_old_bmp:
                gdi32.SelectObject(self._cached_mem_dc, self._cached_old_bmp)
            if self._cached_dib:
                gdi32.DeleteObject(self._cached_dib)
            gdi32.DeleteDC(self._cached_mem_dc)
            self._cached_mem_dc = None
            self._cached_dib = None
            self._cached_old_bmp = None
            self._cached_arr = None
        if hasattr(self, '_screen_dc') and self._screen_dc:
            user32.ReleaseDC(0, self._screen_dc)
            self._screen_dc = None
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None

from config import confidence as _conf_default
from recoil_patterns import (WEAPON_NAMES, get_recoil_offset, get_bullet_delta, get_fire_interval_ms, get_mag_size,
                              RIGID_WEAPON_NAMES, get_rigid_weapon_data)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.py")
PROFILES_DIR = os.path.join(SCRIPT_DIR, "profiles")
LAST_PROFILE_PATH = os.path.join(PROFILES_DIR, "_last_profile.txt")

# ---- Profile system ----
def _ensure_profiles_dir():
    os.makedirs(PROFILES_DIR, exist_ok=True)

def list_profiles():
    """Return sorted list of profile names (without .json extension)."""
    _ensure_profiles_dir()
    names = []
    for f in os.listdir(PROFILES_DIR):
        if f.endswith(".json"):
            names.append(f[:-5])
    return sorted(names)

def _profile_path(name):
    return os.path.join(PROFILES_DIR, f"{name}.json")

def save_profile(name, vals: dict):
    """Save a config dict as a named profile JSON."""
    _ensure_profiles_dir()
    with open(_profile_path(name), "w", encoding="utf-8") as f:
        json.dump(vals, f, ensure_ascii=False, indent=2)
    # Remember last used profile
    with open(LAST_PROFILE_PATH, "w", encoding="utf-8") as f:
        f.write(name)

def load_profile(name) -> dict:
    """Load a named profile JSON. Returns dict or empty dict on error."""
    path = _profile_path(name)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def delete_profile(name):
    path = _profile_path(name)
    if os.path.isfile(path):
        os.remove(path)

def get_last_profile():
    """Return the name of the last used profile, or empty string."""
    try:
        with open(LAST_PROFILE_PATH, "r", encoding="utf-8") as f:
            name = f.read().strip()
        if os.path.isfile(_profile_path(name)):
            return name
    except Exception:
        pass
    return ""


def _box_iou(box1, box2):
    """Compute IoU between two sets of boxes [x1,y1,x2,y2]. box1: [N,4], box2: [M,4] → [N,M]."""
    area1 = (box1[:, 2] - box1[:, 0]) * (box1[:, 3] - box1[:, 1])
    area2 = (box2[:, 2] - box2[:, 0]) * (box2[:, 3] - box2[:, 1])
    inter_x1 = torch.max(box1[:, None, 0], box2[:, 0])
    inter_y1 = torch.max(box1[:, None, 1], box2[:, 1])
    inter_x2 = torch.min(box1[:, None, 2], box2[:, 2])
    inter_y2 = torch.min(box1[:, None, 3], box2[:, 3])
    inter = (inter_x2 - inter_x1).clamp(0) * (inter_y2 - inter_y1).clamp(0)
    return inter / (area1[:, None] + area2 - inter + 1e-6)


def scan_onnx_models():
    """Scan project root, customModels/, and models/ for .onnx files, return display_name -> abs_path dict."""
    models = {}
    # Scan project root
    for f in os.listdir(SCRIPT_DIR):
        if f.lower().endswith(".onnx"):
            models[f] = os.path.join(SCRIPT_DIR, f)
    # Scan additional directories: customModels/ and models/
    for sub in ("customModels", "models"):
        sub_dir = os.path.join(SCRIPT_DIR, sub)
        if os.path.isdir(sub_dir):
            for dirpath, _dirnames, filenames in os.walk(sub_dir):
                for f in filenames:
                    if f.lower().endswith(".onnx"):
                        rel = os.path.relpath(os.path.join(dirpath, f), SCRIPT_DIR)
                        models[rel] = os.path.join(dirpath, f)
    return models

# Key display name -> virtual key code
KEY_OPTIONS = {
    "鼠标右键 (Right Click)": 0x02,
    "鼠标左键 (Left Click)": 0x01,
    "鼠标侧键1 (X1)": 0x05,
    "鼠标侧键2 (X2)": 0x06,
    "Shift": 0x10,
    "Ctrl": 0x11,
    "Alt": 0x12,
    "Caps Lock": 0x14,
    "E": 0x45,
    "F": 0x46,
    "X": 0x58,
}
KEY_CODE_TO_NAME = {v: k for k, v in KEY_OPTIONS.items()}

# Secondary key options (includes "禁用" to disable)
KEY2_OPTIONS = {"禁用 (Off)": 0x00}
KEY2_OPTIONS.update(KEY_OPTIONS)
KEY2_CODE_TO_NAME = {v: k for k, v in KEY2_OPTIONS.items()}

TARGET_OPTIONS = {
    "头部 (Head)": "head",
    "胸口 (Chest)": "chest",
    "身体中心 (Body)": "body",
    "最近位置 (Nearest)": "nearest",
}
TARGET_VALUE_TO_NAME = {v: k for k, v in TARGET_OPTIONS.items()}

# Team filter: which class IDs to aim at
# For multi-class models (e.g. cs2_320 with CT=0, T=1):
#   "all"  = aim at all classes
#   "ct"   = I am CT, aim at T (class 1)
#   "t"    = I am T, aim at CT (class 0)
TEAM_OPTIONS = {
    "全部目标 (All)": "all",
    "我是CT (瞄T)": "ct",
    "我是T (瞄CT)": "t",
}
TEAM_VALUE_TO_NAME = {v: k for k, v in TEAM_OPTIONS.items()}

# Aim mode: aimbot (full takeover) vs aim assist (additive pull, user keeps control)
AIM_MODE_OPTIONS = {
    "自瞄 (Aimbot)": "aimbot",
    "辅助瞄准 (Assist)": "assist",
}
AIM_MODE_VALUE_TO_NAME = {v: k for k, v in AIM_MODE_OPTIONS.items()}

# Hotkey options for toggle keys (keyboard keys only)
HOTKEY_OPTIONS = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Home": 0x24, "End": 0x23, "Insert": 0x2D, "Delete": 0x2E,
    "Page Up": 0x21, "Page Down": 0x22,
    "Num 0": 0x60, "Num 1": 0x61, "Num 2": 0x62, "Num 3": 0x63,
}
HOTKEY_CODE_TO_NAME = {v: k for k, v in HOTKEY_OPTIONS.items()}




def _play_notification(text, voice_enabled, on=True):
    """Play audio notification in a background thread.
    voice_enabled=True → TTS speaks `text`; False → beep tone only.
    on=True → high-pitched beep (enabled); False → low-pitched beep (disabled).
    """
    def _worker():
        if voice_enabled and win32com_client is not None and pythoncom is not None:
            try:
                pythoncom.CoInitialize()
                speaker = win32com_client.Dispatch("SAPI.SpVoice")
                speaker.Rate = 4  # faster speech
                speaker.Speak(text)
                pythoncom.CoUninitialize()
                return
            except Exception:
                pass
        # Fallback: beep tones
        try:
            if on:
                winsound.Beep(800, 150)
                time.sleep(0.05)
                winsound.Beep(1200, 150)
            else:
                winsound.Beep(1200, 150)
                time.sleep(0.05)
                winsound.Beep(600, 200)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


def _read_config_value(name, default, cast=str):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(rf'^{name}\s*=\s*(.+)$', content, re.MULTILINE)
        if match:
            val = match.group(1).strip().strip('"').strip("'")
            return cast(val)
    except Exception:
        pass
    return default


def _read_config_hex(name, default):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(rf'^{name}\s*=\s*(.+)$', content, re.MULTILINE)
        if match:
            return int(match.group(1).strip(), 0)
    except Exception:
        pass
    return default


def save_config_values(values: dict):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    for name, value in values.items():
        if name in ("aaActivateKey", "aaSecondaryKey"):
            replacement = f'{name} = {hex(value)}'
        elif name in ("visuals", "cpsDisplay", "centerOfScreen", "headshot_mode", "useMask"):
            replacement = f'{name} = {value}'
        elif isinstance(value, float):
            replacement = f'{name} = {value}'
        elif isinstance(value, str):
            replacement = f'{name} = "{value}"'
        else:
            replacement = f'{name} = {value}'
        content = re.sub(rf'^{name}\s*=\s*.+$', lambda m: replacement, content, flags=re.MULTILINE)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)


class VisionViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Aimbot - Vision Viewer + 配置")
        self.root.geometry("1180x620")
        self.root.minsize(1060, 550)

        self.windows = []
        self.camera = None
        self.model = None
        self.running = False
        self.worker = None
        self.device_name = "Not initialized"

        self.status_var = tk.StringVar(value="Ready")
        self.device_var = tk.StringVar(value="Device: -")

        # ---- Live aim config variables (read by aim loop every frame) ----
        self.aim_enabled_var = tk.BooleanVar(value=True)
        self.fov_var = tk.IntVar(value=_read_config_value("aaFOV", 150, int))
        self.smooth_var = tk.DoubleVar(value=_read_config_value("aaSmoothFactor", 3.0, float))
        self.amp_var = tk.DoubleVar(value=_read_config_value("aaMovementAmp", 0.4, float))
        self.conf_var = tk.DoubleVar(value=_read_config_value("confidence", 0.4, float))

        self.target_var = tk.StringVar()
        cur_target = _read_config_value("aaTargetPart", "head", str)
        self.target_var.set(TARGET_VALUE_TO_NAME.get(cur_target, "头部 (Head)"))

        self.aim_mode_var = tk.StringVar()
        cur_aim_mode = _read_config_value("aaAimMode", "aimbot", str)
        self.aim_mode_var.set(AIM_MODE_VALUE_TO_NAME.get(cur_aim_mode, "自瞄 (Aimbot)"))

        self.key_var = tk.StringVar()
        cur_key = _read_config_hex("aaActivateKey", 0x02)
        self.key_var.set(KEY_CODE_TO_NAME.get(cur_key, "鼠标右键 (Right Click)"))

        self.key2_var = tk.StringVar()
        cur_key2 = _read_config_hex("aaSecondaryKey", 0x00)
        self.key2_var.set(KEY2_CODE_TO_NAME.get(cur_key2, "禁用 (Off)"))

        # X-axis only aim lock (Y-axis left to player for manual recoil control)
        self.aim_x_only_var = tk.BooleanVar(value=_read_config_value("aaXOnly", False, bool))
        # Duration (ms) to keep full X+Y lock before releasing Y to player
        self.aim_x_lock_duration_var = tk.IntVar(value=_read_config_value("aaXLockDuration", 0, int))
        # Always-aim: no need to hold aim key
        self.aim_always_var = tk.BooleanVar(value=_read_config_value("aaAlwaysAim", False, bool))
        # Adaptive aim: dynamically boost amp when target moves fast
        self.aim_adaptive_var = tk.BooleanVar(value=_read_config_value("aaAdaptive", False, bool))
        self.aim_adaptive_max_var = tk.DoubleVar(value=_read_config_value("aaAdaptiveMax", 3.0, float))

        self.visuals_var = tk.BooleanVar(value=_read_config_value("visuals", True, bool))
        self.overlay_var = tk.BooleanVar(value=_read_config_value("showOverlay", False, bool))
        self._overlay = None  # OverlayWindow instance, created on demand
        # Overlay customization
        self.ov_box_thickness_var = tk.IntVar(value=_read_config_value("ovBoxThickness", 2, int))
        self.ov_box_style_var = tk.StringVar(value=_read_config_value("ovBoxStyle", "full", str))  # full / corners
        self.ov_corner_len_var = tk.IntVar(value=_read_config_value("ovCornerLen", 15, int))
        self.ov_dot_var = tk.BooleanVar(value=_read_config_value("ovDot", False, bool))
        self.ov_dot_size_var = tk.IntVar(value=_read_config_value("ovDotSize", 4, int))
        self.ov_dot_style_var = tk.StringVar(value=_read_config_value("ovDotStyle", "circle", str))  # circle / cross / diamond
        self.ov_dot_color_var = tk.StringVar(value=_read_config_value("ovDotColor", "red", str))  # red / green / cyan / white / yellow
        self.ov_hide_label_var = tk.BooleanVar(value=_read_config_value("ovHideLabel", False, bool))
        # Target lock: lock nearest target, ignore others
        self.aim_target_lock_var = tk.BooleanVar(value=_read_config_value("aaTargetLock", True, bool))
        self.aim_target_lock_frames_var = tk.IntVar(value=_read_config_value("aaTargetLockFrames", 8, int))
        self.aim_target_lock_radius_var = tk.IntVar(value=_read_config_value("aaTargetLockRadius", 100, int))
        # PID controller gains for aim tracking
        # Ki (integral): eliminates steady-state error on moving targets. 0=pure P.
        self.aim_ki_var = tk.DoubleVar(value=_read_config_value("aaKi", 0.0, float))
        # Kd (derivative): dampens overshoot / oscillation. 0=no damping.
        self.aim_kd_var = tk.DoubleVar(value=_read_config_value("aaKd", 0.0, float))
        self.crosshair_y_offset_var = tk.IntVar(value=_read_config_value("crosshairYOffset", 0, int))
        self.fps_var = tk.IntVar(value=_read_config_value("captureFPS", 60, int))
        self.screenshot_size_var = tk.IntVar(value=_read_config_value("screenShotHeight", 320, int))

        # Team filter
        cur_team = _read_config_value("aaTeamFilter", "all", str)
        self.team_var = tk.StringVar(value=TEAM_VALUE_TO_NAME.get(cur_team, "全部目标 (All)"))

        # Recoil compensation
        self.recoil_weapon_var = tk.StringVar(value=_read_config_value("recoilWeapon", "关闭 (Off)", str))
        self.recoil_strength_var = tk.DoubleVar(value=_read_config_value("recoilStrength", 1.0, float))
        self.recoil_smooth_var = tk.IntVar(value=_read_config_value("recoilSmooth", 4, int))
        self.recoil_time_offset_var = tk.IntVar(value=_read_config_value("recoilTimeOffset", 0, int))
        # Recoil trigger key
        self.recoil_key_var = tk.StringVar()
        cur_rc_key = _read_config_hex("recoilKey", 0x01)
        self.recoil_key_var.set(KEY_CODE_TO_NAME.get(cur_rc_key, "鼠标左键 (Left Click)"))
        # Recoil key state for tracking spray (polled by key poll thread)
        self._recoil_key_is_down = False
        # Minimum hold duration (ms) before recoil activates — tap/click won't trigger
        self.recoil_hold_ms_var = tk.IntVar(value=_read_config_value("recoilHoldMs", 100, int))

        # Recoil enabled toggle (like aim_enabled_var for aim)
        self.recoil_enabled_var = tk.BooleanVar(value=True)
        # Recoil only when aim key is held (prevents recoil during grenade throws etc.)
        self.recoil_aim_only_var = tk.BooleanVar(value=_read_config_value("recoilAimOnly", False, bool))

        # ---- Rigid recoil mode (FullExternal-style dedicated thread) ----
        self.rigid_recoil_var = tk.BooleanVar(value=False)  # checkbox to enable rigid mode
        self.rigid_weapon_var = tk.StringVar(value=_read_config_value("rigidWeapon", "关闭 (Off)", str))
        self.cs2_sensitivity_var = tk.DoubleVar(value=_read_config_value("cs2Sensitivity", 2.5, float))
        # Custom smoothness: steps (sub-moves per bullet) and delays (microseconds)
        self.rigid_steps_var = tk.IntVar(value=_read_config_value("rigidSteps", 1, int))
        self.rigid_delay1_var = tk.IntVar(value=_read_config_value("rigidDelay1", 100, int))  # ms between sub-steps
        self.rigid_delay2_var = tk.IntVar(value=_read_config_value("rigidDelay2", 0, int))    # ms after all sub-steps
        # AI dual-axis correction during spray
        self.rigid_ai_correct_var = tk.BooleanVar(value=False)
        # Rigid recoil thread control
        self._rigid_recoil_running = False
        self._rigid_recoil_thread = None
        self._rigid_spray_active = False  # True while rigid thread is spraying (for aim Y suppression)

        # Toggle hotkeys
        self.aim_toggle_key_var = tk.StringVar()
        cur_aim_hk = _read_config_hex("aimToggleKey", 0x74)  # F5
        self.aim_toggle_key_var.set(HOTKEY_CODE_TO_NAME.get(cur_aim_hk, "F5"))
        self.recoil_toggle_key_var = tk.StringVar()
        cur_rc_hk = _read_config_hex("recoilToggleKey", 0x75)  # F6
        self.recoil_toggle_key_var.set(HOTKEY_CODE_TO_NAME.get(cur_rc_hk, "F6"))

        # Voice notification toggle
        self.voice_enabled_var = tk.BooleanVar(value=True)

        # Triggerbot
        self.trigger_enabled_var = tk.BooleanVar(value=False)
        self.trigger_delay_var = tk.IntVar(value=_read_config_value("triggerDelay", 80, int))
        self.trigger_toggle_key_var = tk.StringVar()
        cur_trig_hk = _read_config_hex("triggerToggleKey", 0x76)  # F7
        self.trigger_toggle_key_var.set(HOTKEY_CODE_TO_NAME.get(cur_trig_hk, "F7"))

        # Color detection mode (找色模式)
        self.color_mode_var = tk.BooleanVar(value=False)  # True = use color detection instead of AI
        # HSV color presets for different games/highlights
        self._color_presets = {
            "紫色 (Purple)": {"lower": [140, 110, 150], "upper": [150, 195, 255]},
            "红色 (Red)":    {"lower": [0, 150, 150],   "upper": [10, 255, 255]},
            "黄色 (Yellow)": {"lower": [20, 125, 150],  "upper": [40, 255, 255]},
            "绿色 (Green)":  {"lower": [40, 100, 100],  "upper": [80, 255, 255]},
            "青色 (Cyan)":   {"lower": [80, 100, 100],  "upper": [100, 255, 255]},
            "自定义 (Custom)": None,  # Uses manual HSV sliders
        }
        self.color_preset_var = tk.StringVar(value=_read_config_value("colorPreset", "紫色 (Purple)", str))
        self.color_h_low_var = tk.IntVar(value=_read_config_value("colorHLow", 140, int))
        self.color_s_low_var = tk.IntVar(value=_read_config_value("colorSLow", 110, int))
        self.color_v_low_var = tk.IntVar(value=_read_config_value("colorVLow", 150, int))
        self.color_h_high_var = tk.IntVar(value=_read_config_value("colorHHigh", 150, int))
        self.color_s_high_var = tk.IntVar(value=_read_config_value("colorSHigh", 195, int))
        self.color_v_high_var = tk.IntVar(value=_read_config_value("colorVHigh", 255, int))
        self.color_smooth_var = tk.DoubleVar(value=_read_config_value("colorSmooth", 0.3, float))
        self.color_min_area_var = tk.IntVar(value=_read_config_value("colorMinArea", 20, int))

        # Anti-flash (自动背闪)
        self.antiflash_enabled_var = tk.BooleanVar(value=False)
        self.antiflash_delay_var = tk.DoubleVar(value=_read_config_value("antiflashDelay", 0.5, float))  # seconds
        self.antiflash_conf_var = tk.DoubleVar(value=_read_config_value("antiflashConf", 0.5, float))  # min confidence
        self._antiflash_active = False       # True while turned away
        self._antiflash_cooldown_until = 0.0 # timestamp: ignore new flashes until this time

        # Profile system
        self.profile_var = tk.StringVar()
        last_prof = get_last_profile()
        if last_prof:
            self.profile_var.set(last_prof)

        self._mouse_mode = False  # True = capture follows mouse cursor, no window needed

        # Thread-safe key state flag (polled at ~1000Hz by background thread)
        self._key_is_down = False
        self._key_poll_running = True
        self._key_poll_thread = threading.Thread(target=self._key_poll_loop, daemon=True)
        self._key_poll_thread.start()

        # Model selection
        self.available_models = scan_onnx_models()
        self.model_var = tk.StringVar()
        saved_model = _read_config_value("selectedModel", "yolov5s320Half.onnx", str)
        if saved_model in self.available_models:
            self.model_var.set(saved_model)
        elif self.available_models:
            self.model_var.set(list(self.available_models.keys())[0])
        # Lock for thread-safe model swap
        self._model_lock = threading.Lock()
        self._model_input_size = (320, 320)  # (w, h) — updated when model loads
        self._model_input_dtype = np.float16  # updated when model loads
        self._model_input_name = "images"     # updated when model loads
        self._model_output_format = "v5"      # "v5" or "v8" — updated when model loads
        # Class filter: {cls_id: name} from model metadata, and BooleanVars per class
        self._model_class_names = {}          # {0: "CT", 1: "T", ...} from metadata
        self._class_filter_vars = {}          # {cls_id: tk.BooleanVar} — True = enabled
        self._class_filter_frame = None       # ttk.Frame holding checkboxes, rebuilt on model load

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # Main horizontal panes
        pw = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)

        # ===== LEFT: window list + controls =====
        left = ttk.Frame(pw)
        pw.add(left, weight=3)

        top_frame = ttk.Frame(left, padding=8)
        top_frame.pack(fill=tk.X)
        ttk.Button(top_frame, text="▶ 全屏启动", command=self.start_viewer_fullscreen).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(top_frame, text="停止", command=self.stop_viewer).pack(side=tk.LEFT, padx=(0, 10))

        # Placeholder for preview area (empty when not running)
        self._preview_frame = ttk.Frame(left)
        self._preview_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        bottom = ttk.Frame(left, padding=(8, 0, 8, 8))
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, textvariable=self.device_var).pack(side=tk.LEFT)
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.RIGHT)

        # ===== RIGHT: config panel as Notebook with 5 tabs =====
        right_pane = ttk.Frame(pw, padding=4)
        pw.add(right_pane, weight=1)

        # CRITICAL: Disable MouseWheel on all Combobox widgets globally.
        # readonly TCombobox responds to MouseWheel by cycling through values,
        # which silently corrupts settings when the user scrolls the panel.
        self.root.unbind_class("TCombobox", "<MouseWheel>")

        notebook = ttk.Notebook(right_pane)
        notebook.pack(fill="both", expand=True)

        def _make_scrollable_tab(title):
            """Create a scrollable tab inside the notebook. Returns the inner frame."""
            tab = ttk.Frame(notebook, padding=4)
            notebook.add(tab, text=title)
            canvas_w = tk.Canvas(tab, highlightthickness=0)
            sb = ttk.Scrollbar(tab, orient="vertical", command=canvas_w.yview)
            inner = ttk.Frame(canvas_w)
            inner.bind("<Configure>", lambda e: canvas_w.configure(scrollregion=canvas_w.bbox("all")))
            cw_id = canvas_w.create_window((0, 0), window=inner, anchor="nw")
            # Make inner frame fill canvas width when canvas resizes
            canvas_w.bind("<Configure>", lambda e, cid=cw_id, cv=canvas_w: cv.itemconfig(cid, width=e.width))
            canvas_w.configure(yscrollcommand=sb.set)
            canvas_w.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            # Mouse wheel: only when cursor is over THIS tab's canvas
            def _on_wheel(e, cv=canvas_w):
                cv.yview_scroll(int(-1*(e.delta/120)), "units")
            canvas_w.bind("<Enter>", lambda e, cv=canvas_w, fn=_on_wheel: cv.bind_all("<MouseWheel>", fn))
            canvas_w.bind("<Leave>", lambda e: canvas_w.unbind_all("<MouseWheel>"))
            return inner

        # Create 5 tabs (order matters: aim is most-used so it's first)
        tab_aim    = _make_scrollable_tab("瞄准")
        tab_detect = _make_scrollable_tab("识别")
        tab_combat = _make_scrollable_tab("压枪/扳机")
        tab_visual = _make_scrollable_tab("外观")
        tab_system = _make_scrollable_tab("系统")

        # `right` is the current build target — start with system tab for profile section
        right = tab_system

        # ===== Profile selector =====
        ttk.Label(right, text="── 配置方案 ──", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 2))
        prof_frame = ttk.Frame(right)
        prof_frame.pack(fill="x", pady=2)
        ttk.Label(prof_frame, text="当前方案:").pack(side="left")
        self.profile_combo = ttk.Combobox(prof_frame, textvariable=self.profile_var,
                                           values=list_profiles(), state="readonly", width=16)
        self.profile_combo.pack(side="left", padx=4)
        ttk.Button(prof_frame, text="加载", command=self._load_profile, width=4).pack(side="left", padx=2)

        prof_btn_frame = ttk.Frame(right)
        prof_btn_frame.pack(fill="x", pady=2)
        ttk.Button(prof_btn_frame, text="新建方案", command=self._new_profile).pack(side="left", padx=(0, 4))
        ttk.Button(prof_btn_frame, text="删除方案", command=self._delete_profile).pack(side="left", padx=(0, 4))
        ttk.Button(prof_btn_frame, text="重命名", command=self._rename_profile).pack(side="left")

        # ===== Switch to DETECT tab =====
        right = tab_detect

        ttk.Label(right, text="识别设置", font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(0, 8), fill="x")

        # --- Model selector ---
        ttk.Label(right, text="检测模型:", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        model_names = list(self.available_models.keys())
        self.model_combo = ttk.Combobox(right, textvariable=self.model_var, values=model_names,
                                        state="readonly", width=30)
        self.model_combo.pack(fill="x", pady=2)
        btn_frame = ttk.Frame(right)
        btn_frame.pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="切换模型", command=self._switch_model).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="刷新模型列表", command=self._refresh_models).pack(side="left")
        self.model_status_label = ttk.Label(right, text="", foreground="gray")
        self.model_status_label.pack(anchor="w")

        # --- Class filter (dynamic checkboxes, rebuilt when model loads) ---
        self._class_filter_frame = ttk.LabelFrame(right, text="识别类别筛选")
        self._class_filter_frame.pack(fill="x", pady=(2, 0))
        ttk.Label(self._class_filter_frame, text="(加载模型后自动显示)", foreground="gray").pack(anchor="w")

        # ===== Switch to AIM tab =====
        right = tab_aim

        # --- Status display ---
        status_frame = ttk.Frame(right)
        status_frame.pack(fill="x", pady=2)
        ttk.Label(status_frame, text="自瞄状态:").pack(side="left")
        self.aim_status_label = tk.Label(status_frame, text="已开启", fg="green", font=("", 9, "bold"))
        self.aim_status_label.pack(side="left", padx=(4, 12))
        ttk.Label(status_frame, text="压枪状态:").pack(side="left")
        self.recoil_status_label = tk.Label(status_frame, text="已开启", fg="green", font=("", 9, "bold"))
        self.recoil_status_label.pack(side="left", padx=4)
        status_frame2 = ttk.Frame(right)
        status_frame2.pack(fill="x", pady=2)
        ttk.Label(status_frame2, text="扳机状态:").pack(side="left")
        self.trigger_status_label = tk.Label(status_frame2, text="已关闭", fg="red", font=("", 9, "bold"))
        self.trigger_status_label.pack(side="left", padx=4)

        # Checkbuttons
        ttk.Checkbutton(right, text="启用自瞄", variable=self.aim_enabled_var,
                         command=self._update_status_labels).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="启用压枪", variable=self.recoil_enabled_var,
                         command=self._update_status_labels).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="启用扳机", variable=self.trigger_enabled_var,
                         command=self._update_status_labels).pack(anchor="w", pady=2)
        # ===== Switch to VISUAL tab =====
        right = tab_visual

        ttk.Label(right, text="外观设置", font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(0, 8), fill="x")
        ttk.Checkbutton(right, text="显示预览窗口", variable=self.visuals_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="屏幕叠加层 (Overlay)", variable=self.overlay_var).pack(anchor="w", pady=2)

        # --- Overlay customization ---
        ov_frame = ttk.LabelFrame(right, text="Overlay 外观设置")
        ov_frame.pack(fill="x", pady=4, padx=2)

        f_ovt = ttk.Frame(ov_frame); f_ovt.pack(fill="x", pady=1)
        ttk.Label(f_ovt, text="方框粗细:").pack(side="left")
        self.ov_thickness_label = ttk.Label(f_ovt, text=str(self.ov_box_thickness_var.get()))
        self.ov_thickness_label.pack(side="right")
        tk.Scale(ov_frame, from_=1, to=6, orient="horizontal", variable=self.ov_box_thickness_var,
                 command=lambda v: self.ov_thickness_label.configure(text=str(int(float(v))))).pack(fill="x")

        f_ovs = ttk.Frame(ov_frame); f_ovs.pack(fill="x", pady=1)
        ttk.Label(f_ovs, text="方框样式:").pack(side="left")
        ttk.Combobox(f_ovs, textvariable=self.ov_box_style_var,
                     values=["full", "corners"], state="readonly", width=10).pack(side="right")

        f_ovcl = ttk.Frame(ov_frame); f_ovcl.pack(fill="x", pady=1)
        ttk.Label(f_ovcl, text="转角长度:").pack(side="left")
        self.ov_corner_label = ttk.Label(f_ovcl, text=str(self.ov_corner_len_var.get()))
        self.ov_corner_label.pack(side="right")
        tk.Scale(ov_frame, from_=5, to=40, orient="horizontal", variable=self.ov_corner_len_var,
                 command=lambda v: self.ov_corner_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(ov_frame, text="corners模式下转角线段长度", font=("", 8)).pack(anchor="w")

        ttk.Checkbutton(ov_frame, text="隐藏标签文字 (CT/T 和置信度)", variable=self.ov_hide_label_var).pack(anchor="w", pady=1)

        ttk.Separator(ov_frame, orient="horizontal").pack(fill="x", pady=3)

        ttk.Checkbutton(ov_frame, text="绘制瞄准点", variable=self.ov_dot_var).pack(anchor="w", pady=1)

        f_ovds = ttk.Frame(ov_frame); f_ovds.pack(fill="x", pady=1)
        ttk.Label(f_ovds, text="瞄点样式:").pack(side="left")
        ttk.Combobox(f_ovds, textvariable=self.ov_dot_style_var,
                     values=["circle", "cross", "diamond"], state="readonly", width=10).pack(side="right")

        f_ovdc = ttk.Frame(ov_frame); f_ovdc.pack(fill="x", pady=1)
        ttk.Label(f_ovdc, text="瞄点颜色:").pack(side="left")
        ttk.Combobox(f_ovdc, textvariable=self.ov_dot_color_var,
                     values=["red", "green", "cyan", "white", "yellow", "magenta"], state="readonly", width=10).pack(side="right")

        f_ovdz = ttk.Frame(ov_frame); f_ovdz.pack(fill="x", pady=1)
        ttk.Label(f_ovdz, text="瞄点大小:").pack(side="left")
        self.ov_dot_size_label = ttk.Label(f_ovdz, text=str(self.ov_dot_size_var.get()))
        self.ov_dot_size_label.pack(side="right")
        tk.Scale(ov_frame, from_=2, to=12, orient="horizontal", variable=self.ov_dot_size_var,
                 command=lambda v: self.ov_dot_size_label.configure(text=str(int(float(v))))).pack(fill="x")

        # ===== Switch back to AIM tab =====
        right = tab_aim

        # --- Target lock (anti-pull) ---
        ttk.Checkbutton(right, text="目标锁定 (避免多目标拉扯)", variable=self.aim_target_lock_var).pack(anchor="w", pady=2)
        f_tlf = ttk.Frame(right); f_tlf.pack(fill="x", pady=1)
        ttk.Label(f_tlf, text="丢失帧数:").pack(side="left")
        self.tl_frames_label = ttk.Label(f_tlf, text=str(self.aim_target_lock_frames_var.get()))
        self.tl_frames_label.pack(side="right")
        tk.Scale(right, from_=3, to=30, orient="horizontal", variable=self.aim_target_lock_frames_var,
                 command=lambda v: self.tl_frames_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="目标消失多少帧后才切换 越大越不容易切目标", font=("", 8)).pack(anchor="w")

        f_tlr = ttk.Frame(right); f_tlr.pack(fill="x", pady=1)
        ttk.Label(f_tlr, text="追踪半径:").pack(side="left")
        self.tl_radius_label = ttk.Label(f_tlr, text=str(self.aim_target_lock_radius_var.get()))
        self.tl_radius_label.pack(side="right")
        tk.Scale(right, from_=30, to=200, orient="horizontal", variable=self.aim_target_lock_radius_var,
                 command=lambda v: self.tl_radius_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="帧间目标匹配距离 越大容忍快速移动", font=("", 8)).pack(anchor="w")

        # --- PID controller gains ---
        ttk.Label(right, text="── PID 控制器 ──", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
        ttk.Label(right, text="Kp = amp/smooth (上方已有), 下面设置 Ki 和 Kd", font=("", 8)).pack(anchor="w")

        f_ki = ttk.Frame(right); f_ki.pack(fill="x", pady=1)
        ttk.Label(f_ki, text="Ki (积分):").pack(side="left")
        self.ki_label = ttk.Label(f_ki, text=f"{self.aim_ki_var.get():.2f}")
        self.ki_label.pack(side="right")
        tk.Scale(right, from_=0.0, to=10.0, orient="horizontal", variable=self.aim_ki_var,
                 resolution=0.1, command=lambda v: self.ki_label.configure(text=f"{float(v):.1f}")).pack(fill="x")
        ttk.Label(right, text="消除移动目标跟踪偏差 0=纯P 高smooth时需更大Ki", font=("", 8)).pack(anchor="w")

        f_kd = ttk.Frame(right); f_kd.pack(fill="x", pady=1)
        ttk.Label(f_kd, text="Kd (微分):").pack(side="left")
        self.kd_label = ttk.Label(f_kd, text=f"{self.aim_kd_var.get():.2f}")
        self.kd_label.pack(side="right")
        tk.Scale(right, from_=0.0, to=5.0, orient="horizontal", variable=self.aim_kd_var,
                 resolution=0.1, command=lambda v: self.kd_label.configure(text=f"{float(v):.1f}")).pack(fill="x")
        ttk.Label(right, text="抑制过冲/抖动 0=无阻尼 推荐0.5~2.0", font=("", 8)).pack(anchor="w")

        # Start a periodic status label updater (catches hotkey toggles too)
        self._update_status_labels()

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # Target part
        f1 = ttk.Frame(right); f1.pack(fill="x", pady=2)
        ttk.Label(f1, text="锁定位置:").pack(side="left")
        ttk.Combobox(f1, textvariable=self.target_var, values=list(TARGET_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")

        # Aim mode
        f_aim_mode = ttk.Frame(right); f_aim_mode.pack(fill="x", pady=2)
        ttk.Label(f_aim_mode, text="自瞄模式:").pack(side="left")
        ttk.Combobox(f_aim_mode, textvariable=self.aim_mode_var, values=list(AIM_MODE_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")

        # Activate key
        f2 = ttk.Frame(right); f2.pack(fill="x", pady=2)
        ttk.Label(f2, text="自瞄按键:").pack(side="left")
        ttk.Combobox(f2, textvariable=self.key_var, values=list(KEY_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")

        # Secondary activate key
        f2b = ttk.Frame(right); f2b.pack(fill="x", pady=2)
        ttk.Label(f2b, text="次要自瞄键:").pack(side="left")
        ttk.Combobox(f2b, textvariable=self.key2_var, values=list(KEY2_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")

        # X-axis only aim lock
        ttk.Checkbutton(right, text="只锁X轴 (Y轴由玩家自己压枪)", variable=self.aim_x_only_var).pack(anchor="w", pady=2)

        # X-lock duration: full X+Y lock for first N ms, then release Y
        f_xlock = ttk.Frame(right); f_xlock.pack(fill="x", pady=2)
        ttk.Label(f_xlock, text="锁头时长(ms):").pack(side="left")
        self.xlock_dur_label = ttk.Label(f_xlock, text=str(self.aim_x_lock_duration_var.get()))
        self.xlock_dur_label.pack(side="right")
        tk.Scale(right, from_=0, to=1000, orient="horizontal", variable=self.aim_x_lock_duration_var,
                 resolution=50, command=lambda v: self.xlock_dur_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="按下自瞄键后先锁XY轴N毫秒 之后只锁X (0=始终只锁X)", font=("", 8)).pack(anchor="w")

        # Always-aim checkbox
        ttk.Checkbutton(right, text="一直自瞄 (无需按键，始终追踪目标)", variable=self.aim_always_var).pack(anchor="w", pady=2)

        # Team filter (enemy identification)
        f_team = ttk.Frame(right); f_team.pack(fill="x", pady=2)
        ttk.Label(f_team, text="敌我识别:").pack(side="left")
        ttk.Combobox(f_team, textvariable=self.team_var, values=list(TEAM_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")
        ttk.Label(right, text="需要多类别模型(如cs2_320) 单类别模型无效", font=("", 8)).pack(anchor="w")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # FOV
        f3 = ttk.Frame(right); f3.pack(fill="x", pady=2)
        ttk.Label(f3, text="自瞄范围 (FOV):").pack(side="left")
        self.fov_label = ttk.Label(f3, text=str(self.fov_var.get()))
        self.fov_label.pack(side="right")
        tk.Scale(right, from_=0, to=500, orient="horizontal", variable=self.fov_var,
                 command=lambda v: self.fov_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="0=无限制  推荐100~300", font=("", 8)).pack(anchor="w")

        # Smooth
        f4 = ttk.Frame(right); f4.pack(fill="x", pady=2)
        ttk.Label(f4, text="平滑度:").pack(side="left")
        self.smooth_label = ttk.Label(f4, text=f"{self.smooth_var.get():.1f}")
        self.smooth_label.pack(side="right")
        tk.Scale(right, from_=1.0, to=10.0, orient="horizontal", variable=self.smooth_var,
                 resolution=0.1, command=lambda v: self.smooth_label.configure(text=f"{float(v):.1f}")).pack(fill="x")
        ttk.Label(right, text="1=瞬锁 3=平滑跟踪 5+=很柔和", font=("", 8)).pack(anchor="w")

        # Movement amp
        f5 = ttk.Frame(right); f5.pack(fill="x", pady=2)
        ttk.Label(f5, text="移动倍率:").pack(side="left")
        self.amp_label = ttk.Label(f5, text=f"{self.amp_var.get():.2f}")
        self.amp_label.pack(side="right")
        tk.Scale(right, from_=0.1, to=2.0, orient="horizontal", variable=self.amp_var,
                 resolution=0.05, command=lambda v: self.amp_label.configure(text=f"{float(v):.2f}")).pack(fill="x")

        # Adaptive aim
        ttk.Checkbutton(right, text="自适应倍率 (目标移动时自动加速追踪)", variable=self.aim_adaptive_var).pack(anchor="w", pady=2)
        f_adapt = ttk.Frame(right); f_adapt.pack(fill="x", pady=2)
        ttk.Label(f_adapt, text="最大加速倍数:").pack(side="left")
        self.adaptive_max_label = ttk.Label(f_adapt, text=f"{self.aim_adaptive_max_var.get():.1f}")
        self.adaptive_max_label.pack(side="right")
        tk.Scale(right, from_=1.5, to=5.0, orient="horizontal", variable=self.aim_adaptive_max_var,
                 resolution=0.5, command=lambda v: self.adaptive_max_label.configure(text=f"{float(v):.1f}")).pack(fill="x")
        ttk.Label(right, text="静态用基础倍率 移动时最高放大到此倍数", font=("", 8)).pack(anchor="w")

        # Confidence
        f6 = ttk.Frame(right); f6.pack(fill="x", pady=2)
        ttk.Label(f6, text="检测置信度:").pack(side="left")
        self.conf_label = ttk.Label(f6, text=f"{self.conf_var.get():.2f}")
        self.conf_label.pack(side="right")
        tk.Scale(right, from_=0.1, to=0.9, orient="horizontal", variable=self.conf_var,
                 resolution=0.05, command=lambda v: self.conf_label.configure(text=f"{float(v):.2f}")).pack(fill="x")

        # Crosshair Y offset
        f7 = ttk.Frame(right); f7.pack(fill="x", pady=2)
        ttk.Label(f7, text="准星Y偏移:").pack(side="left")
        self.yoff_label = ttk.Label(f7, text="0")
        self.yoff_label.pack(side="right")
        tk.Scale(right, from_=-80, to=80, orient="horizontal", variable=self.crosshair_y_offset_var,
                 command=lambda v: self.yoff_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="负=上移准星  正=下移准星", font=("", 8)).pack(anchor="w")

        # Capture FPS
        f8 = ttk.Frame(right); f8.pack(fill="x", pady=2)
        ttk.Label(f8, text="截图帧率:").pack(side="left")
        self.fps_label = ttk.Label(f8, text=str(self.fps_var.get()))
        self.fps_label.pack(side="right")
        tk.Scale(right, from_=30, to=500, orient="horizontal", variable=self.fps_var,
                 command=lambda v: self.fps_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="实时生效  推荐60~240", font=("", 8)).pack(anchor="w")

        # Screenshot capture size
        f_ss = ttk.Frame(right); f_ss.pack(fill="x", pady=2)
        ttk.Label(f_ss, text="截图区域:").pack(side="left")
        self.ss_label = ttk.Label(f_ss, text=f"{self.screenshot_size_var.get()}x{self.screenshot_size_var.get()}")
        self.ss_label.pack(side="right")
        tk.Scale(right, from_=128, to=1024, orient="horizontal", variable=self.screenshot_size_var,
                 resolution=32, command=lambda v: self.ss_label.configure(text=f"{int(float(v))}x{int(float(v))}")).pack(fill="x")
        ttk.Label(right, text="重启捕获后生效  越大看越远但推理越慢", font=("", 8)).pack(anchor="w")

        # ===== Switch to COMBAT tab =====
        right = tab_combat

        ttk.Label(right, text="压枪 / 扳机 / 背闪", font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(0, 8), fill="x")

        # --- Recoil Compensation ---
        ttk.Label(right, text="── 压枪补偿 ──", font=("", 9, "bold")).pack(anchor="w", pady=(4, 2))

        # Weapon selector
        f_rc1 = ttk.Frame(right); f_rc1.pack(fill="x", pady=2)
        ttk.Label(f_rc1, text="武器:").pack(side="left")
        rc_combo = ttk.Combobox(f_rc1, textvariable=self.recoil_weapon_var,
                                values=WEAPON_NAMES, state="readonly", width=16)
        rc_combo.pack(side="right", fill="x", expand=True)

        # Recoil trigger key
        f_rc_key = ttk.Frame(right); f_rc_key.pack(fill="x", pady=2)
        ttk.Label(f_rc_key, text="压枪按键:").pack(side="left")
        ttk.Combobox(f_rc_key, textvariable=self.recoil_key_var, values=list(KEY_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")
        ttk.Label(right, text="按住该键才会压枪 (通常选左键)", font=("", 8)).pack(anchor="w")

        # Recoil strength
        f_rc2 = ttk.Frame(right); f_rc2.pack(fill="x", pady=2)
        ttk.Label(f_rc2, text="压枪强度:").pack(side="left")
        self.recoil_str_label = ttk.Label(f_rc2, text=f"{self.recoil_strength_var.get():.2f}")
        self.recoil_str_label.pack(side="right")
        tk.Scale(right, from_=0.0, to=100.0, orient="horizontal", variable=self.recoil_strength_var,
                 resolution=0.5, command=lambda v: self.recoil_str_label.configure(text=f"{float(v):.1f}")).pack(fill="x")
        ttk.Label(right, text="1.0=标准 <1弱补偿 >1强补偿 (按灵敏度调)", font=("", 8)).pack(anchor="w")

        # Recoil smoothness
        f_rc3 = ttk.Frame(right); f_rc3.pack(fill="x", pady=2)
        ttk.Label(f_rc3, text="压枪平滑:").pack(side="left")
        self.recoil_smooth_label = ttk.Label(f_rc3, text=str(self.recoil_smooth_var.get()))
        self.recoil_smooth_label.pack(side="right")
        tk.Scale(right, from_=1, to=8, orient="horizontal", variable=self.recoil_smooth_var,
                 resolution=1, command=lambda v: self.recoil_smooth_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="1=瞬移(机器感) 3~5=自然手感 8=非常柔和", font=("", 8)).pack(anchor="w")

        # Recoil time offset
        f_rc_toff = ttk.Frame(right); f_rc_toff.pack(fill="x", pady=2)
        ttk.Label(f_rc_toff, text="时序偏移(ms):").pack(side="left")
        self.recoil_toff_label = ttk.Label(f_rc_toff, text=str(self.recoil_time_offset_var.get()))
        self.recoil_toff_label.pack(side="right")
        tk.Scale(right, from_=-500, to=500, orient="horizontal", variable=self.recoil_time_offset_var,
                 resolution=10, command=lambda v: self.recoil_toff_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="负值=提前压枪 正值=延后压枪 瓦用-100~-200", font=("", 8)).pack(anchor="w")

        # Recoil hold threshold (tap vs hold)
        f_rc_hold = ttk.Frame(right); f_rc_hold.pack(fill="x", pady=2)
        ttk.Label(f_rc_hold, text="按住延迟(ms):").pack(side="left")
        self.recoil_hold_label = ttk.Label(f_rc_hold, text=str(self.recoil_hold_ms_var.get()))
        self.recoil_hold_label.pack(side="right")
        tk.Scale(right, from_=0, to=300, orient="horizontal", variable=self.recoil_hold_ms_var,
                 resolution=10, command=lambda v: self.recoil_hold_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="按住超过此时间才压枪，点射不触发 (0=立即，推荐80~150)", font=("", 8)).pack(anchor="w")

        # Recoil aim-only checkbox
        ttk.Checkbutton(right, text="仅自瞄时压枪 (防止投掷物等误触压枪)",
                         variable=self.recoil_aim_only_var).pack(anchor="w", pady=2)
        ttk.Label(right, text="勾选后只有按住自瞄键时才会压枪", font=("", 8)).pack(anchor="w")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # --- Rigid Recoil Mode (FullExternal-style) ---
        ttk.Label(right, text="── 精准压枪 (独立线程) ──", font=("", 9, "bold")).pack(anchor="w", pady=(4, 2))
        ttk.Label(right, text="完全复刻FullExternal压枪，独立线程+微秒级精度", font=("", 8)).pack(anchor="w")

        ttk.Checkbutton(right, text="启用精准压枪模式 (勾选后替代上方旧压枪)",
                         variable=self.rigid_recoil_var,
                         command=self._on_rigid_toggle).pack(anchor="w", pady=2)

        # Rigid weapon selector
        f_rg1 = ttk.Frame(right); f_rg1.pack(fill="x", pady=2)
        ttk.Label(f_rg1, text="武器:").pack(side="left")
        ttk.Combobox(f_rg1, textvariable=self.rigid_weapon_var,
                     values=RIGID_WEAPON_NAMES, state="readonly", width=16).pack(side="right", fill="x", expand=True)

        # CS2 sensitivity
        f_rg3 = ttk.Frame(right); f_rg3.pack(fill="x", pady=2)
        ttk.Label(f_rg3, text="CS2灵敏度:").pack(side="left")
        self.cs2_sens_label = ttk.Label(f_rg3, text=f"{self.cs2_sensitivity_var.get():.2f}")
        self.cs2_sens_label.pack(side="right")
        tk.Scale(right, from_=0.1, to=10.0, orient="horizontal", variable=self.cs2_sensitivity_var,
                 resolution=0.01, command=lambda v: self.cs2_sens_label.configure(text=f"{float(v):.2f}")).pack(fill="x")
        ttk.Label(right, text="必须和游戏内灵敏度一致! 默认2.50", font=("", 8)).pack(anchor="w")

        # Custom smoothness: steps
        f_rg_steps = ttk.Frame(right); f_rg_steps.pack(fill="x", pady=2)
        ttk.Label(f_rg_steps, text="分步数 (Steps):").pack(side="left")
        self.rigid_steps_label = ttk.Label(f_rg_steps, text=str(self.rigid_steps_var.get()))
        self.rigid_steps_label.pack(side="right")
        tk.Scale(right, from_=1, to=10, orient="horizontal", variable=self.rigid_steps_var,
                 resolution=1, command=lambda v: self.rigid_steps_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="1=一次到位(最精准) 2~3=适中 5+=很柔和", font=("", 8)).pack(anchor="w")

        # Custom smoothness: delay between sub-steps (ms)
        f_rg_d1 = ttk.Frame(right); f_rg_d1.pack(fill="x", pady=2)
        ttk.Label(f_rg_d1, text="步间延迟 (ms):").pack(side="left")
        self.rigid_d1_label = ttk.Label(f_rg_d1, text=str(self.rigid_delay1_var.get()))
        self.rigid_d1_label.pack(side="right")
        tk.Scale(right, from_=1, to=200, orient="horizontal", variable=self.rigid_delay1_var,
                 resolution=1, command=lambda v: self.rigid_d1_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="每个分步之间的间隔 推荐: 1步=100ms, 2步=25ms, 5步=4ms", font=("", 8)).pack(anchor="w")

        # Custom smoothness: delay after bullet (ms)
        f_rg_d2 = ttk.Frame(right); f_rg_d2.pack(fill="x", pady=2)
        ttk.Label(f_rg_d2, text="弹后延迟 (ms):").pack(side="left")
        self.rigid_d2_label = ttk.Label(f_rg_d2, text=str(self.rigid_delay2_var.get()))
        self.rigid_d2_label.pack(side="right")
        tk.Scale(right, from_=0, to=100, orient="horizontal", variable=self.rigid_delay2_var,
                 resolution=1, command=lambda v: self.rigid_d2_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="所有分步完成后的额外等待 通常0即可", font=("", 8)).pack(anchor="w")

        # AI dual-axis correction
        ttk.Checkbutton(right, text="AI双轴修正 (喷射中自瞄X+Y追踪目标)",
                         variable=self.rigid_ai_correct_var).pack(anchor="w", pady=2)
        ttk.Label(right, text="弹道表对抗后坐力 + AI实时修正目标偏差", font=("", 8)).pack(anchor="w")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # --- Triggerbot ---
        ttk.Label(right, text="── 扳机 (Triggerbot) ──", font=("", 9, "bold")).pack(anchor="w", pady=(4, 2))

        # Trigger delay
        f_trig = ttk.Frame(right); f_trig.pack(fill="x", pady=2)
        ttk.Label(f_trig, text="开枪延迟 (ms):").pack(side="left")
        self.trigger_delay_label = ttk.Label(f_trig, text=str(self.trigger_delay_var.get()))
        self.trigger_delay_label.pack(side="right")
        tk.Scale(right, from_=0, to=500, orient="horizontal", variable=self.trigger_delay_var,
                 resolution=10, command=lambda v: self.trigger_delay_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="0=瞬间开枪  50~150=自然反应  推荐80ms", font=("", 8)).pack(anchor="w")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # --- Anti-flash (自动背闪) ---
        ttk.Label(right, text="── 自动背闪 (Anti-Flash) ──", font=("", 9, "bold")).pack(anchor="w", pady=(4, 2))
        ttk.Checkbutton(right, text="启用自动背闪", variable=self.antiflash_enabled_var).pack(anchor="w")
        ttk.Label(right, text="检测到闪光弹时自动转身→等待→转回", font=("", 8)).pack(anchor="w")

        # Anti-flash delay
        f_af1 = ttk.Frame(right); f_af1.pack(fill="x", pady=2)
        ttk.Label(f_af1, text="背身持续 (秒):").pack(side="left")
        self.antiflash_delay_label = ttk.Label(f_af1, text=f"{self.antiflash_delay_var.get():.1f}")
        self.antiflash_delay_label.pack(side="right")
        tk.Scale(right, from_=0.2, to=3.0, orient="horizontal", variable=self.antiflash_delay_var,
                 resolution=0.1, command=lambda v: self.antiflash_delay_label.configure(text=f"{float(v):.1f}")).pack(fill="x")

        # Anti-flash confidence threshold
        f_af2 = ttk.Frame(right); f_af2.pack(fill="x", pady=2)
        ttk.Label(f_af2, text="闪光置信度:").pack(side="left")
        self.antiflash_conf_label = ttk.Label(f_af2, text=f"{self.antiflash_conf_var.get():.2f}")
        self.antiflash_conf_label.pack(side="right")
        tk.Scale(right, from_=0.1, to=1.0, orient="horizontal", variable=self.antiflash_conf_var,
                 resolution=0.05, command=lambda v: self.antiflash_conf_label.configure(text=f"{float(v):.2f}")).pack(fill="x")
        ttk.Label(right, text="需要模型类别含\"闪\"字, 使用CS2灵敏度计算转身", font=("", 8)).pack(anchor="w")

        # ===== Switch to DETECT tab (color section) =====
        right = tab_detect

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # --- Color detection mode (找色模式) ---
        ttk.Label(right, text="── 找色模式 (Color Aim) ──", font=("", 9, "bold")).pack(anchor="w", pady=(4, 2))
        ttk.Checkbutton(right, text="启用找色模式 (关闭AI识别)", variable=self.color_mode_var).pack(anchor="w")
        ttk.Label(right, text="用颜色检测代替AI, 帧率极高, 适合有高亮轮廓的游戏", font=("", 8)).pack(anchor="w")

        # Color preset
        f_cp = ttk.Frame(right); f_cp.pack(fill="x", pady=2)
        ttk.Label(f_cp, text="颜色预设:").pack(side="left")
        color_combo = ttk.Combobox(f_cp, textvariable=self.color_preset_var,
                                    values=list(self._color_presets.keys()),
                                    state="readonly", width=18)
        color_combo.pack(side="right")
        color_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_color_preset())

        # HSV sliders
        self._color_hsv_frame = ttk.LabelFrame(right, text="HSV 范围")
        self._color_hsv_frame.pack(fill="x", pady=2)
        # H low/high
        f_h = ttk.Frame(self._color_hsv_frame); f_h.pack(fill="x")
        ttk.Label(f_h, text="H:", width=3).pack(side="left")
        tk.Scale(f_h, from_=0, to=179, orient="horizontal", variable=self.color_h_low_var, length=80).pack(side="left", expand=True, fill="x")
        ttk.Label(f_h, text="~").pack(side="left")
        tk.Scale(f_h, from_=0, to=179, orient="horizontal", variable=self.color_h_high_var, length=80).pack(side="left", expand=True, fill="x")
        # S low/high
        f_s = ttk.Frame(self._color_hsv_frame); f_s.pack(fill="x")
        ttk.Label(f_s, text="S:", width=3).pack(side="left")
        tk.Scale(f_s, from_=0, to=255, orient="horizontal", variable=self.color_s_low_var, length=80).pack(side="left", expand=True, fill="x")
        ttk.Label(f_s, text="~").pack(side="left")
        tk.Scale(f_s, from_=0, to=255, orient="horizontal", variable=self.color_s_high_var, length=80).pack(side="left", expand=True, fill="x")
        # V low/high
        f_v = ttk.Frame(self._color_hsv_frame); f_v.pack(fill="x")
        ttk.Label(f_v, text="V:", width=3).pack(side="left")
        tk.Scale(f_v, from_=0, to=255, orient="horizontal", variable=self.color_v_low_var, length=80).pack(side="left", expand=True, fill="x")
        ttk.Label(f_v, text="~").pack(side="left")
        tk.Scale(f_v, from_=0, to=255, orient="horizontal", variable=self.color_v_high_var, length=80).pack(side="left", expand=True, fill="x")

        # Color smooth factor
        f_cs = ttk.Frame(right); f_cs.pack(fill="x", pady=2)
        ttk.Label(f_cs, text="找色平滑:").pack(side="left")
        self.color_smooth_label = ttk.Label(f_cs, text=f"{self.color_smooth_var.get():.2f}")
        self.color_smooth_label.pack(side="right")
        tk.Scale(right, from_=0.05, to=1.0, orient="horizontal", variable=self.color_smooth_var,
                 resolution=0.05, command=lambda v: self.color_smooth_label.configure(text=f"{float(v):.2f}")).pack(fill="x")

        # Min contour area
        f_ca = ttk.Frame(right); f_ca.pack(fill="x", pady=2)
        ttk.Label(f_ca, text="最小面积:").pack(side="left")
        self.color_area_label = ttk.Label(f_ca, text=str(self.color_min_area_var.get()))
        self.color_area_label.pack(side="right")
        tk.Scale(right, from_=5, to=500, orient="horizontal", variable=self.color_min_area_var,
                 resolution=5, command=lambda v: self.color_area_label.configure(text=str(int(float(v))))).pack(fill="x")
        ttk.Label(right, text="平滑=鼠标跟随速度 面积=过滤小噪点", font=("", 8)).pack(anchor="w")

        # ===== Switch to SYSTEM tab =====
        right = tab_system

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # --- Toggle Hotkeys & Audio ---
        ttk.Label(right, text="── 快捷开关 ──", font=("", 9, "bold")).pack(anchor="w", pady=(4, 2))

        # Aim toggle hotkey
        f_hk1 = ttk.Frame(right); f_hk1.pack(fill="x", pady=2)
        ttk.Label(f_hk1, text="自瞄开关键:").pack(side="left")
        ttk.Combobox(f_hk1, textvariable=self.aim_toggle_key_var, values=list(HOTKEY_OPTIONS.keys()),
                     state="readonly", width=12).pack(side="right")

        # Recoil toggle hotkey
        f_hk2 = ttk.Frame(right); f_hk2.pack(fill="x", pady=2)
        ttk.Label(f_hk2, text="压枪开关键:").pack(side="left")
        ttk.Combobox(f_hk2, textvariable=self.recoil_toggle_key_var, values=list(HOTKEY_OPTIONS.keys()),
                     state="readonly", width=12).pack(side="right")

        # Trigger toggle hotkey
        f_hk3 = ttk.Frame(right); f_hk3.pack(fill="x", pady=2)
        ttk.Label(f_hk3, text="扳机开关键:").pack(side="left")
        ttk.Combobox(f_hk3, textvariable=self.trigger_toggle_key_var, values=list(HOTKEY_OPTIONS.keys()),
                     state="readonly", width=12).pack(side="right")

        ttk.Label(right, text="按一次开/再按一次关 (实时生效)", font=("", 8)).pack(anchor="w")

        # Voice notification toggle
        ttk.Checkbutton(right, text="语音播报 (关闭则只播放音效)", variable=self.voice_enabled_var).pack(anchor="w", pady=2)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # Save button
        ttk.Button(right, text="保存配置 (config.py + 当前方案)", command=self.save_config).pack(fill="x", pady=4)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -------------------------------------------------- status label updater
    def _update_status_labels(self):
        """Update aim/recoil status labels. Called by checkbutton command and periodically."""
        if self.aim_enabled_var.get():
            self.aim_status_label.config(text="已开启", fg="green")
        else:
            self.aim_status_label.config(text="已关闭", fg="red")
        if self.recoil_enabled_var.get():
            self.recoil_status_label.config(text="已开启", fg="green")
        else:
            self.recoil_status_label.config(text="已关闭", fg="red")
        if self.trigger_enabled_var.get():
            self.trigger_status_label.config(text="已开启", fg="green")
        else:
            self.trigger_status_label.config(text="已关闭", fg="red")
        # Re-schedule every 200ms to catch hotkey toggles
        self.root.after(200, self._update_status_labels)

    # -------------------------------------------------- key polling thread
    def _key_poll_loop(self):
        """Background thread: poll aim key + recoil key + toggle hotkeys at ~1000 Hz."""
        prev_aim_toggle = False
        prev_rc_toggle = False
        prev_trig_toggle = False
        while self._key_poll_running:
            try:
                if win32api is None:
                    time.sleep(0.01)
                    continue
                # Aim activation key (primary OR secondary)
                cur_key = KEY_OPTIONS.get(self.key_var.get(), 0x02)
                key1_down = bool(win32api.GetAsyncKeyState(cur_key) & 0x8000)
                cur_key2 = KEY2_OPTIONS.get(self.key2_var.get(), 0x00)
                key2_down = bool(win32api.GetAsyncKeyState(cur_key2) & 0x8000) if cur_key2 != 0 else False
                self._key_is_down = key1_down or key2_down
                # Recoil trigger key
                rc_key = KEY_OPTIONS.get(self.recoil_key_var.get(), 0x01)
                self._recoil_key_is_down = bool(win32api.GetAsyncKeyState(rc_key) & 0x8000)

                # --- Toggle hotkeys (edge-triggered, via GetAsyncKeyState) ---
                aim_hk = HOTKEY_OPTIONS.get(self.aim_toggle_key_var.get(), 0x74)
                aim_hk_down = bool(win32api.GetAsyncKeyState(aim_hk) & 0x8000)
                if aim_hk_down and not prev_aim_toggle:
                    new_val = not self.aim_enabled_var.get()
                    self.root.after(0, self.aim_enabled_var.set, new_val)
                    voice = self.voice_enabled_var.get()
                    _play_notification("开启自瞄" if new_val else "关闭自瞄", voice, on=new_val)
                prev_aim_toggle = aim_hk_down

                rc_hk = HOTKEY_OPTIONS.get(self.recoil_toggle_key_var.get(), 0x75)
                rc_hk_down = bool(win32api.GetAsyncKeyState(rc_hk) & 0x8000)
                if rc_hk_down and not prev_rc_toggle:
                    new_val = not self.recoil_enabled_var.get()
                    self.root.after(0, self.recoil_enabled_var.set, new_val)
                    voice = self.voice_enabled_var.get()
                    _play_notification("开启压枪" if new_val else "关闭压枪", voice, on=new_val)
                prev_rc_toggle = rc_hk_down

                trig_hk = HOTKEY_OPTIONS.get(self.trigger_toggle_key_var.get(), 0x76)
                trig_hk_down = bool(win32api.GetAsyncKeyState(trig_hk) & 0x8000)
                if trig_hk_down and not prev_trig_toggle:
                    new_val = not self.trigger_enabled_var.get()
                    self.root.after(0, self.trigger_enabled_var.set, new_val)
                    voice = self.voice_enabled_var.get()
                    _play_notification("开启扳机" if new_val else "关闭扳机", voice, on=new_val)
                prev_trig_toggle = trig_hk_down

            except Exception:
                self._key_is_down = False
                self._recoil_key_is_down = False
            time.sleep(0.001)  # 1ms = ~1000 Hz polling

    # ------------------------------------------------ rigid recoil thread
    def _on_rigid_toggle(self):
        """Called when the rigid recoil checkbox is toggled."""
        if self.rigid_recoil_var.get():
            self._start_rigid_recoil()
        else:
            self._stop_rigid_recoil()

    def _start_rigid_recoil(self):
        """Start the dedicated rigid recoil thread."""
        if self._rigid_recoil_running:
            return
        self._rigid_recoil_running = True
        self._rigid_recoil_thread = threading.Thread(target=self._rigid_recoil_loop, daemon=True)
        self._rigid_recoil_thread.start()
        print("[RIGID RECOIL] Thread started")

    def _stop_rigid_recoil(self):
        """Stop the dedicated rigid recoil thread."""
        self._rigid_recoil_running = False
        self._rigid_spray_active = False
        if self._rigid_recoil_thread is not None:
            self._rigid_recoil_thread.join(timeout=1.0)
            self._rigid_recoil_thread = None
        print("[RIGID RECOIL] Thread stopped")

    @staticmethod
    def _send_input_move(dx, dy):
        """Use Windows SendInput API for lowest-latency mouse movement."""
        inp = _INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.mi.dx = int(dx)
        inp.mi.dy = int(dy)
        inp.mi.dwFlags = _MOUSEEVENTF_MOVE
        inp.mi.time = 0
        inp.mi.mouseData = 0
        inp.mi.dwExtraInfo = None
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def _antiflash_execute(self, delay_sec):
        """Anti-flash thread: turn 180°, wait, turn back. Runs in background thread."""
        try:
            self._antiflash_active = True
            sens = self.cs2_sensitivity_var.get()
            # CS2: mouse_counts = degrees / (sensitivity * 0.022)
            turn_counts = int(180.0 / (sens * 0.022))
            print(f"[ANTI-FLASH] Triggered! sens={sens:.2f} turn={turn_counts}px delay={delay_sec:.1f}s")

            # Split the large move into chunks to avoid driver limits on single mouse_event
            chunk = 500  # max pixels per single move call
            remaining = turn_counts
            while remaining > 0:
                move = min(remaining, chunk)
                self._send_input_move(move, 0)
                remaining -= move
                if remaining > 0:
                    time.sleep(0.001)

            # Wait while turned away
            time.sleep(delay_sec)

            # Turn back (negative direction, same magnitude)
            remaining = turn_counts
            while remaining > 0:
                move = min(remaining, chunk)
                self._send_input_move(-move, 0)
                remaining -= move
                if remaining > 0:
                    time.sleep(0.001)

            print(f"[ANTI-FLASH] Returned to original angle")
        except Exception as e:
            print(f"[ANTI-FLASH] Error: {e}")
        finally:
            self._antiflash_active = False
            # Cooldown: don't trigger again for (delay + 1) seconds
            self._antiflash_cooldown_until = time.perf_counter() + delay_sec + 1.0

    def _rigid_recoil_loop(self):
        """
        Dedicated rigid recoil thread — FullExternal-style with custom smoothness.
        Uses SendInput for lowest latency mouse moves.
        Fixes integer truncation by accumulating float remainders.
        """
        count = 0  # current bullet index (0 = idle, 1..size-1 = active)
        _rigid_press_t = 0.0  # when recoil key was first pressed (for hold threshold)

        while self._rigid_recoil_running:
            # Check if rigid recoil is enabled
            if not self.recoil_enabled_var.get():
                self._rigid_spray_active = False
                time.sleep(0.05)
                continue

            weapon = self.rigid_weapon_var.get()
            if weapon == "关闭 (Off)" or weapon not in RIGID_WEAPON_NAMES:
                self._rigid_spray_active = False
                time.sleep(0.05)
                continue

            sens = self.cs2_sensitivity_var.get()
            X, Y, size = get_rigid_weapon_data(weapon, sens)

            if X is None or size == 0:
                self._rigid_spray_active = False
                time.sleep(0.05)
                continue

            # Read smoothness params from GUI (live)
            steps = max(self.rigid_steps_var.get(), 1)
            delay1_ms = max(self.rigid_delay1_var.get(), 1)  # ms between sub-steps
            delay2_ms = max(self.rigid_delay2_var.get(), 0)  # ms after all sub-steps

            # Read recoil trigger key
            rc_key = KEY_OPTIONS.get(self.recoil_key_var.get(), 0x01)

            # Aim-only gate: if enabled, require aim key to be held for recoil
            rc_aim_only = self.recoil_aim_only_var.get()
            aim_key_held = self._key_is_down

            # Check if trigger key is NOT held — reset spray
            rc_hold_ms = self.recoil_hold_ms_var.get()
            key_held = (win32api is not None and win32api.GetAsyncKeyState(rc_key) < 0)
            if rc_aim_only and not aim_key_held:
                key_held = False  # suppress recoil when aim key not held
            if not key_held:
                if count != 0:
                    count = 0
                    self._rigid_spray_active = False
                _rigid_press_t = 0.0
                time.sleep(0.001)
                continue

            # Key IS held — track hold duration for tap-vs-hold detection
            if _rigid_press_t == 0.0:
                _rigid_press_t = time.perf_counter()
            held_dur_ms = (time.perf_counter() - _rigid_press_t) * 1000.0
            if held_dur_ms < rc_hold_ms:
                # Still in tap zone — don't start spraying yet
                time.sleep(0.001)
                continue

            # Trigger key IS held long enough — check magazine end
            if count >= size - 1:
                count = 0
                self._rigid_spray_active = False
                time.sleep(0.5)
                continue

            # Advance to next bullet
            count += 1
            self._rigid_spray_active = True

            # --- Sub-step movement with float accumulation (fixes truncation) ---
            # Total float movement for this bullet
            total_fx = X[count]
            total_fy = Y[count]
            accum_x = 0.0
            accum_y = 0.0

            for i in range(steps):
                time.sleep(delay1_ms / 1000.0)
                # Calculate ideal accumulated movement after this sub-step
                ideal_x = total_fx * (i + 1) / steps
                ideal_y = total_fy * (i + 1) / steps
                # Integer delta = ideal - already sent
                dx = round(ideal_x - accum_x)
                dy = round(ideal_y - accum_y)
                if dx != 0 or dy != 0:
                    self._send_input_move(dx, dy)
                accum_x += dx
                accum_y += dy

            if delay2_ms > 0:
                time.sleep(delay2_ms / 1000.0)

        self._rigid_spray_active = False

    # -------------------------------------------------------- config helpers
    def _gather_config_vals(self):
        """Collect current GUI values into a dict (used for save & profile)."""
        # Validate combobox values before gathering — warn if any would fall back to default
        _checks = [
            ("aaAimMode", self.aim_mode_var.get(), AIM_MODE_OPTIONS),
            ("aaTargetPart", self.target_var.get(), TARGET_OPTIONS),
            ("aaActivateKey", self.key_var.get(), KEY_OPTIONS),
            ("aaSecondaryKey", self.key2_var.get(), KEY2_OPTIONS),
            ("recoilKey", self.recoil_key_var.get(), KEY_OPTIONS),
            ("aaTeamFilter", self.team_var.get(), TEAM_OPTIONS),
            ("aimToggleKey", self.aim_toggle_key_var.get(), HOTKEY_OPTIONS),
            ("recoilToggleKey", self.recoil_toggle_key_var.get(), HOTKEY_OPTIONS),
            ("triggerToggleKey", self.trigger_toggle_key_var.get(), HOTKEY_OPTIONS),
        ]
        for cfg_name, gui_val, options_dict in _checks:
            if gui_val not in options_dict:
                print(f"[CONFIG WARNING] {cfg_name}: GUI value {gui_val!r} not in options, will use fallback default!")
        return {
            "aaFOV": self.fov_var.get(),
            "aaXOnly": self.aim_x_only_var.get(),
            "aaXLockDuration": self.aim_x_lock_duration_var.get(),
            "aaAlwaysAim": self.aim_always_var.get(),
            "aaAdaptive": self.aim_adaptive_var.get(),
            "aaAdaptiveMax": round(self.aim_adaptive_max_var.get(), 1),
            "aaTargetLock": self.aim_target_lock_var.get(),
            "aaTargetLockFrames": self.aim_target_lock_frames_var.get(),
            "aaTargetLockRadius": self.aim_target_lock_radius_var.get(),
            "aaKi": round(self.aim_ki_var.get(), 3),
            "aaKd": round(self.aim_kd_var.get(), 3),
            "ovBoxThickness": self.ov_box_thickness_var.get(),
            "ovBoxStyle": self.ov_box_style_var.get(),
            "ovCornerLen": self.ov_corner_len_var.get(),
            "ovDot": self.ov_dot_var.get(),
            "ovDotSize": self.ov_dot_size_var.get(),
            "ovDotStyle": self.ov_dot_style_var.get(),
            "ovDotColor": self.ov_dot_color_var.get(),
            "ovHideLabel": self.ov_hide_label_var.get(),
            "aaAimMode": AIM_MODE_OPTIONS.get(self.aim_mode_var.get(), "aimbot"),
            "aaTargetPart": TARGET_OPTIONS.get(self.target_var.get(), "head"),
            "aaSmoothFactor": round(self.smooth_var.get(), 1),
            "aaActivateKey": KEY_OPTIONS.get(self.key_var.get(), 0x02),
            "aaSecondaryKey": KEY2_OPTIONS.get(self.key2_var.get(), 0x00),
            "aaMovementAmp": round(self.amp_var.get(), 2),
            "confidence": round(self.conf_var.get(), 2),
            "crosshairYOffset": self.crosshair_y_offset_var.get(),
            "captureFPS": self.fps_var.get(),
            "screenShotHeight": self.screenshot_size_var.get(),
            "screenShotWidth": self.screenshot_size_var.get(),
            "recoilWeapon": self.recoil_weapon_var.get(),
            "recoilStrength": round(self.recoil_strength_var.get(), 2),
            "recoilSmooth": self.recoil_smooth_var.get(),
            "recoilTimeOffset": self.recoil_time_offset_var.get(),
            "recoilKey": KEY_OPTIONS.get(self.recoil_key_var.get(), 0x01),
            "recoilAimOnly": self.recoil_aim_only_var.get(),
            "recoilHoldMs": self.recoil_hold_ms_var.get(),
            "visuals": self.visuals_var.get(),
            "showOverlay": self.overlay_var.get(),
            "aaTeamFilter": TEAM_OPTIONS.get(self.team_var.get(), "all"),
            "aimToggleKey": HOTKEY_OPTIONS.get(self.aim_toggle_key_var.get(), 0x74),
            "recoilToggleKey": HOTKEY_OPTIONS.get(self.recoil_toggle_key_var.get(), 0x75),
            "triggerDelay": self.trigger_delay_var.get(),
            "triggerToggleKey": HOTKEY_OPTIONS.get(self.trigger_toggle_key_var.get(), 0x76),
            "selectedModel": self.model_var.get(),
            # Rigid recoil
            "rigidWeapon": self.rigid_weapon_var.get(),
            "cs2Sensitivity": round(self.cs2_sensitivity_var.get(), 2),
            "rigidSteps": self.rigid_steps_var.get(),
            "rigidDelay1": self.rigid_delay1_var.get(),
            "rigidDelay2": self.rigid_delay2_var.get(),
            # Toggle states (profile-only, not written to config.py)
            "aimEnabled": self.aim_enabled_var.get(),
            "recoilEnabled": self.recoil_enabled_var.get(),
            "triggerEnabled": self.trigger_enabled_var.get(),
            "voiceEnabled": self.voice_enabled_var.get(),
            "rigidRecoilEnabled": self.rigid_recoil_var.get(),
            "rigidAiCorrect": self.rigid_ai_correct_var.get(),
            # Anti-flash
            "antiflashEnabled": self.antiflash_enabled_var.get(),
            "antiflashDelay": round(self.antiflash_delay_var.get(), 1),
            "antiflashConf": round(self.antiflash_conf_var.get(), 2),
            # Color detection mode
            "colorModeEnabled": self.color_mode_var.get(),
            "colorPreset": self.color_preset_var.get(),
            "colorHLow": self.color_h_low_var.get(),
            "colorSLow": self.color_s_low_var.get(),
            "colorVLow": self.color_v_low_var.get(),
            "colorHHigh": self.color_h_high_var.get(),
            "colorSHigh": self.color_s_high_var.get(),
            "colorVHigh": self.color_v_high_var.get(),
            "colorSmooth": round(self.color_smooth_var.get(), 2),
            "colorMinArea": self.color_min_area_var.get(),
        }

    def _apply_config_vals(self, vals: dict):
        """Apply a config dict to all GUI variables."""
        # Log key values being applied for debugging config corruption
        _track = ["aaAimMode", "aaActivateKey", "aaSecondaryKey", "aaTargetPart", "aaTeamFilter"]
        for k in _track:
            if k in vals:
                print(f"[CONFIG APPLY] {k} = {vals[k]!r}")
        if "aaFOV" in vals:
            self.fov_var.set(int(vals["aaFOV"]))
        if "aaXOnly" in vals:
            self.aim_x_only_var.set(bool(vals["aaXOnly"]))
        if "aaXLockDuration" in vals:
            self.aim_x_lock_duration_var.set(int(vals["aaXLockDuration"]))
        if "aaAlwaysAim" in vals:
            self.aim_always_var.set(bool(vals["aaAlwaysAim"]))
        if "aaAdaptive" in vals:
            self.aim_adaptive_var.set(bool(vals["aaAdaptive"]))
        if "aaAdaptiveMax" in vals:
            self.aim_adaptive_max_var.set(float(vals["aaAdaptiveMax"]))
        if "aaTargetLock" in vals:
            self.aim_target_lock_var.set(bool(vals["aaTargetLock"]))
        if "aaTargetLockFrames" in vals:
            self.aim_target_lock_frames_var.set(int(vals["aaTargetLockFrames"]))
        if "aaTargetLockRadius" in vals:
            self.aim_target_lock_radius_var.set(int(vals["aaTargetLockRadius"]))
        if "aaKi" in vals:
            self.aim_ki_var.set(float(vals["aaKi"]))
        if "aaKd" in vals:
            self.aim_kd_var.set(float(vals["aaKd"]))
        if "ovBoxThickness" in vals:
            self.ov_box_thickness_var.set(int(vals["ovBoxThickness"]))
        if "ovBoxStyle" in vals:
            self.ov_box_style_var.set(str(vals["ovBoxStyle"]))
        if "ovCornerLen" in vals:
            self.ov_corner_len_var.set(int(vals["ovCornerLen"]))
        if "ovDot" in vals:
            self.ov_dot_var.set(bool(vals["ovDot"]))
        if "ovDotSize" in vals:
            self.ov_dot_size_var.set(int(vals["ovDotSize"]))
        if "ovDotStyle" in vals:
            self.ov_dot_style_var.set(str(vals["ovDotStyle"]))
        if "ovDotColor" in vals:
            self.ov_dot_color_var.set(str(vals["ovDotColor"]))
        if "ovHideLabel" in vals:
            self.ov_hide_label_var.set(bool(vals["ovHideLabel"]))
        if "aaTargetPart" in vals:
            v = vals["aaTargetPart"]
            self.target_var.set(TARGET_VALUE_TO_NAME.get(v, "头部 (Head)"))
        if "aaAimMode" in vals:
            v = vals["aaAimMode"]
            self.aim_mode_var.set(AIM_MODE_VALUE_TO_NAME.get(v, "自瞄 (Aimbot)"))
        if "aaSmoothFactor" in vals:
            self.smooth_var.set(float(vals["aaSmoothFactor"]))
        if "aaActivateKey" in vals:
            self.key_var.set(KEY_CODE_TO_NAME.get(int(vals["aaActivateKey"]), "鼠标右键 (Right Click)"))
        if "aaSecondaryKey" in vals:
            self.key2_var.set(KEY2_CODE_TO_NAME.get(int(vals["aaSecondaryKey"]), "禁用 (Off)"))
        if "aaMovementAmp" in vals:
            self.amp_var.set(float(vals["aaMovementAmp"]))
        if "confidence" in vals:
            self.conf_var.set(float(vals["confidence"]))
        if "crosshairYOffset" in vals:
            self.crosshair_y_offset_var.set(int(vals["crosshairYOffset"]))
        if "captureFPS" in vals:
            self.fps_var.set(int(vals["captureFPS"]))
        if "screenShotHeight" in vals:
            self.screenshot_size_var.set(int(vals["screenShotHeight"]))
        if "recoilWeapon" in vals:
            self.recoil_weapon_var.set(vals["recoilWeapon"])
        if "recoilStrength" in vals:
            self.recoil_strength_var.set(float(vals["recoilStrength"]))
        if "recoilSmooth" in vals:
            self.recoil_smooth_var.set(int(vals["recoilSmooth"]))
        if "recoilTimeOffset" in vals:
            self.recoil_time_offset_var.set(int(vals["recoilTimeOffset"]))
        if "recoilKey" in vals:
            self.recoil_key_var.set(KEY_CODE_TO_NAME.get(int(vals["recoilKey"]), "鼠标左键 (Left Click)"))
        if "recoilAimOnly" in vals:
            self.recoil_aim_only_var.set(bool(vals["recoilAimOnly"]))
        if "recoilHoldMs" in vals:
            self.recoil_hold_ms_var.set(int(vals["recoilHoldMs"]))
        if "visuals" in vals:
            self.visuals_var.set(bool(vals["visuals"]))
        if "showOverlay" in vals:
            self.overlay_var.set(bool(vals["showOverlay"]))
        if "aaTeamFilter" in vals:
            v = vals["aaTeamFilter"]
            self.team_var.set(TEAM_VALUE_TO_NAME.get(v, "全部目标 (All)"))
        if "aimToggleKey" in vals:
            self.aim_toggle_key_var.set(HOTKEY_CODE_TO_NAME.get(int(vals["aimToggleKey"]), "F5"))
        if "recoilToggleKey" in vals:
            self.recoil_toggle_key_var.set(HOTKEY_CODE_TO_NAME.get(int(vals["recoilToggleKey"]), "F6"))
        if "triggerDelay" in vals:
            self.trigger_delay_var.set(int(vals["triggerDelay"]))
        if "triggerToggleKey" in vals:
            self.trigger_toggle_key_var.set(HOTKEY_CODE_TO_NAME.get(int(vals["triggerToggleKey"]), "F7"))
        if "selectedModel" in vals:
            m = vals["selectedModel"]
            if m in self.available_models:
                self.model_var.set(m)
        # Rigid recoil
        if "rigidWeapon" in vals:
            self.rigid_weapon_var.set(vals["rigidWeapon"])
        if "cs2Sensitivity" in vals:
            self.cs2_sensitivity_var.set(float(vals["cs2Sensitivity"]))
        if "rigidSteps" in vals:
            self.rigid_steps_var.set(int(vals["rigidSteps"]))
        if "rigidDelay1" in vals:
            self.rigid_delay1_var.set(int(vals["rigidDelay1"]))
        if "rigidDelay2" in vals:
            self.rigid_delay2_var.set(int(vals["rigidDelay2"]))
        if "rigidAiCorrect" in vals:
            self.rigid_ai_correct_var.set(bool(vals["rigidAiCorrect"]))
        # Anti-flash
        if "antiflashEnabled" in vals:
            self.antiflash_enabled_var.set(bool(vals["antiflashEnabled"]))
        if "antiflashDelay" in vals:
            self.antiflash_delay_var.set(float(vals["antiflashDelay"]))
        if "antiflashConf" in vals:
            self.antiflash_conf_var.set(float(vals["antiflashConf"]))
        # Color detection mode
        if "colorModeEnabled" in vals:
            self.color_mode_var.set(bool(vals["colorModeEnabled"]))
        if "colorPreset" in vals:
            self.color_preset_var.set(str(vals["colorPreset"]))
        if "colorHLow" in vals:
            self.color_h_low_var.set(int(vals["colorHLow"]))
        if "colorSLow" in vals:
            self.color_s_low_var.set(int(vals["colorSLow"]))
        if "colorVLow" in vals:
            self.color_v_low_var.set(int(vals["colorVLow"]))
        if "colorHHigh" in vals:
            self.color_h_high_var.set(int(vals["colorHHigh"]))
        if "colorSHigh" in vals:
            self.color_s_high_var.set(int(vals["colorSHigh"]))
        if "colorVHigh" in vals:
            self.color_v_high_var.set(int(vals["colorVHigh"]))
        if "colorSmooth" in vals:
            self.color_smooth_var.set(float(vals["colorSmooth"]))
        if "colorMinArea" in vals:
            self.color_min_area_var.set(int(vals["colorMinArea"]))
        # Toggle states
        if "aimEnabled" in vals:
            self.aim_enabled_var.set(bool(vals["aimEnabled"]))
        if "recoilEnabled" in vals:
            self.recoil_enabled_var.set(bool(vals["recoilEnabled"]))
        if "triggerEnabled" in vals:
            self.trigger_enabled_var.set(bool(vals["triggerEnabled"]))
        if "voiceEnabled" in vals:
            self.voice_enabled_var.set(bool(vals["voiceEnabled"]))
        if "rigidRecoilEnabled" in vals:
            new_rigid = bool(vals["rigidRecoilEnabled"])
            self.rigid_recoil_var.set(new_rigid)
            if new_rigid:
                self._start_rigid_recoil()
            else:
                self._stop_rigid_recoil()
        self._update_status_labels()

    # -------------------------------------------------------- config save
    # Profile-only keys (not written to config.py)
    _PROFILE_ONLY_KEYS = {"aimEnabled", "recoilEnabled", "triggerEnabled", "voiceEnabled", "rigidRecoilEnabled", "rigidAiCorrect", "antiflashEnabled", "colorModeEnabled"}

    def save_config(self):
        vals = self._gather_config_vals()
        try:
            config_vals = {k: v for k, v in vals.items() if k not in self._PROFILE_ONLY_KEYS}
            save_config_values(config_vals)
            # Also save to current profile if one is selected
            prof_name = self.profile_var.get()
            if prof_name:
                save_profile(prof_name, vals)
                msg = f"配置已保存到 config.py + 方案「{prof_name}」\n(当前运行中的设置已实时生效，无需重启)"
            else:
                msg = "配置已保存到 config.py\n(当前运行中的设置已实时生效，无需重启)\n\n提示: 未选择方案，仅保存到 config.py"
            messagebox.showinfo("成功", msg)
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    # -------------------------------------------------- profile management
    def _refresh_profile_list(self):
        """Refresh the profile combobox values."""
        self.profile_combo["values"] = list_profiles()

    def _load_profile(self):
        name = self.profile_var.get()
        if not name:
            messagebox.showwarning("未选择", "请先选择一个配置方案。")
            return
        vals = load_profile(name)
        if not vals:
            messagebox.showerror("错误", f"方案「{name}」加载失败或为空。")
            return
        self._apply_config_vals(vals)
        # Write the GUI-applied values (not raw profile) to config.py
        # This ensures values go through the proper mapping round-trip
        try:
            fresh_vals = self._gather_config_vals()
            config_vals = {k: v for k, v in fresh_vals.items() if k not in self._PROFILE_ONLY_KEYS}
            save_config_values(config_vals)
        except Exception:
            pass
        # Remember as last used
        try:
            with open(LAST_PROFILE_PATH, "w", encoding="utf-8") as f:
                f.write(name)
        except Exception:
            pass
        messagebox.showinfo("成功", f"已加载方案「{name}」\n所有设置已实时生效。")

    def _new_profile(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("新建配置方案", "请输入方案名称 (如 CS2, CF, Valorant):",
                                       parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        # Check for invalid characters
        if any(c in name for c in r'\/:*?"<>|'):
            messagebox.showerror("错误", "方案名称不能包含特殊字符: \\ / : * ? \" < > |")
            return
        if name in list_profiles():
            if not messagebox.askyesno("覆盖确认", f"方案「{name}」已存在，是否覆盖？"):
                return
        vals = self._gather_config_vals()
        try:
            save_profile(name, vals)
            self._refresh_profile_list()
            self.profile_var.set(name)
            messagebox.showinfo("成功", f"方案「{name}」已创建并保存当前设置。")
        except Exception as e:
            messagebox.showerror("错误", f"创建失败: {e}")

    def _delete_profile(self):
        name = self.profile_var.get()
        if not name:
            messagebox.showwarning("未选择", "请先选择一个配置方案。")
            return
        if not messagebox.askyesno("确认删除", f"确定要删除方案「{name}」吗？\n此操作不可撤销。"):
            return
        try:
            delete_profile(name)
            self._refresh_profile_list()
            self.profile_var.set("")
            messagebox.showinfo("成功", f"方案「{name}」已删除。")
        except Exception as e:
            messagebox.showerror("错误", f"删除失败: {e}")

    def _rename_profile(self):
        old_name = self.profile_var.get()
        if not old_name:
            messagebox.showwarning("未选择", "请先选择一个配置方案。")
            return
        from tkinter import simpledialog
        new_name = simpledialog.askstring("重命名方案", f"将「{old_name}」重命名为:",
                                           parent=self.root, initialvalue=old_name)
        if not new_name or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()
        if any(c in new_name for c in r'\/:*?"<>|'):
            messagebox.showerror("错误", "方案名称不能包含特殊字符: \\ / : * ? \" < > |")
            return
        if new_name in list_profiles():
            messagebox.showerror("错误", f"方案「{new_name}」已存在。")
            return
        try:
            vals = load_profile(old_name)
            save_profile(new_name, vals)
            delete_profile(old_name)
            self._refresh_profile_list()
            self.profile_var.set(new_name)
            messagebox.showinfo("成功", f"方案已重命名: 「{old_name}」→「{new_name}」")
        except Exception as e:
            messagebox.showerror("错误", f"重命名失败: {e}")

    def _apply_color_preset(self):
        """Apply a color preset's HSV values to the sliders."""
        name = self.color_preset_var.get()
        preset = self._color_presets.get(name)
        if preset is None:
            return  # Custom — user sets manually
        self.color_h_low_var.set(preset["lower"][0])
        self.color_s_low_var.set(preset["lower"][1])
        self.color_v_low_var.set(preset["lower"][2])
        self.color_h_high_var.set(preset["upper"][0])
        self.color_s_high_var.set(preset["upper"][1])
        self.color_v_high_var.set(preset["upper"][2])

    # ------------------------------------------------------- window list (legacy stubs)
    def refresh_windows(self):
        pass

    def get_selected_window(self):
        return None

    # --------------------------------------------------------- model
    def _get_selected_model_path(self):
        name = self.model_var.get()
        return self.available_models.get(name, "")

    def _read_model_class_names(self, session):
        """Read class names from ONNX model metadata. Returns dict {id: name} or empty."""
        try:
            import ast as _ast
            meta = session.get_modelmeta().custom_metadata_map
            if 'names' in meta:
                cls_names = _ast.literal_eval(meta['names'])
                if isinstance(cls_names, dict):
                    # Ensure keys are int
                    return {int(k): str(v) for k, v in cls_names.items()}
        except Exception:
            pass
        return {}

    def _update_class_filter_ui(self, class_names):
        """Rebuild the class filter checkboxes from class_names dict {id: name}.
        Called on the main thread after a model is loaded."""
        self._model_class_names = class_names
        # Clear old checkboxes
        for w in self._class_filter_frame.winfo_children():
            w.destroy()
        self._class_filter_vars = {}

        if not class_names:
            ttk.Label(self._class_filter_frame, text="(模型无类别元数据)", foreground="gray").pack(anchor="w")
            return

        # Build checkboxes vertically (one per row)
        for cls_id in sorted(class_names.keys()):
            name = class_names[cls_id]
            var = tk.BooleanVar(value=True)
            self._class_filter_vars[cls_id] = var
            ttk.Checkbutton(self._class_filter_frame, text=f"{cls_id}: {name}",
                            variable=var).pack(anchor="w", padx=4)

        # Select All / Deselect All buttons
        btn_row = ttk.Frame(self._class_filter_frame)
        btn_row.pack(fill="x", padx=2, pady=(2, 2))
        ttk.Button(btn_row, text="全选", width=6,
                   command=lambda: [v.set(True) for v in self._class_filter_vars.values()]).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row, text="全不选", width=6,
                   command=lambda: [v.set(False) for v in self._class_filter_vars.values()]).pack(side="left")

    def _get_enabled_class_ids(self):
        """Return set of enabled class IDs, or None if no filter (all enabled or no metadata)."""
        if not self._class_filter_vars:
            return None  # No metadata = accept all
        enabled = {cid for cid, var in self._class_filter_vars.items() if var.get()}
        if len(enabled) == len(self._class_filter_vars):
            return None  # All enabled = no filter needed
        return enabled

    def _refresh_models(self):
        self.available_models = scan_onnx_models()
        names = list(self.available_models.keys())
        self.model_combo["values"] = names
        if self.model_var.get() not in names and names:
            self.model_var.set(names[0])
        self.model_status_label.configure(text=f"找到 {len(names)} 个模型", foreground="gray")

    def _switch_model(self):
        path = self._get_selected_model_path()
        if not path:
            messagebox.showwarning("未选择", "请先选择一个模型。")
            return
        try:
            self.model_status_label.configure(text="正在加载...", foreground="orange")
            self.root.update_idletasks()
            new_model = self._create_onnx_session(path)
            # Read model expected input size (handle dynamic axes from YOLOv11)
            inp = new_model.get_inputs()[0]
            shape = inp.shape  # e.g. [1, 3, 640, 640] or ['batch', 3, 'height', 'width']
            _ss_fallback = self.screenshot_size_var.get()
            model_h = int(shape[2]) if len(shape) >= 4 and isinstance(shape[2], int) else _ss_fallback
            model_w = int(shape[3]) if len(shape) >= 4 and isinstance(shape[3], int) else _ss_fallback
            model_dtype = np.float16 if 'float16' in str(inp.type).lower() or 'half' in path.lower() else np.float32
            # Read class names from model metadata and update filter UI
            cls_names = self._read_model_class_names(new_model)
            if cls_names:
                print(f"[MODEL] Metadata class names: {cls_names}")
            self._update_class_filter_ui(cls_names)
            # Detect output format: yolox / v8 / v5
            # Uses dummy inference probe for models with dynamic output shapes (e.g. YOLOv11)
            out_fmt, _, _ = self._detect_output_format(new_model, model_h, model_w, inp.name, model_dtype)
            with self._model_lock:
                self.model = new_model
                self._model_input_size = (model_w, model_h)
                self._model_input_dtype = model_dtype
                self._model_input_name = inp.name
                self._model_output_format = out_fmt
                self._model_skip_normalize = self._check_skip_normalize(path, out_fmt)
                if out_fmt == "yolox":
                    yolox_strides = [8, 16, 32]
                    # Pre-build YOLOX decode grids
                    grid_x_list, grid_y_list, stride_list = [], [], []
                    for s in yolox_strides:
                        gs_h, gs_w = model_h // s, model_w // s
                        yv, xv = np.meshgrid(np.arange(gs_h), np.arange(gs_w), indexing='ij')
                        grid_x_list.append(xv.flatten())
                        grid_y_list.append(yv.flatten())
                        stride_list.append(np.full(gs_h * gs_w, s))
                    self._yolox_grid_x = np.concatenate(grid_x_list).astype(np.float32)
                    self._yolox_grid_y = np.concatenate(grid_y_list).astype(np.float32)
                    self._yolox_stride = np.concatenate(stride_list).astype(np.float32)
            self.model_status_label.configure(text=f"已加载: {self.model_var.get()} ({model_w}x{model_h} {out_fmt})", foreground="green")
            print(f"[MODEL] Switched to: {path}  input={inp.name} {model_w}x{model_h} dtype={inp.type} format={out_fmt}")
        except Exception as e:
            self.model_status_label.configure(text=f"加载失败!", foreground="red")
            messagebox.showerror("模型加载失败", str(e))

    @staticmethod
    def _check_skip_normalize(model_path, out_fmt):
        """Check if model needs raw 0-255 input (skip /255 normalization).
        YOLOX always skips. Models with '77-' prefix are known to need raw input."""
        if out_fmt == "yolox":
            return True
        basename = os.path.basename(model_path)
        if basename.startswith("77-"):
            print(f"[MODEL] 77-series model detected, using raw 0-255 input")
            return True
        return False

    @staticmethod
    def _detect_output_format(session, model_h, model_w, input_name, model_dtype):
        """Detect ONNX model output format by inspecting output shape.
        If the output shape has dynamic (None/str) dimensions, runs a dummy inference
        to get the actual tensor shape. This is critical for YOLOv11 models which
        are often exported with dynamic axes.

        Returns: ("v5" | "v8" | "yolox", dim1, dim2) where dim1/dim2 are the actual
        output dimensions (excluding batch).
        """
        out_meta = session.get_outputs()[0].shape
        print(f"[MODEL] Output metadata shape: {out_meta}")

        dim1, dim2 = None, None

        # Try to read from metadata first
        if len(out_meta) == 3:
            d1, d2 = out_meta[1], out_meta[2]
            if isinstance(d1, int) and isinstance(d2, int):
                dim1, dim2 = d1, d2

        # If dimensions are dynamic (None or str), probe with dummy inference
        if dim1 is None or dim2 is None:
            print(f"[MODEL] Dynamic output shape detected, probing with dummy inference...")
            try:
                dummy = np.zeros((1, 3, model_h, model_w), dtype=model_dtype)
                dummy_out = session.run(None, {input_name: dummy})[0]
                actual_shape = dummy_out.shape
                print(f"[MODEL] Probed actual output shape: {actual_shape}")
                if len(actual_shape) == 3:
                    dim1, dim2 = actual_shape[1], actual_shape[2]
                elif len(actual_shape) == 2:
                    # Some models squeeze the batch dim
                    dim1, dim2 = actual_shape[0], actual_shape[1]
            except Exception as e:
                print(f"[MODEL] Dummy inference failed: {e}")

        if dim1 is None or dim2 is None:
            print(f"[MODEL] Could not determine output dims, defaulting to v5")
            return "v5", 0, 0

        # Detect format
        yolox_strides = [8, 16, 32]
        yolox_expected = sum((model_h // s) * (model_w // s) for s in yolox_strides)
        print(f"[MODEL] dim1={dim1} dim2={dim2} yolox_expected={yolox_expected}")

        if dim1 == yolox_expected and dim2 < dim1:
            return "yolox", dim1, dim2
        elif dim1 < dim2:
            # v8/v11 format: [1, 4+nc, num_anchors] where 4+nc < num_anchors
            return "v8", dim1, dim2
        else:
            # v5 format: [1, num_anchors, 5+nc] where num_anchors > 5+nc
            return "v5", dim1, dim2

    def _create_onnx_session(self, model_path):
        if ort is None:
            raise RuntimeError("onnxruntime 未安装")
        if not os.path.exists(model_path):
            raise RuntimeError(f"找不到模型: {model_path}")
        providers = ort.get_available_providers()
        if "DmlExecutionProvider" in providers:
            prov = "DmlExecutionProvider"; self.device_name = "DirectML (GPU)"
        elif "CUDAExecutionProvider" in providers:
            prov = "CUDAExecutionProvider"; self.device_name = "CUDA (GPU)"
        else:
            prov = "CPUExecutionProvider"; self.device_name = "CPU"
        self.device_var.set(f"Device: {self.device_name}")
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return ort.InferenceSession(model_path, sess_options=so, providers=[prov])

    def load_model(self):
        if torch is None or non_max_suppression is None:
            raise RuntimeError("PyTorch 未安装")
        path = self._get_selected_model_path()
        if not path:
            path = os.path.join(SCRIPT_DIR, "yolov5s320Half.onnx")
        self.status_var.set("正在加载模型...")
        self.root.update_idletasks()
        self.model = self._create_onnx_session(path)
        # Read model expected input size (handle dynamic axes from YOLOv11)
        inp = self.model.get_inputs()[0]
        shape = inp.shape  # e.g. [1, 3, 640, 640] or ['batch', 3, 'height', 'width']
        _ss_fallback = self.screenshot_size_var.get()
        model_h = int(shape[2]) if len(shape) >= 4 and isinstance(shape[2], int) else _ss_fallback
        model_w = int(shape[3]) if len(shape) >= 4 and isinstance(shape[3], int) else _ss_fallback
        self._model_input_size = (model_w, model_h)
        self._model_input_dtype = np.float16 if 'float16' in str(inp.type).lower() or 'half' in path.lower() else np.float32
        self._model_input_name = inp.name
        # Read class names from model metadata and update filter UI
        cls_names = self._read_model_class_names(self.model)
        if cls_names:
            print(f"[MODEL] Metadata class names: {cls_names}")
        self._update_class_filter_ui(cls_names)
        # Detect output format using unified helper (handles dynamic shapes from YOLOv11)
        out_fmt, _, _ = self._detect_output_format(self.model, model_h, model_w, inp.name, self._model_input_dtype)
        self._model_output_format = out_fmt
        if out_fmt == "yolox":
            yolox_strides = [8, 16, 32]
            grid_x_list, grid_y_list, stride_list = [], [], []
            for s in yolox_strides:
                gs_h, gs_w = model_h // s, model_w // s
                yv, xv = np.meshgrid(np.arange(gs_h), np.arange(gs_w), indexing='ij')
                grid_x_list.append(xv.flatten())
                grid_y_list.append(yv.flatten())
                stride_list.append(np.full(gs_h * gs_w, s))
            self._yolox_grid_x = np.concatenate(grid_x_list).astype(np.float32)
            self._yolox_grid_y = np.concatenate(grid_y_list).astype(np.float32)
            self._yolox_stride = np.concatenate(stride_list).astype(np.float32)
        self._model_skip_normalize = self._check_skip_normalize(path, self._model_output_format)
        self.model_status_label.configure(text=f"已加载: {self.model_var.get()} ({model_w}x{model_h} {self._model_output_format})", foreground="green")
        self.status_var.set("模型已加载。")
        print(f"[MODEL] Loaded: {path}  input={inp.name} {model_w}x{model_h} dtype={self._model_input_dtype} format={self._model_output_format} skip_norm={self._model_skip_normalize}")

    # --------------------------------------------------------- start/stop
    def start_viewer(self):
        if self.running:
            return
        if bettercam is None:
            messagebox.showerror("缺少依赖", "bettercam 未安装。\npip install bettercam")
            return
        window = self.get_selected_window()
        if window is None:
            return
        try:
            window.activate()
        except Exception:
            pass
        # Try to get the actual client area (game rendering area, excluding title bar)
        # This gives the true center where the crosshair is
        try:
            hwnd = window._hWnd
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
            pt = ctypes.wintypes.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
            client_left = pt.x
            client_top = pt.y
            client_w = rect.right - rect.left
            client_h = rect.bottom - rect.top
            center_x = client_left + client_w // 2
            center_y = client_top + client_h // 2
            print(f"[CAPTURE] Using client area: ({client_left},{client_top}) {client_w}x{client_h} center=({center_x},{center_y})")
        except Exception as e:
            # Fallback: use window bounds
            center_x = (window.left + window.right) // 2
            center_y = (window.top + window.bottom) // 2
            print(f"[CAPTURE] Fallback to window bounds: center=({center_x},{center_y}) err={e}")
        ss = self.screenshot_size_var.get()
        left = center_x - ss // 2
        top = center_y - ss // 2
        region = (left, top, left + ss, top + ss)
        print(f"[CAPTURE] region={region}")
        self._mouse_mode = False
        self._capture_region = region  # for overlay coordinate mapping
        try:
            if not self.color_mode_var.get() and self.model is None:
                self.load_model()
            self.camera = bettercam.create(region=region, output_color="BGRA")
            if self.camera is None:
                raise RuntimeError("摄像头创建失败")
        except Exception as exc:
            self.status_var.set("启动失败")
            messagebox.showerror("启动失败", str(exc))
            return
        self.running = True
        self.status_var.set("运行中 (窗口模式) | 按 Q 关闭预览窗口")
        self.worker = threading.Thread(target=self.viewer_loop, daemon=True)
        self.worker.start()

    def start_viewer_fullscreen(self):
        """Start capture centered on screen center — no window selection needed."""
        if self.running:
            return
        if bettercam is None:
            messagebox.showerror("缺少依赖", "bettercam 未安装。\npip install bettercam")
            return
        user32 = ctypes.windll.user32
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        center_x = sw // 2
        center_y = sh // 2
        ss = self.screenshot_size_var.get()
        left = max(0, center_x - ss // 2)
        top = max(0, center_y - ss // 2)
        region = (left, top, left + ss, top + ss)
        print(f"[CAPTURE] Fullscreen center mode: screen={sw}x{sh} region={region}")
        self._mouse_mode = False
        self._capture_region = region
        try:
            if not self.color_mode_var.get() and self.model is None:
                self.load_model()
            self.camera = bettercam.create(region=region, output_color="BGRA")
            if self.camera is None:
                raise RuntimeError("摄像头创建失败")
        except Exception as exc:
            self.status_var.set("启动失败")
            messagebox.showerror("启动失败", str(exc))
            return
        self.running = True
        self.status_var.set("运行中 (全屏模式) | 按 Q 关闭预览窗口")
        self.worker = threading.Thread(target=self.viewer_loop, daemon=True)
        self.worker.start()

    def start_viewer_mouse_mode(self):
        """Start capture in mouse-follow mode — region follows cursor every frame."""
        if self.running:
            return
        if bettercam is None:
            messagebox.showerror("缺少依赖", "bettercam 未安装。\npip install bettercam")
            return
        self._mouse_mode = True
        try:
            if not self.color_mode_var.get() and self.model is None:
                self.load_model()
            # Create camera without fixed region — we pass region per-frame in grab()
            self.camera = bettercam.create(output_color="BGRA")
            if self.camera is None:
                raise RuntimeError("摄像头创建失败")
        except Exception as exc:
            self.status_var.set("启动失败")
            messagebox.showerror("启动失败", str(exc))
            return
        self.running = True
        self.status_var.set("运行中 (鼠标模式) | 按 Q 关闭预览窗口")
        print("[CAPTURE] Mouse-follow mode: region tracks cursor every frame")
        self.worker = threading.Thread(target=self.viewer_loop, daemon=True)
        self.worker.start()

    # -------------------------------------------------------- main loop
    def viewer_loop(self):
        last_time = time.time()
        frame_count = 0
        fps = 0.0
        _ss = self.screenshot_size_var.get()
        cWidth = _ss // 2
        cHeight = _ss // 2
        debug_timer = time.time()

        aim_log_timer = 0.0
        current_fps = self.fps_var.get()
        perf_capture_ms = 0.0
        perf_infer_ms = 0.0
        perf_total_ms = 0.0
        perf_count = 0
        render_counter = 0
        RENDER_EVERY_N = 3  # Only render preview every N frames for performance
        cv_window_created = False

        # Recoil spray tracking
        spray_start_time = 0.0   # When left-click started (for bullet index calc)
        prev_lmb = False         # Previous frame's LMB state
        last_recoil_idx = -1     # Last bullet index we applied recoil for
        recoil_target_x = 0.0   # Where recoil SHOULD be (accumulates bullet deltas)
        recoil_target_y = 0.0
        recoil_current_x = 0.0  # Where recoil ACTUALLY is (lerps toward target)
        recoil_current_y = 0.0
        recoil_accum_x = 0.0    # Total mouse pixels applied (for aim offset calc)
        recoil_accum_y = 0.0
        recoil_key_press_time = 0.0  # When recoil key was first pressed (for hold threshold)

        # Aim key press tracking (for X-only lock duration)
        aim_key_press_time = 0.0   # When aim key was first pressed
        prev_aim_key = False       # Previous frame's aim key state

        # PID controller for aim tracking (replaces pure P-controller)
        aim_pid = AimPID()

        # Target lock: stick to one target to avoid multi-target pull
        locked_target_pos = None    # (mid_x, mid_y) of locked target
        locked_miss_count = 0       # consecutive frames target not found

        # Triggerbot state
        trigger_on_target_since = 0.0  # timestamp when crosshair first entered a target box
        trigger_fired = False          # whether we already fired for this "on-target" episode
        trigger_fire_time = 0.0        # when the last shot was fired (for cooldown)

        # Osiris-style max angle delta per frame (pixels) to prevent snap/overshoot
        # This acts as a speed cap — no single frame can move more than this
        MAX_PIXEL_DELTA = 150

        print("===== Aim loop started =====")
        print(f"  win32api loaded = {win32api is not None}")
        print(f"  screenShot = {_ss}x{_ss}")
        print(f"  target_fps = {current_fps}")
        print("=============================")

        while self.running:
            t_frame_start = time.perf_counter()
            capture_region = None  # (left, top, ...) for overlay coordinate mapping
            if self._mouse_mode and win32api is not None:
                # Mouse-follow mode: capture region centered on cursor
                cx, cy = win32api.GetCursorPos()
                sw = ctypes.windll.user32.GetSystemMetrics(0)  # screen width
                sh = ctypes.windll.user32.GetSystemMetrics(1)  # screen height
                ml = max(0, min(cx - _ss // 2, sw - _ss))
                mt = max(0, min(cy - _ss // 2, sh - _ss))
                mouse_region = (ml, mt, ml + _ss, mt + _ss)
                capture_region = mouse_region
                frame = self.camera.grab(region=mouse_region) if self.camera else None
            else:
                capture_region = getattr(self, '_capture_region', None)
                frame = self.camera.grab() if self.camera else None
            if frame is None:
                time.sleep(0.001)
                continue
            t_capture_done = time.perf_counter()

            image = np.array(frame)
            if image.shape[2] == 4:
                image = image[:, :, :3]

            # ----- Read LIVE config from tkinter vars (real-time, no restart) -----
            use_color_mode = self.color_mode_var.get()
            cur_fov = self.fov_var.get()
            cur_smooth = self.smooth_var.get()
            cur_amp = self.amp_var.get()
            cur_conf = self.conf_var.get()
            cur_target = TARGET_OPTIONS.get(self.target_var.get(), "head")
            cur_key = KEY_OPTIONS.get(self.key_var.get(), 0x02)
            aim_on = self.aim_enabled_var.get()
            # Color mode overrides: convert color_smooth (0.05~1.0 multiplier) to smooth divisor
            if use_color_mode:
                _cs = self.color_smooth_var.get()
                cur_smooth = max(1.0 / max(_cs, 0.05), 1.0)
                cur_amp = 1.0  # color mode doesn't need amp scaling
            show_preview = self.visuals_var.get()
            use_overlay = self.overlay_var.get()
            # Manage overlay lifecycle
            if use_overlay and self._overlay is None:
                try:
                    self._overlay = OverlayWindow()
                    print("[OVERLAY] Created fullscreen overlay")
                except Exception as oe:
                    print(f"[OVERLAY] Failed to create: {oe}")
                    self._overlay = None
            elif not use_overlay and self._overlay is not None:
                self._overlay.destroy()
                self._overlay = None
                print("[OVERLAY] Destroyed overlay")
            render_counter += 1
            do_render = (show_preview or use_overlay) and (render_counter % RENDER_EVERY_N == 0)

            if use_color_mode:
                # ============ COLOR DETECTION MODE (找色模式) ============
                # Skip ONNX entirely — use HSV color detection for ultra-high FPS
                t_infer_start = time.perf_counter()
                cap_h, cap_w = image.shape[:2]
                scale_x = 1.0
                scale_y = 1.0

                # Read HSV range from GUI
                hsv_lower = np.array([self.color_h_low_var.get(), self.color_s_low_var.get(), self.color_v_low_var.get()])
                hsv_upper = np.array([self.color_h_high_var.get(), self.color_s_high_var.get(), self.color_v_high_var.get()])
                min_area = self.color_min_area_var.get()

                # BGR → HSV → color mask → dilate → find contours
                hsv_img = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv_img, hsv_lower, hsv_upper)
                dilated = cv2.dilate(mask, None, iterations=3)
                contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                # Build targets from contours (same dict format as AI detection)
                targets = []
                head_boxes = []
                display = image.copy() if do_render else None
                all_dets = []
                is_headbody_model = False  # color mode has no head/body distinction

                for cnt in contours:
                    area = cv2.contourArea(cnt)
                    if area < min_area:
                        continue
                    x, y, w, h = cv2.boundingRect(cnt)
                    mid_x = x + w / 2.0
                    mid_y = y + h / 2.0
                    dist = ((mid_x - cWidth)**2 + (mid_y - cHeight)**2) ** 0.5
                    ibox = (x, y, x + w, y + h)
                    d = {"cls": 0, "conf": 1.0,
                         "mid_x": mid_x, "mid_y": mid_y,
                         "box_h": float(h), "box_w": float(w), "area": area,
                         "dist": dist, "xyxy": ibox}
                    all_dets.append(d)
                    targets.append(d)
                    if display is not None:
                        color = (0, 255, 0)
                        label = f"COLOR {area:.0f}px"
                        d["_color"] = color; d["_label"] = label
                        cv2.rectangle(display, (x, y), (x + w, y + h), color, 2)
                        cv2.putText(display, label, (x, max(20, y - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)

                t_infer_done = time.perf_counter()

                # Accumulate perf stats
                perf_capture_ms += (t_capture_done - t_frame_start) * 1000
                perf_infer_ms += (t_infer_done - t_infer_start) * 1000
                perf_total_ms += (t_infer_done - t_frame_start) * 1000
                perf_count += 1

            else:
                # ============ AI DETECTION MODE (ONNX) ============
                # Preprocess — resize to model input size if needed
                with self._model_lock:
                    model_w, model_h = self._model_input_size
                cap_h, cap_w = image.shape[:2]
                if cap_w != model_w or cap_h != model_h:
                    im_resized = cv2.resize(image, (model_w, model_h), interpolation=cv2.INTER_LINEAR)
                    scale_x = cap_w / model_w
                    scale_y = cap_h / model_h
                else:
                    im_resized = image
                    scale_x = 1.0
                    scale_y = 1.0
                with self._model_lock:
                    model_dtype = self._model_input_dtype
                    out_fmt = self._model_output_format
                    skip_norm = getattr(self, '_model_skip_normalize', False)
                if skip_norm:
                    # Model expects 0-255 raw RGB pixel input (bettercam gives BGR, so convert)
                    im = np.expand_dims(im_resized[:, :, ::-1], 0).astype(model_dtype)
                elif out_fmt == "v8":
                    # YOLOv8/v11 models are trained on RGB; bettercam gives BGR → convert
                    im = np.expand_dims(im_resized[:, :, ::-1], 0).astype(model_dtype) / 255.0
                else:
                    # YOLOv5 models: keep BGR (YOLOv5 pipeline uses BGR internally)
                    im = np.expand_dims(im_resized, 0).astype(model_dtype) / 255.0
                im = np.ascontiguousarray(np.moveaxis(im, 3, 1))

                t_infer_start = time.perf_counter()
                try:
                    with self._model_lock:
                        input_name = self._model_input_name
                        out_fmt = self._model_output_format
                        outputs = self.model.run(None, {input_name: im})
                    raw = outputs[0]

                    if out_fmt == "yolox":
                        # YOLOX: [1, N_anchors, 5+nc] — bbox is raw (needs grid decode), obj+cls are sigmoid
                        with self._model_lock:
                            gx = self._yolox_grid_x
                            gy = self._yolox_grid_y
                            gs = self._yolox_stride
                        boxes_raw = raw[0]  # [N, 5+nc]
                        dec_cx = (boxes_raw[:, 0] + gx) * gs
                        dec_cy = (boxes_raw[:, 1] + gy) * gs
                        dec_w  = np.exp(boxes_raw[:, 2]) * gs
                        dec_h  = np.exp(boxes_raw[:, 3]) * gs
                        obj_conf = boxes_raw[:, 4]
                        cls_conf = boxes_raw[:, 5:]
                        nc = cls_conf.shape[1]
                        if nc > 1:
                            class_ids = np.argmax(cls_conf, axis=1)
                            class_max = np.max(cls_conf, axis=1)
                        else:
                            class_ids = np.zeros(len(obj_conf), dtype=int)
                            class_max = cls_conf[:, 0]
                        confs = obj_conf * class_max
                        mask = confs > cur_conf
                        pred = []
                        if mask.any():
                            cx_f, cy_f = dec_cx[mask], dec_cy[mask]
                            w_f, h_f = dec_w[mask], dec_h[mask]
                            x1 = cx_f - w_f / 2
                            y1 = cy_f - h_f / 2
                            x2 = cx_f + w_f / 2
                            y2 = cy_f + h_f / 2
                            confs_f = confs[mask]
                            class_ids_f = class_ids[mask].astype(np.float32)
                            dets = torch.tensor(np.stack([x1, y1, x2, y2, confs_f, class_ids_f], axis=1))
                            order = torch.argsort(dets[:, 4], descending=True)
                            dets = dets[order[:50]]
                            keep = []
                            while len(dets) > 0 and len(keep) < 10:
                                keep.append(dets[0])
                                if len(dets) == 1:
                                    break
                                ious = _box_iou(dets[0, :4].unsqueeze(0), dets[1:, :4]).squeeze(0)
                                dets = dets[1:][ious < 0.45]
                            pred = [torch.stack(keep)] if keep else []

                    elif out_fmt == "v8":
                        # YOLOv8 output: [1, 4+nc, anchors] → transpose to [1, anchors, 4+nc]
                        raw_t = np.transpose(raw, (0, 2, 1))
                        boxes = raw_t[0]
                        nc = boxes.shape[1] - 4
                        if nc > 1:
                            class_confs = boxes[:, 4:]
                            class_ids = np.argmax(class_confs, axis=1)
                            confs = np.max(class_confs, axis=1)
                        else:
                            confs = boxes[:, 4]
                            class_ids = np.zeros(len(confs), dtype=int)
                        mask = confs > cur_conf
                        boxes_f = boxes[mask]
                        confs_f = confs[mask]
                        class_ids_f = class_ids[mask]
                        pred = []
                        if len(boxes_f) > 0:
                            cx, cy, w, h = boxes_f[:, 0], boxes_f[:, 1], boxes_f[:, 2], boxes_f[:, 3]
                            x1 = cx - w / 2
                            y1 = cy - h / 2
                            x2 = cx + w / 2
                            y2 = cy + h / 2
                            dets = torch.tensor(np.stack([x1, y1, x2, y2, confs_f, class_ids_f.astype(np.float32)], axis=1))
                            order = torch.argsort(dets[:, 4], descending=True)
                            dets = dets[order[:50]]
                            keep = []
                            while len(dets) > 0 and len(keep) < 10:
                                keep.append(dets[0])
                                if len(dets) == 1:
                                    break
                                ious = _box_iou(dets[0, :4].unsqueeze(0), dets[1:, :4]).squeeze(0)
                                dets = dets[1:][ious < 0.45]
                            pred = [torch.stack(keep)] if keep else []
                    else:
                        # YOLOv5 output: [1, N, 85]
                        pred = torch.from_numpy(raw).to('cpu')
                        pred = non_max_suppression(pred, cur_conf, cur_conf, 0, False, max_det=10)
                except Exception as exc:
                    print(f"[ERROR] Inference failed: {exc}")
                    self.root.after(0, self.status_var.set, f"检测错误: {exc}")
                    break
                t_infer_done = time.perf_counter()

                # Accumulate perf stats
                perf_capture_ms += (t_capture_done - t_frame_start) * 1000
                perf_infer_ms += (t_infer_done - t_infer_start) * 1000
                perf_total_ms += (t_infer_done - t_frame_start) * 1000
                perf_count += 1

                # --- Build targets ---
                targets = []
                head_boxes = []
                display = image.copy() if do_render else None
                model_name = self.model_var.get()
                is_headbody_model = "头身" in model_name or "头" in model_name
                head_cls_ids = set()
                body_cls_ids = set()
                ct_body_cls = set()
                t_body_cls = set()
                if is_headbody_model:
                    import re as _re
                    cls_matches = _re.findall(r'(\d+)([\u4e00-\u9fff]+)', os.path.basename(model_name))
                    if cls_matches:
                        for cid_str, label in cls_matches:
                            cid = int(cid_str)
                            if "头" in label:
                                head_cls_ids.add(cid)
                            else:
                                body_cls_ids.add(cid)
                                if "警" in label:
                                    ct_body_cls.add(cid)
                                elif "匪" in label:
                                    t_body_cls.add(cid)
                has_explicit_cls = len(head_cls_ids) > 0 and len(body_cls_ids) > 0

                cur_team = TEAM_OPTIONS.get(self.team_var.get(), "all")
                if has_explicit_cls:
                    if cur_team == "ct":
                        enemy_body_cls = t_body_cls
                    elif cur_team == "t":
                        enemy_body_cls = ct_body_cls
                    else:
                        enemy_body_cls = body_cls_ids
                    enemy_cls = None
                else:
                    if cur_team == "ct":
                        enemy_cls = {1}
                    elif cur_team == "t":
                        enemy_cls = {0}
                    else:
                        enemy_cls = None
                    enemy_body_cls = None

                # First pass: collect all detections with their info
                all_dets = []
                for det in pred:
                    if len(det) == 0:
                        continue
                    for *xyxy, conf_val, cls in det:
                        if float(conf_val) < cur_conf:
                            continue
                        cls_id = int(cls)
                        x1 = float(xyxy[0]) * scale_x
                        y1 = float(xyxy[1]) * scale_y
                        x2 = float(xyxy[2]) * scale_x
                        y2 = float(xyxy[3]) * scale_y
                        mid_x = (x1 + x2) / 2
                        mid_y = (y1 + y2) / 2
                        box_h = y2 - y1
                        box_w = x2 - x1
                        area = box_w * box_h
                        dist = ((mid_x - cWidth)**2 + (mid_y - cHeight)**2) ** 0.5
                        ibox = (int(x1), int(y1), int(x2), int(y2))
                        all_dets.append({"cls": cls_id, "conf": float(conf_val),
                                         "mid_x": mid_x, "mid_y": mid_y,
                                         "box_h": box_h, "box_w": box_w, "area": area,
                                         "dist": dist, "xyxy": ibox})

                # --- Anti-flash: check for flash class BEFORE class filter ---
                if (self.antiflash_enabled_var.get() and not self._antiflash_active
                        and time.perf_counter() > self._antiflash_cooldown_until
                        and self._model_class_names):
                    af_conf = self.antiflash_conf_var.get()
                    flash_cls_ids = {cid for cid, name in self._model_class_names.items() if "闪" in name}
                    if flash_cls_ids:
                        for d in all_dets:
                            if d["cls"] in flash_cls_ids and d["conf"] >= af_conf:
                                af_delay = self.antiflash_delay_var.get()
                                threading.Thread(target=self._antiflash_execute,
                                                 args=(af_delay,), daemon=True).start()
                                break

                # Apply class filter from GUI checkboxes (if user unchecked some classes)
                _enabled_cls = self._get_enabled_class_ids()
                if _enabled_cls is not None:
                    all_dets = [d for d in all_dets if d["cls"] in _enabled_cls]

                if is_headbody_model and has_explicit_cls and len(all_dets) > 0:
                    for d in all_dets:
                        cls_id = d["cls"]
                        if cls_id in head_cls_ids:
                            d["_role"] = "head"
                            color = (0, 255, 255)
                            label = f"HEAD(c{cls_id}) {d['conf']:.0%}"
                            d["_color"] = color; d["_label"] = label
                            head_boxes.append(d)
                            if display is not None:
                                cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                                cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)
                        elif cls_id in body_cls_ids:
                            d["_role"] = "body"
                            is_enemy = (enemy_body_cls is None or cls_id in enemy_body_cls)
                            color = (0, 0, 255) if is_enemy else (255, 180, 0)
                            side = "CT" if cls_id in ct_body_cls else "T"
                            label = f"{side}(c{cls_id}) {d['conf']:.0%}"
                            d["_color"] = color; d["_label"] = label
                            if enemy_body_cls is None or cls_id in enemy_body_cls:
                                targets.append(d)
                            if display is not None:
                                cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                                cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                        else:
                            d["_role"] = "body"
                            color = (0, 255, 0)
                            label = f"c{cls_id} {d['conf']:.0%}"
                            d["_color"] = color; d["_label"] = label
                            targets.append(d)
                            if display is not None:
                                cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                                cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                elif is_headbody_model and len(all_dets) > 0:
                    # Geometric containment model (e.g. GO_头身): small box inside big box = head
                    all_dets.sort(key=lambda d: d["area"], reverse=True)
                    for i, d in enumerate(all_dets):
                        d["_role"] = "body"
                        dx, dy = d["mid_x"], d["mid_y"]
                        for j in range(i):
                            big = all_dets[j]
                            bx1, by1, bx2, by2 = big["xyxy"]
                            if bx1 <= dx <= bx2 and by1 <= dy <= by2 and d["area"] < big["area"] * 0.5:
                                d["_role"] = "head"
                                break
                    for d in all_dets:
                        if d["_role"] == "head":
                            color = (0, 255, 255)
                            label = f"HEAD(c{d['cls']}) {d['conf']:.0%}"
                            d["_color"] = color; d["_label"] = label
                            head_boxes.append(d)
                            if display is not None:
                                cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                                cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)
                        else:
                            color = (0, 0, 255)
                            label = f"BODY(c{d['cls']}) {d['conf']:.0%}"
                            d["_color"] = color; d["_label"] = label
                            targets.append(d)
                            if display is not None:
                                cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                                cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                else:
                    # Normal model (no 头身): use standard enemy filter
                    for d in all_dets:
                        is_enemy = (enemy_cls is None or d["cls"] in enemy_cls)
                        if is_enemy:
                            targets.append(d)
                        if enemy_cls is None:
                            color = (0, 255, 0)
                        elif is_enemy:
                            color = (0, 0, 255)
                        else:
                            color = (255, 180, 0)
                        label = f"{'CT' if d['cls'] == 0 else 'T'} {d['conf']:.0%}"
                        d["_color"] = color; d["_label"] = label
                        if display is not None:
                            cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                            cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # --- Aim assist ---
            cur_y_offset = self.crosshair_y_offset_var.get()
            # Key state comes from background 1000Hz polling thread
            keyDown = self._key_is_down
            # Always-aim: bypass key requirement
            if self.aim_always_var.get():
                keyDown = True

            # Track aim key press/release for X-only lock duration
            if keyDown and not prev_aim_key:
                aim_key_press_time = time.perf_counter()
            elif not keyDown:
                aim_key_press_time = 0.0
                # Reset target lock when aim key released (fresh lock next engagement)
                if prev_aim_key and self.aim_target_lock_var.get():
                    locked_target_pos = None
                    locked_miss_count = 0
            prev_aim_key = keyDown

            # Keep unfiltered targets for triggerbot (FOV is aim-only concept)
            all_targets = list(targets)

            if win32api is not None and aim_on and len(targets) > 0:
                targets.sort(key=lambda t: t["dist"])
                if cur_fov > 0:
                    targets = [t for t in targets if t["dist"] <= cur_fov]

                # Debug log every second
                now_t = time.time()
                if now_t - debug_timer > 1:
                    if perf_count > 0:
                        avg_cap = perf_capture_ms / perf_count
                        avg_inf = perf_infer_ms / perf_count
                        avg_tot = perf_total_ms / perf_count
                        print(f"[PERF] actual_fps={perf_count} capture={avg_cap:.1f}ms infer={avg_inf:.1f}ms total={avg_tot:.1f}ms")
                        perf_capture_ms = perf_infer_ms = perf_total_ms = 0.0
                        perf_count = 0
                    print(f"[DEBUG] targets={len(targets)} key_down={keyDown} fov={cur_fov} smooth={cur_smooth} amp={cur_amp} y_off={cur_y_offset}")
                    debug_timer = now_t

                # --- Target lock: stick to one target, avoid multi-target pull ---
                if self.aim_target_lock_var.get() and len(targets) > 0:
                    tl_radius = self.aim_target_lock_radius_var.get()
                    tl_max_miss = self.aim_target_lock_frames_var.get()
                    if locked_target_pos is not None:
                        # Try to find the same target by proximity to last known position
                        best_match = None
                        best_match_dist = float("inf")
                        lx, ly = locked_target_pos
                        for tgt in targets:
                            d_tl = ((tgt["mid_x"] - lx)**2 + (tgt["mid_y"] - ly)**2) ** 0.5
                            if d_tl < best_match_dist:
                                best_match_dist = d_tl
                                best_match = tgt
                        if best_match is not None and best_match_dist <= tl_radius:
                            # Found the same target — move it to front
                            locked_target_pos = (best_match["mid_x"], best_match["mid_y"])
                            locked_miss_count = 0
                            targets.remove(best_match)
                            targets.insert(0, best_match)
                        else:
                            # Target not found this frame
                            locked_miss_count += 1
                            if locked_miss_count >= tl_max_miss:
                                # Lost target for too long — release lock, pick nearest
                                locked_target_pos = (targets[0]["mid_x"], targets[0]["mid_y"])
                                locked_miss_count = 0
                    else:
                        # No lock yet — lock onto nearest target
                        locked_target_pos = (targets[0]["mid_x"], targets[0]["mid_y"])
                        locked_miss_count = 0
                elif len(targets) == 0:
                    locked_miss_count += 1
                    tl_max_miss = self.aim_target_lock_frames_var.get()
                    if locked_miss_count >= tl_max_miss:
                        locked_target_pos = None
                        locked_miss_count = 0

                if len(targets) > 0:
                    t = targets[0]
                    xMid, yMid, box_h = t["mid_x"], t["mid_y"], t["box_h"]

                    # Calculate aim point from bounding box (auto-scales with distance)
                    x1_box, y1_box = t["xyxy"][0], t["xyxy"][1]
                    x2_box = t["xyxy"][2]
                    box_w = x2_box - x1_box

                    # For head+body models: try to use the actual head bounding box
                    matched_head = None
                    if is_headbody_model and cur_target == "head" and head_boxes:
                        # Find head box whose center is inside or closest to this body box
                        best_hd = None
                        best_hd_dist = float("inf")
                        bx1, by1, bx2, by2 = t["xyxy"]
                        for hb in head_boxes:
                            hx, hy = hb["mid_x"], hb["mid_y"]
                            # Check if head center is inside/near body box
                            if bx1 <= hx <= bx2 and by1 <= hy <= by2:
                                hd = ((hx - xMid)**2 + (hy - yMid)**2) ** 0.5
                                if hd < best_hd_dist:
                                    best_hd_dist = hd
                                    best_hd = hb
                        matched_head = best_hd

                    if matched_head is not None:
                        # Aim at the center of the matched head bounding box
                        aim_x_abs = matched_head["mid_x"]
                        aim_y_abs = matched_head["mid_y"]
                    elif cur_target == "head":
                        aim_y_abs = y1_box + box_h * 0.08
                        aim_x_abs = x1_box + box_w * 0.5
                    elif cur_target == "chest":
                        aim_y_abs = y1_box + box_h * 0.35
                        aim_x_abs = xMid
                    elif cur_target == "body":
                        aim_y_abs = y1_box + box_h * 0.50
                        aim_x_abs = xMid
                    elif cur_target == "nearest":
                        aim_y_abs = yMid
                        aim_x_abs = xMid
                    else:
                        aim_y_abs = y1_box + box_h * 0.08
                        aim_x_abs = x1_box + box_w * 0.5

                    # --- Unified aim offset ---
                    # Raw pixel offset from screen center to aim point
                    rawX = aim_x_abs - cWidth
                    rawY = aim_y_abs - (cHeight + cur_y_offset)

                    # --- Adaptive aim: boost amp AND reduce smooth when not on target ---
                    if self.aim_adaptive_var.get():
                        cur_raw_dist = (rawX**2 + rawY**2) ** 0.5
                        on_target_px = 5.0
                        full_boost_px = 50.0
                        if cur_raw_dist > on_target_px:
                            boost_frac = min((cur_raw_dist - on_target_px) / (full_boost_px - on_target_px), 1.0)
                            adaptive_max = self.aim_adaptive_max_var.get()
                            cur_amp = cur_amp * (1.0 + boost_frac * (adaptive_max - 1.0))
                            cur_smooth = cur_smooth + boost_frac * (1.0 - cur_smooth)

                    # X-only aim lock: suppress Y-axis, let player control vertical manually
                    if self.aim_x_only_var.get():
                        x_lock_dur = self.aim_x_lock_duration_var.get()
                        if x_lock_dur > 0 and aim_key_press_time > 0:
                            aim_held_ms = (time.perf_counter() - aim_key_press_time) * 1000.0
                            if aim_held_ms > x_lock_dur:
                                rawY = 0.0
                        else:
                            rawY = 0.0

                    cur_aim_mode = AIM_MODE_OPTIONS.get(self.aim_mode_var.get(), "aimbot")
                    raw_dist = (rawX**2 + rawY**2) ** 0.5

                    # Reset PID when aim key just pressed (fresh start each engagement)
                    if keyDown and not prev_aim_key:
                        aim_pid.reset()

                    if cur_aim_mode == "assist":
                        # --- Aim Assist mode (PID) ---
                        rigid_spraying_a = self._rigid_spray_active and self.rigid_recoil_var.get()
                        ai_correct = self.rigid_ai_correct_var.get()
                        if rigid_spraying_a and not ai_correct:
                            rawY = 0.0
                        # Assist uses weaker gains: scale Kp down
                        assist_fov = max(cur_fov, 100)
                        proximity = max(0.0, 1.0 - (raw_dist / assist_fov))
                        assist_scale = 0.15 + 0.20 * proximity  # 15%~35%
                        kp = cur_amp / max(cur_smooth, 1.0) * assist_scale
                        ki = self.aim_ki_var.get() * assist_scale
                        kd = self.aim_kd_var.get() * assist_scale
                        moveX, moveY = aim_pid.compute(rawX, rawY, kp, ki, kd)

                        # Softer clamp for assist
                        assist_max = MAX_PIXEL_DELTA * 0.5
                        move_mag = (moveX**2 + moveY**2) ** 0.5
                        if move_mag > assist_max:
                            scale_f = assist_max / move_mag
                            moveX *= scale_f
                            moveY *= scale_f

                        mX, mY = round(moveX), round(moveY)
                        if abs(mX) <= 1 and abs(mY) <= 1:
                            mX, mY = 0, 0

                        if keyDown and (mX != 0 or mY != 0):
                            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, mX, mY, 0, 0)
                            if now_t - aim_log_timer > 1:
                                print(f"[ASSIST] raw=({rawX:.1f},{rawY:.1f}) dist={raw_dist:.1f} move=({mX},{mY})")
                                aim_log_timer = now_t
                    else:
                        # --- Aimbot mode (PID) ---
                        rigid_spraying = self._rigid_spray_active and self.rigid_recoil_var.get()
                        ai_correct = self.rigid_ai_correct_var.get()
                        lerp_spraying = (spray_start_time > 0 and cur_lmb and last_recoil_idx >= 1)
                        if rigid_spraying and not ai_correct:
                            rawY = 0.0
                        elif lerp_spraying:
                            rawY = 0.0

                        # PID gains: Kp = amp/smooth (same P behavior as before)
                        smooth_div = max(cur_smooth, 1.0)
                        kp = cur_amp / smooth_div
                        ki = self.aim_ki_var.get()
                        kd = self.aim_kd_var.get()
                        moveX, moveY = aim_pid.compute(rawX, rawY, kp, ki, kd)

                        # Clamp per-frame movement to MAX_PIXEL_DELTA
                        move_mag = (moveX**2 + moveY**2) ** 0.5
                        if move_mag > MAX_PIXEL_DELTA:
                            scale_f = MAX_PIXEL_DELTA / move_mag
                            moveX *= scale_f
                            moveY *= scale_f

                        mX, mY = round(moveX), round(moveY)

                        # Dead zone
                        if abs(mX) <= 1 and abs(mY) <= 1 and raw_dist < 3:
                            mX, mY = 0, 0

                        if keyDown and (mX != 0 or mY != 0):
                            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, mX, mY, 0, 0)
                            if now_t - aim_log_timer > 0.5:
                                print(f"[AIM] raw=({rawX:.1f},{rawY:.1f}) dist={raw_dist:.1f} kp={kp:.3f} ki={ki:.3f} kd={kd:.3f} move=({mX},{mY}) amp={cur_amp:.2f} sm={cur_smooth:.1f}")
                                aim_log_timer = now_t

                    # Attach aim point to detection for overlay dot drawing
                    t["_aim_x"] = aim_x_abs
                    t["_aim_y"] = aim_y_abs

                    if display is not None:
                        cv2.circle(display, (int(aim_x_abs), int(aim_y_abs)), 5, (0, 0, 255), -1)

            # --- Recoil compensation (unified with aim, lerp-smoothed) ---
            # Skip old lerp recoil entirely when rigid mode is active
            cur_lmb = self._recoil_key_is_down
            _rigid_active = self.rigid_recoil_var.get() and self._rigid_recoil_running
            rc_weapon = self.recoil_weapon_var.get()
            rc_strength = self.recoil_strength_var.get()
            rc_mag = get_mag_size(rc_weapon)
            rc_smooth = max(self.recoil_smooth_var.get(), 1)
            rc_enabled = self.recoil_enabled_var.get()

            # Aim-only gate: if enabled, treat recoil key as NOT pressed when aim key is not held
            rc_aim_only = self.recoil_aim_only_var.get()
            if rc_aim_only and not self._key_is_down:
                cur_lmb = False

            if rc_enabled and rc_mag > 0 and rc_strength > 0 and not _rigid_active:
                rc_hold_threshold = self.recoil_hold_ms_var.get()
                if cur_lmb and not prev_lmb:
                    # LMB just pressed — record press time, don't start spray yet
                    recoil_key_press_time = time.perf_counter()
                    spray_start_time = 0.0
                    last_recoil_idx = -1
                    recoil_target_x = 0.0
                    recoil_target_y = 0.0
                    recoil_current_x = 0.0
                    recoil_current_y = 0.0
                    recoil_accum_x = 0.0
                    recoil_accum_y = 0.0

                # Only activate spray after holding key for rc_hold_threshold ms
                if cur_lmb and recoil_key_press_time > 0 and spray_start_time == 0.0:
                    held_ms = (time.perf_counter() - recoil_key_press_time) * 1000.0
                    if held_ms >= rc_hold_threshold:
                        spray_start_time = time.perf_counter()

                if cur_lmb and spray_start_time > 0:
                    # Calculate which bullet we're on using per-bullet timing
                    # recoilTimeOffset: negative = compensate earlier, positive = later
                    rc_time_offset = self.recoil_time_offset_var.get()
                    elapsed_ms = (time.perf_counter() - spray_start_time) * 1000.0 - rc_time_offset
                    elapsed_ms = max(elapsed_ms, 0.0)
                    cumulative_ms = 0.0
                    bullet_idx = 0
                    for bi in range(rc_mag):
                        interval = get_fire_interval_ms(rc_weapon, bi)
                        if cumulative_ms + interval > elapsed_ms:
                            break
                        cumulative_ms += interval
                        bullet_idx = bi + 1
                    bullet_idx = min(bullet_idx, rc_mag - 1)

                    # Add new bullet deltas to the recoil target
                    while last_recoil_idx < bullet_idx:
                        last_recoil_idx += 1
                        dx, dy = get_bullet_delta(rc_weapon, last_recoil_idx)
                        recoil_target_x += dx * rc_strength
                        recoil_target_y += dy * rc_strength

                    # Lerp current position toward target
                    # alpha = 1.0 → instant (no smooth), smaller → smoother
                    # rc_smooth=1 → alpha=1.0, rc_smooth=4 → alpha≈0.35,
                    # rc_smooth=8 → alpha≈0.2
                    alpha = (1.0 / rc_smooth) * random.uniform(0.85, 1.15)
                    alpha = min(alpha, 1.0)
                    recoil_current_x += (recoil_target_x - recoil_current_x) * alpha
                    recoil_current_y += (recoil_target_y - recoil_current_y) * alpha

                    # Apply the delta from what we've already sent
                    rc_mx = round(recoil_current_x - recoil_accum_x)
                    rc_my = round(recoil_current_y - recoil_accum_y)
                    if win32api is not None and (rc_mx != 0 or rc_my != 0):
                        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, rc_mx, rc_my, 0, 0)
                    recoil_accum_x += rc_mx
                    recoil_accum_y += rc_my

                if not cur_lmb and prev_lmb:
                    # LMB released — reset spray
                    spray_start_time = 0.0
                    last_recoil_idx = -1
                    recoil_target_x = 0.0
                    recoil_target_y = 0.0
                    recoil_current_x = 0.0
                    recoil_current_y = 0.0
                    recoil_accum_x = 0.0
                    recoil_accum_y = 0.0

            prev_lmb = cur_lmb

            # --- Triggerbot (uses all_targets, independent of aim FOV filter) ---
            if win32api is not None and self.trigger_enabled_var.get() and len(all_targets) > 0:
                trig_delay_ms = self.trigger_delay_var.get()
                now_trig = time.perf_counter()
                # Check if crosshair (screen center) is inside any enemy bounding box
                crosshair_in_box = False
                for t in all_targets:
                    x1t, y1t, x2t, y2t = t["xyxy"]
                    if x1t <= cWidth <= x2t and y1t <= (cHeight + cur_y_offset) <= y2t:
                        crosshair_in_box = True
                        break
                if crosshair_in_box:
                    if trigger_on_target_since == 0.0:
                        trigger_on_target_since = now_trig
                    elapsed_on = (now_trig - trigger_on_target_since) * 1000.0
                    if elapsed_on >= trig_delay_ms and not trigger_fired:
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                        time.sleep(random.uniform(0.02, 0.06))
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                        trigger_fired = True
                        trigger_fire_time = now_trig
                else:
                    trigger_on_target_since = 0.0
                    trigger_fired = False
            else:
                trigger_on_target_since = 0.0
                trigger_fired = False

            # Crosshair on display (with Y offset visualized)
            if display is not None:
                cy = cHeight + cur_y_offset
                cv2.line(display, (cWidth-10, cy), (cWidth+10, cy), (0, 0, 255), 1)
                cv2.line(display, (cWidth, cy-10), (cWidth, cy+10), (0, 0, 255), 1)

            # FPS
            frame_count += 1
            now = time.time()
            if now - last_time >= 1.0:
                fps = frame_count / (now - last_time)
                frame_count = 0
                last_time = now
                mode_tag = "找色" if use_color_mode else "AI"
                self.root.after(0, self.status_var.set, f"运行中 [{mode_tag}] | FPS: {fps:.1f} | 目标: {len(targets)}")

            if do_render and display is not None and show_preview:
                cv2.putText(display, f"{self.device_name} | FPS:{fps:.0f}", (8, 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                aim_status = "ON" if aim_on else "OFF"
                cv2.putText(display, f"Aim:{aim_status} Key:{self.key_var.get()} Target:{cur_target}",
                            (8, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 255), 1)
                cv2.imshow("AI Vision Viewer", display)
                if not cv_window_created:
                    cv2.setWindowProperty("AI Vision Viewer", cv2.WND_PROP_TOPMOST, 1)
                    cv_window_created = True
                if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
                    break
            elif not show_preview:
                # Yield CPU so key polling thread can run reliably
                time.sleep(0.001)

            # --- Overlay drawing ---
            if do_render and use_overlay and self._overlay is not None:
                self._overlay.draw(all_dets, capture_region,
                                   box_thickness=self.ov_box_thickness_var.get(),
                                   box_style=self.ov_box_style_var.get(),
                                   corner_len=self.ov_corner_len_var.get(),
                                   show_dot=self.ov_dot_var.get(),
                                   dot_size=self.ov_dot_size_var.get(),
                                   dot_style=self.ov_dot_style_var.get(),
                                   dot_color=self.ov_dot_color_var.get(),
                                   hide_label=self.ov_hide_label_var.get())
            elif not use_overlay and self._overlay is not None:
                pass  # overlay destroyed above already

        self._cleanup_camera()
        self.root.after(0, self.stop_viewer)

    def stop_viewer(self):
        if not self.running and self.camera is None:
            return
        self.running = False
        # Camera cleanup is handled by _cleanup_camera, called from the worker
        # thread after it exits the loop (see viewer_loop end).
        # We only do UI cleanup here to keep the main thread non-blocking.
        if self._overlay is not None:
            self._overlay.clear()
        cv2.destroyAllWindows()
        self.status_var.set("已停止")

    def _cleanup_camera(self):
        """Release camera resources. Called from worker thread after loop exits."""
        cam = self.camera
        self.camera = None
        self.worker = None
        if cam is not None:
            try:
                cam.release()
            except Exception as e:
                print(f"[STOP] camera.release() error: {e}")
        print("[STOP] Camera released")

    def on_close(self):
        self._key_poll_running = False
        self._stop_rigid_recoil()
        self.running = False
        # Wait briefly for worker thread to finish before destroying window
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=2.0)
        # Now safe to release camera and destroy UI
        try:
            if self.camera is not None:
                self.camera.release()
                self.camera = None
        except Exception:
            pass
        if self._overlay is not None:
            self._overlay.destroy()
            self._overlay = None
        cv2.destroyAllWindows()
        self.root.destroy()


def main():
    root = tk.Tk()
    VisionViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
