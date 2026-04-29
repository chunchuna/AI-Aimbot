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

from config import confidence as _conf_default, screenShotHeight, screenShotWidth
from recoil_patterns import WEAPON_NAMES, get_recoil_offset, get_bullet_delta, get_fire_interval_ms, get_mag_size

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

        self.visuals_var = tk.BooleanVar(value=True)
        self.crosshair_y_offset_var = tk.IntVar(value=_read_config_value("crosshairYOffset", 0, int))
        self.fps_var = tk.IntVar(value=_read_config_value("captureFPS", 60, int))

        # Team filter
        cur_team = _read_config_value("aaTeamFilter", "all", str)
        self.team_var = tk.StringVar(value=TEAM_VALUE_TO_NAME.get(cur_team, "全部目标 (All)"))

        # Recoil compensation
        self.recoil_weapon_var = tk.StringVar(value=_read_config_value("recoilWeapon", "关闭 (Off)", str))
        self.recoil_strength_var = tk.DoubleVar(value=_read_config_value("recoilStrength", 1.0, float))
        self.recoil_smooth_var = tk.IntVar(value=_read_config_value("recoilSmooth", 4, int))
        # Recoil trigger key
        self.recoil_key_var = tk.StringVar()
        cur_rc_key = _read_config_hex("recoilKey", 0x01)
        self.recoil_key_var.set(KEY_CODE_TO_NAME.get(cur_rc_key, "鼠标左键 (Left Click)"))
        # Recoil key state for tracking spray (polled by key poll thread)
        self._recoil_key_is_down = False

        # Recoil enabled toggle (like aim_enabled_var for aim)
        self.recoil_enabled_var = tk.BooleanVar(value=True)

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

        self._build_ui()
        self.refresh_windows()

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
        ttk.Label(top_frame, text="选择目标窗口:").pack(side=tk.LEFT)
        ttk.Button(top_frame, text="刷新", command=self.refresh_windows).pack(side=tk.RIGHT, padx=4)
        ttk.Button(top_frame, text="启动", command=self.start_viewer).pack(side=tk.RIGHT, padx=4)
        ttk.Button(top_frame, text="鼠标模式", command=self.start_viewer_mouse_mode).pack(side=tk.RIGHT, padx=4)
        ttk.Button(top_frame, text="停止", command=self.stop_viewer).pack(side=tk.RIGHT)

        columns = ("index", "title", "process", "pid", "size")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("index", text="#")
        self.tree.heading("title", text="窗口标题")
        self.tree.heading("process", text="进程")
        self.tree.heading("pid", text="PID")
        self.tree.heading("size", text="尺寸")
        self.tree.column("index", width=36, anchor=tk.CENTER)
        self.tree.column("title", width=320)
        self.tree.column("process", width=120)
        self.tree.column("pid", width=60, anchor=tk.CENTER)
        self.tree.column("size", width=80, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        bottom = ttk.Frame(left, padding=(8, 0, 8, 8))
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, textvariable=self.device_var).pack(side=tk.LEFT)
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.RIGHT)

        # ===== RIGHT: config panel =====
        right = ttk.Frame(pw, padding=8)
        pw.add(right, weight=1)

        # Use a scrollable canvas for the right panel to fit all controls
        canvas = tk.Canvas(right, highlightthickness=0)
        scrollbar = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        # Enable mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        # Re-point 'right' to the scrollable inner frame
        right = scroll_frame

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

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        ttk.Label(right, text="自瞄设置", font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(0, 8), fill="x")

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

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

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
        ttk.Checkbutton(right, text="显示预览窗口", variable=self.visuals_var).pack(anchor="w", pady=2)

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

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

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

    # -------------------------------------------------------- config helpers
    def _gather_config_vals(self):
        """Collect current GUI values into a dict (used for save & profile)."""
        return {
            "aaFOV": self.fov_var.get(),
            "aaAimMode": AIM_MODE_OPTIONS.get(self.aim_mode_var.get(), "aimbot"),
            "aaTargetPart": TARGET_OPTIONS.get(self.target_var.get(), "head"),
            "aaSmoothFactor": round(self.smooth_var.get(), 1),
            "aaActivateKey": KEY_OPTIONS.get(self.key_var.get(), 0x02),
            "aaSecondaryKey": KEY2_OPTIONS.get(self.key2_var.get(), 0x00),
            "aaMovementAmp": round(self.amp_var.get(), 2),
            "confidence": round(self.conf_var.get(), 2),
            "crosshairYOffset": self.crosshair_y_offset_var.get(),
            "captureFPS": self.fps_var.get(),
            "recoilWeapon": self.recoil_weapon_var.get(),
            "recoilStrength": round(self.recoil_strength_var.get(), 2),
            "recoilSmooth": self.recoil_smooth_var.get(),
            "recoilKey": KEY_OPTIONS.get(self.recoil_key_var.get(), 0x01),
            "aaTeamFilter": TEAM_OPTIONS.get(self.team_var.get(), "all"),
            "aimToggleKey": HOTKEY_OPTIONS.get(self.aim_toggle_key_var.get(), 0x74),
            "recoilToggleKey": HOTKEY_OPTIONS.get(self.recoil_toggle_key_var.get(), 0x75),
            "triggerDelay": self.trigger_delay_var.get(),
            "triggerToggleKey": HOTKEY_OPTIONS.get(self.trigger_toggle_key_var.get(), 0x76),
            "selectedModel": self.model_var.get(),
            # Toggle states (profile-only, not written to config.py)
            "aimEnabled": self.aim_enabled_var.get(),
            "recoilEnabled": self.recoil_enabled_var.get(),
            "triggerEnabled": self.trigger_enabled_var.get(),
            "voiceEnabled": self.voice_enabled_var.get(),
        }

    def _apply_config_vals(self, vals: dict):
        """Apply a config dict to all GUI variables."""
        if "aaFOV" in vals:
            self.fov_var.set(int(vals["aaFOV"]))
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
        if "recoilWeapon" in vals:
            self.recoil_weapon_var.set(vals["recoilWeapon"])
        if "recoilStrength" in vals:
            self.recoil_strength_var.set(float(vals["recoilStrength"]))
        if "recoilSmooth" in vals:
            self.recoil_smooth_var.set(int(vals["recoilSmooth"]))
        if "recoilKey" in vals:
            self.recoil_key_var.set(KEY_CODE_TO_NAME.get(int(vals["recoilKey"]), "鼠标左键 (Left Click)"))
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
        # Toggle states
        if "aimEnabled" in vals:
            self.aim_enabled_var.set(bool(vals["aimEnabled"]))
        if "recoilEnabled" in vals:
            self.recoil_enabled_var.set(bool(vals["recoilEnabled"]))
        if "triggerEnabled" in vals:
            self.trigger_enabled_var.set(bool(vals["triggerEnabled"]))
        if "voiceEnabled" in vals:
            self.voice_enabled_var.set(bool(vals["voiceEnabled"]))
        self._update_status_labels()

    # -------------------------------------------------------- config save
    # Profile-only keys (not written to config.py)
    _PROFILE_ONLY_KEYS = {"aimEnabled", "recoilEnabled", "triggerEnabled", "voiceEnabled"}

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
        # Also write to config.py so it takes effect (exclude profile-only keys)
        try:
            config_vals = {k: v for k, v in vals.items() if k not in self._PROFILE_ONLY_KEYS}
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

    # ------------------------------------------------------- window list
    def refresh_windows(self):
        if self.running:
            messagebox.showinfo("运行中", "请先停止再刷新窗口列表。")
            return
        self.tree.delete(*self.tree.get_children())
        self.windows = []
        for window in pygetwindow.getAllWindows():
            title = window.title.strip()
            if not title or window.width <= 0 or window.height <= 0:
                continue
            hwnd = getattr(window, "_hWnd", None)
            pid = "?"
            proc = "?"
            if hwnd:
                try:
                    _, pv = win32process.GetWindowThreadProcessId(hwnd)
                    pid = str(pv)
                    if psutil:
                        proc = psutil.Process(pv).name()
                except Exception:
                    pass
            item = {"window": window, "title": title, "process": proc, "pid": pid,
                    "size": f"{window.width}x{window.height}"}
            self.windows.append(item)
            self.tree.insert("", tk.END, values=(len(self.windows)-1, title, proc, pid, item["size"]))
        self.status_var.set(f"找到 {len(self.windows)} 个窗口")

    def get_selected_window(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("未选择", "请先选择一个窗口。")
            return None
        idx = int(self.tree.item(sel[0], "values")[0])
        if idx < 0 or idx >= len(self.windows):
            return None
        return self.windows[idx]["window"]

    # --------------------------------------------------------- model
    def _get_selected_model_path(self):
        name = self.model_var.get()
        return self.available_models.get(name, "")

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
            # Read model expected input size
            inp = new_model.get_inputs()[0]
            shape = inp.shape  # e.g. [1, 3, 640, 640]
            model_h = int(shape[2]) if len(shape) >= 4 else 320
            model_w = int(shape[3]) if len(shape) >= 4 else 320
            model_dtype = np.float16 if 'float16' in str(inp.type).lower() or 'half' in path.lower() else np.float32
            # Detect output format: yolox / v8 / v5
            out_shape = new_model.get_outputs()[0].shape
            out_fmt = "v5"
            if len(out_shape) == 3 and out_shape[1] is not None and out_shape[2] is not None:
                dim1, dim2 = int(out_shape[1]), int(out_shape[2])
                yolox_strides = [8, 16, 32]
                yolox_expected = sum((model_h // s) * (model_w // s) for s in yolox_strides)
                if dim1 == yolox_expected and dim2 < dim1:
                    out_fmt = "yolox"
                elif dim1 < dim2:
                    out_fmt = "v8"
                else:
                    out_fmt = "v5"
            with self._model_lock:
                self.model = new_model
                self._model_input_size = (model_w, model_h)
                self._model_input_dtype = model_dtype
                self._model_input_name = inp.name
                self._model_output_format = out_fmt
                self._model_skip_normalize = self._check_skip_normalize(path, out_fmt)
                if out_fmt == "yolox":
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
                    print(f"[MODEL] YOLOX detected: {yolox_expected} anchors, strides={yolox_strides}")
            self.model_status_label.configure(text=f"已加载: {self.model_var.get()} ({model_w}x{model_h} {out_fmt})", foreground="green")
            print(f"[MODEL] Switched to: {path}  input={inp.name} {model_w}x{model_h} dtype={inp.type} format={out_fmt} out_shape={out_shape}")
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
        # Read model expected input size
        inp = self.model.get_inputs()[0]
        shape = inp.shape
        model_h = int(shape[2]) if len(shape) >= 4 else 320
        model_w = int(shape[3]) if len(shape) >= 4 else 320
        self._model_input_size = (model_w, model_h)
        self._model_input_dtype = np.float16 if 'float16' in str(inp.type).lower() or 'half' in path.lower() else np.float32
        self._model_input_name = inp.name
        out_shape = self.model.get_outputs()[0].shape
        print(f"[MODEL] out_shape={out_shape} len={len(out_shape)} types={[type(x).__name__ for x in out_shape]}")
        if len(out_shape) == 3 and out_shape[1] is not None and out_shape[2] is not None:
            dim1, dim2 = int(out_shape[1]), int(out_shape[2])
            # Check for YOLOX: anchor-free, 1 anchor per grid cell
            # Expected anchors = sum((input_size/stride)^2) for strides [8,16,32]
            yolox_strides = [8, 16, 32]
            yolox_expected = sum((model_h // s) * (model_w // s) for s in yolox_strides)
            print(f"[MODEL] dim1={dim1} dim2={dim2} yolox_expected={yolox_expected} match={dim1 == yolox_expected}")
            if dim1 == yolox_expected and dim2 < dim1:
                # YOLOX format: [1, N_anchors, 5+nc] with raw bbox needing grid decode
                self._model_output_format = "yolox"
                # Pre-build decode grids
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
                print(f"[MODEL] YOLOX detected: {yolox_expected} anchors, strides={yolox_strides}")
            elif dim1 < dim2:
                self._model_output_format = "v8"
            else:
                self._model_output_format = "v5"
        else:
            self._model_output_format = "v5"
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
        left = center_x - screenShotWidth // 2
        top = center_y - screenShotHeight // 2
        region = (left, top, left + screenShotWidth, top + screenShotHeight)
        print(f"[CAPTURE] region={region}")
        self._mouse_mode = False
        try:
            if self.model is None:
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

    def start_viewer_mouse_mode(self):
        """Start capture in mouse-follow mode — region follows cursor every frame."""
        if self.running:
            return
        if bettercam is None:
            messagebox.showerror("缺少依赖", "bettercam 未安装。\npip install bettercam")
            return
        self._mouse_mode = True
        try:
            if self.model is None:
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
        cWidth = screenShotWidth // 2
        cHeight = screenShotHeight // 2
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

        # Triggerbot state
        trigger_on_target_since = 0.0  # timestamp when crosshair first entered a target box
        trigger_fired = False          # whether we already fired for this "on-target" episode
        trigger_fire_time = 0.0        # when the last shot was fired (for cooldown)

        # Osiris-style max angle delta per frame (pixels) to prevent snap/overshoot
        # This acts as a speed cap — no single frame can move more than this
        MAX_PIXEL_DELTA = 150

        print("===== Aim loop started =====")
        print(f"  win32api loaded = {win32api is not None}")
        print(f"  screenShot = {screenShotWidth}x{screenShotHeight}")
        print(f"  target_fps = {current_fps}")
        print("=============================")

        while self.running:
            t_frame_start = time.perf_counter()
            if self._mouse_mode and win32api is not None:
                # Mouse-follow mode: capture region centered on cursor
                cx, cy = win32api.GetCursorPos()
                sw = ctypes.windll.user32.GetSystemMetrics(0)  # screen width
                sh = ctypes.windll.user32.GetSystemMetrics(1)  # screen height
                ml = max(0, min(cx - screenShotWidth // 2, sw - screenShotWidth))
                mt = max(0, min(cy - screenShotHeight // 2, sh - screenShotHeight))
                mouse_region = (ml, mt, ml + screenShotWidth, mt + screenShotHeight)
                frame = self.camera.grab(region=mouse_region) if self.camera else None
            else:
                frame = self.camera.grab() if self.camera else None
            if frame is None:
                time.sleep(0.001)
                continue
            t_capture_done = time.perf_counter()

            image = np.array(frame)
            if image.shape[2] == 4:
                image = image[:, :, :3]

            # ----- Read LIVE config from tkinter vars (real-time, no restart) -----
            cur_fov = self.fov_var.get()
            cur_smooth = self.smooth_var.get()
            cur_amp = self.amp_var.get()
            cur_conf = self.conf_var.get()
            cur_target = TARGET_OPTIONS.get(self.target_var.get(), "head")
            cur_key = KEY_OPTIONS.get(self.key_var.get(), 0x02)
            aim_on = self.aim_enabled_var.get()
            show_preview = self.visuals_var.get()
            render_counter += 1
            do_render = show_preview and (render_counter % RENDER_EVERY_N == 0)

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
            else:
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
                    # Decode bbox: cx = (raw_x + grid_x) * stride, cy = (raw_y + grid_y) * stride
                    #              w  = exp(raw_w) * stride,        h  = exp(raw_h) * stride
                    dec_cx = (boxes_raw[:, 0] + gx) * gs
                    dec_cy = (boxes_raw[:, 1] + gy) * gs
                    dec_w  = np.exp(boxes_raw[:, 2]) * gs
                    dec_h  = np.exp(boxes_raw[:, 3]) * gs
                    obj_conf = boxes_raw[:, 4]             # already sigmoid
                    cls_conf = boxes_raw[:, 5:]            # already sigmoid
                    nc = cls_conf.shape[1]
                    if nc > 1:
                        class_ids = np.argmax(cls_conf, axis=1)
                        class_max = np.max(cls_conf, axis=1)
                    else:
                        class_ids = np.zeros(len(obj_conf), dtype=int)
                        class_max = cls_conf[:, 0]
                    confs = obj_conf * class_max            # final score
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
                    raw_t = np.transpose(raw, (0, 2, 1))  # [1, 8400, 5]
                    # raw_t format per row: [cx, cy, w, h, conf_cls0, conf_cls1, ...]
                    # For single-class: col4 is the class confidence
                    boxes = raw_t[0]  # [8400, 5+]
                    nc = boxes.shape[1] - 4  # number of classes
                    # Get max class confidence and class id per box
                    if nc > 1:
                        class_confs = boxes[:, 4:]
                        class_ids = np.argmax(class_confs, axis=1)
                        confs = np.max(class_confs, axis=1)
                    else:
                        confs = boxes[:, 4]
                        class_ids = np.zeros(len(confs), dtype=int)
                    # Filter by confidence
                    mask = confs > cur_conf
                    boxes_f = boxes[mask]
                    confs_f = confs[mask]
                    class_ids_f = class_ids[mask]
                    # Convert cx,cy,w,h to x1,y1,x2,y2
                    pred = []
                    if len(boxes_f) > 0:
                        cx, cy, w, h = boxes_f[:, 0], boxes_f[:, 1], boxes_f[:, 2], boxes_f[:, 3]
                        x1 = cx - w / 2
                        y1 = cy - h / 2
                        x2 = cx + w / 2
                        y2 = cy + h / 2
                        # Simple NMS using torchvision-style or manual
                        dets = torch.tensor(np.stack([x1, y1, x2, y2, confs_f, class_ids_f.astype(np.float32)], axis=1))
                        # Sort by confidence descending, keep top 10
                        order = torch.argsort(dets[:, 4], descending=True)
                        dets = dets[order[:50]]
                        # Simple greedy NMS
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
            head_boxes = []  # Head bounding boxes for 头身 models
            display = image.copy() if do_render else None
            model_name = self.model_var.get()
            # Detect head+body model: "头身" OR filename contains class-head mapping like "0警1头2匪3头"
            is_headbody_model = "头身" in model_name or "头" in model_name
            # Parse explicit head/body class IDs from filename (e.g. "0警1头2匪3头")
            # Pattern: digit + label, where "头" = head class, anything else = body class
            head_cls_ids = set()
            body_cls_ids = set()
            ct_body_cls = set()
            t_body_cls = set()
            if is_headbody_model:
                import re as _re
                # Match patterns like "0警", "1头", "2匪", "3头" in filename
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
            # Team filter: depends on model type
            if has_explicit_cls:
                # 4-class model: CT-body/T-body are separate classes
                if cur_team == "ct":
                    enemy_body_cls = t_body_cls   # I am CT → aim at T bodies
                elif cur_team == "t":
                    enemy_body_cls = ct_body_cls   # I am T → aim at CT bodies
                else:
                    enemy_body_cls = body_cls_ids   # aim at all bodies
                enemy_cls = None  # not used in explicit mode
            else:
                # 2-class model (cs2_320 convention): ct=0, t=1
                if cur_team == "ct":
                    enemy_cls = {1}
                elif cur_team == "t":
                    enemy_cls = {0}
                else:
                    enemy_cls = None     # None = accept all classes
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

            if is_headbody_model and has_explicit_cls and len(all_dets) > 0:
                # Explicit class-ID model (e.g. 0警1头2匪3头): use class IDs directly
                for d in all_dets:
                    cls_id = d["cls"]
                    if cls_id in head_cls_ids:
                        d["_role"] = "head"
                        head_boxes.append(d)
                        if display is not None:
                            color = (0, 255, 255)
                            label = f"HEAD(c{cls_id}) {d['conf']:.0%}"
                            cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                            cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)
                    elif cls_id in body_cls_ids:
                        d["_role"] = "body"
                        # Apply team filter for bodies
                        if enemy_body_cls is None or cls_id in enemy_body_cls:
                            targets.append(d)
                        if display is not None:
                            is_enemy = (enemy_body_cls is None or cls_id in enemy_body_cls)
                            color = (0, 0, 255) if is_enemy else (255, 180, 0)
                            side = "CT" if cls_id in ct_body_cls else "T"
                            label = f"{side}(c{cls_id}) {d['conf']:.0%}"
                            cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                            cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    else:
                        # Unknown class — treat as body, add to targets
                        d["_role"] = "body"
                        targets.append(d)
                        if display is not None:
                            cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], (0, 255, 0), 2)
                            cv2.putText(display, f"c{cls_id} {d['conf']:.0%}", (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            elif is_headbody_model and len(all_dets) > 0:
                # Geometric containment model (e.g. GO_头身): small box inside big box = head
                all_dets.sort(key=lambda d: d["area"], reverse=True)  # large first
                for i, d in enumerate(all_dets):
                    d["_role"] = "body"  # default
                    # Check if this box's center is inside any larger box
                    dx, dy = d["mid_x"], d["mid_y"]
                    for j in range(i):
                        big = all_dets[j]
                        bx1, by1, bx2, by2 = big["xyxy"]
                        if bx1 <= dx <= bx2 and by1 <= dy <= by2 and d["area"] < big["area"] * 0.5:
                            d["_role"] = "head"
                            break
                for d in all_dets:
                    if d["_role"] == "head":
                        head_boxes.append(d)
                        if display is not None:
                            color = (0, 255, 255)
                            label = f"HEAD(c{d['cls']}) {d['conf']:.0%}"
                            cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                            cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)
                    else:
                        targets.append(d)
                        if display is not None:
                            color = (0, 0, 255)
                            label = f"BODY(c{d['cls']}) {d['conf']:.0%}"
                            cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                            cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            else:
                # Normal model (no 头身): use standard enemy filter
                for d in all_dets:
                    is_enemy = (enemy_cls is None or d["cls"] in enemy_cls)
                    if is_enemy:
                        targets.append(d)
                    if display is not None:
                        if enemy_cls is None:
                            color = (0, 255, 0)
                        elif is_enemy:
                            color = (0, 0, 255)
                        else:
                            color = (255, 180, 0)
                        label = f"{'CT' if d['cls'] == 0 else 'T'} {d['conf']:.0%}"
                        cv2.rectangle(display, d["xyxy"][:2], d["xyxy"][2:], color, 2)
                        cv2.putText(display, label, (d["xyxy"][0], max(20, d["xyxy"][1]-8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # --- Aim assist ---
            cur_y_offset = self.crosshair_y_offset_var.get()
            # Key state comes from background 1000Hz polling thread
            keyDown = self._key_is_down

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

                    cur_aim_mode = AIM_MODE_OPTIONS.get(self.aim_mode_var.get(), "aimbot")
                    raw_dist = (rawX**2 + rawY**2) ** 0.5

                    if cur_aim_mode == "assist":
                        # --- Aim Assist mode ---
                        # Additive pull toward target. User keeps full mouse control.
                        # Pull strength = proportional to offset, scaled by proximity:
                        #   - Far from target (near FOV edge): weak pull
                        #   - Close to target: stronger pull (helps stick)
                        # The pull is a fraction of the offset, much weaker than aimbot.
                        assist_fov = max(cur_fov, 100)  # effective FOV for falloff calc
                        # Proximity factor: 1.0 when on target, ~0.2 at FOV edge
                        proximity = max(0.0, 1.0 - (raw_dist / assist_fov))
                        # Pull strength: base 15-30% of offset, boosted by proximity
                        pull_pct = 0.15 + 0.20 * proximity  # 15% at edge → 35% on target
                        pull_pct /= max(cur_smooth, 1.0)     # user smooth still applies
                        moveX = rawX * pull_pct * cur_amp
                        moveY = rawY * pull_pct * cur_amp

                        # Softer clamp for assist (half of aimbot max)
                        assist_max = MAX_PIXEL_DELTA * 0.5
                        move_mag = (moveX**2 + moveY**2) ** 0.5
                        if move_mag > assist_max:
                            scale_f = assist_max / move_mag
                            moveX *= scale_f
                            moveY *= scale_f

                        mX, mY = round(moveX), round(moveY)
                        # Tighter dead zone for assist
                        if abs(mX) <= 1 and abs(mY) <= 1:
                            mX, mY = 0, 0

                        if keyDown and (mX != 0 or mY != 0):
                            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, mX, mY, 0, 0)
                            if now_t - aim_log_timer > 1:
                                print(f"[ASSIST] raw=({rawX:.1f},{rawY:.1f}) dist={raw_dist:.1f} pull={pull_pct:.0%} move=({mX},{mY})")
                                aim_log_timer = now_t
                    else:
                        # --- Aimbot mode (original) ---
                        # During active spray, suppress Y-axis aim correction.
                        # Vertical control is handled entirely by the recoil pattern;
                        # aim only tracks horizontally (X) for spray transfer.
                        spraying = (spray_start_time > 0 and cur_lmb and last_recoil_idx >= 1)
                        if spraying:
                            rawY = 0.0

                        # Osiris-style smoothing: offset / smooth
                        smooth_div = max(cur_smooth, 1.0)
                        moveX = rawX * cur_amp / smooth_div
                        moveY = rawY * cur_amp / smooth_div

                        # Clamp per-frame movement to MAX_PIXEL_DELTA (anti-overshoot)
                        move_mag = (moveX**2 + moveY**2) ** 0.5
                        if move_mag > MAX_PIXEL_DELTA:
                            scale_f = MAX_PIXEL_DELTA / move_mag
                            moveX *= scale_f
                            moveY *= scale_f

                        mX, mY = round(moveX), round(moveY)

                        # Dead zone: suppress sub-pixel jitter when nearly on target
                        if abs(mX) <= 1 and abs(mY) <= 1 and raw_dist < 3:
                            mX, mY = 0, 0

                        if keyDown and (mX != 0 or mY != 0):
                            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, mX, mY, 0, 0)
                            if now_t - aim_log_timer > 1:
                                print(f"[AIM] raw=({rawX:.1f},{rawY:.1f}) dist={raw_dist:.1f} move=({mX},{mY}) recoil_off=({recoil_accum_x:.0f},{recoil_accum_y:.0f})")
                                aim_log_timer = now_t

                    if display is not None:
                        cv2.circle(display, (int(aim_x_abs), int(aim_y_abs)), 5, (0, 0, 255), -1)

            # --- Recoil compensation (unified with aim, lerp-smoothed) ---
            cur_lmb = self._recoil_key_is_down
            rc_weapon = self.recoil_weapon_var.get()
            rc_strength = self.recoil_strength_var.get()
            rc_mag = get_mag_size(rc_weapon)
            rc_smooth = max(self.recoil_smooth_var.get(), 1)
            rc_enabled = self.recoil_enabled_var.get()

            if rc_enabled and rc_mag > 0 and rc_strength > 0:
                if cur_lmb and not prev_lmb:
                    # LMB just pressed — start spray
                    spray_start_time = time.perf_counter()
                    last_recoil_idx = -1
                    recoil_target_x = 0.0
                    recoil_target_y = 0.0
                    recoil_current_x = 0.0
                    recoil_current_y = 0.0
                    recoil_accum_x = 0.0
                    recoil_accum_y = 0.0

                if cur_lmb and spray_start_time > 0:
                    # Calculate which bullet we're on using per-bullet timing
                    elapsed_ms = (time.perf_counter() - spray_start_time) * 1000.0
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
                self.root.after(0, self.status_var.set, f"运行中 | FPS: {fps:.1f} | 目标: {len(targets)}")

            if do_render and display is not None:
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
            else:
                # Yield CPU so key polling thread can run reliably
                time.sleep(0.001)

        self.root.after(0, self.stop_viewer)

    def stop_viewer(self):
        if not self.running and self.camera is None:
            return
        self.running = False
        try:
            if self.camera is not None:
                self.camera.release()
        except Exception:
            pass
        self.camera = None
        cv2.destroyAllWindows()
        self.status_var.set("已停止")

    def on_close(self):
        self._key_poll_running = False
        self.stop_viewer()
        self.root.destroy()


def main():
    root = tk.Tk()
    VisionViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
