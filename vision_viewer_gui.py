import os
import re
import threading
import time
import tkinter as tk
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

from config import confidence as _conf_default, screenShotHeight, screenShotWidth
from recoil_patterns import WEAPON_NAMES, get_recoil_offset, get_bullet_delta, get_fire_interval_ms, get_mag_size

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.py")


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

TARGET_OPTIONS = {
    "头部 (Head)": "head",
    "胸口 (Chest)": "chest",
    "身体中心 (Body)": "body",
    "最近位置 (Nearest)": "nearest",
}
TARGET_VALUE_TO_NAME = {v: k for k, v in TARGET_OPTIONS.items()}


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
        if name == "aaActivateKey":
            replacement = f'{name} = {hex(value)}'
        elif name in ("visuals", "cpsDisplay", "centerOfScreen", "headshot_mode", "useMask"):
            replacement = f'{name} = {value}'
        elif isinstance(value, float):
            replacement = f'{name} = {value}'
        elif isinstance(value, str):
            replacement = f'{name} = "{value}"'
        else:
            replacement = f'{name} = {value}'
        content = re.sub(rf'^{name}\s*=\s*.+$', replacement, content, flags=re.MULTILINE)

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

        self.key_var = tk.StringVar()
        cur_key = _read_config_hex("aaActivateKey", 0x02)
        self.key_var.set(KEY_CODE_TO_NAME.get(cur_key, "鼠标右键 (Right Click)"))

        self.visuals_var = tk.BooleanVar(value=True)
        self.crosshair_y_offset_var = tk.IntVar(value=_read_config_value("crosshairYOffset", 0, int))
        self.fps_var = tk.IntVar(value=_read_config_value("captureFPS", 60, int))

        # Recoil compensation
        self.recoil_weapon_var = tk.StringVar(value=_read_config_value("recoilWeapon", "关闭 (Off)", str))
        self.recoil_strength_var = tk.DoubleVar(value=_read_config_value("recoilStrength", 1.0, float))
        # Left-click state for tracking spray (polled by key poll thread)
        self._lmb_is_down = False

        # Thread-safe key state flag (polled at ~1000Hz by background thread)
        self._key_is_down = False
        self._key_poll_running = True
        self._key_poll_thread = threading.Thread(target=self._key_poll_loop, daemon=True)
        self._key_poll_thread.start()

        # Model selection
        self.available_models = scan_onnx_models()
        self.model_var = tk.StringVar()
        default_model = "yolov5s320Half.onnx"
        if default_model in self.available_models:
            self.model_var.set(default_model)
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

        # Aim enabled
        ttk.Checkbutton(right, text="启用自瞄", variable=self.aim_enabled_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(right, text="显示预览窗口", variable=self.visuals_var).pack(anchor="w", pady=2)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # Target part
        f1 = ttk.Frame(right); f1.pack(fill="x", pady=2)
        ttk.Label(f1, text="锁定位置:").pack(side="left")
        ttk.Combobox(f1, textvariable=self.target_var, values=list(TARGET_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")

        # Activate key
        f2 = ttk.Frame(right); f2.pack(fill="x", pady=2)
        ttk.Label(f2, text="自瞄按键:").pack(side="left")
        ttk.Combobox(f2, textvariable=self.key_var, values=list(KEY_OPTIONS.keys()),
                     state="readonly", width=20).pack(side="right")

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

        # Recoil strength
        f_rc2 = ttk.Frame(right); f_rc2.pack(fill="x", pady=2)
        ttk.Label(f_rc2, text="压枪强度:").pack(side="left")
        self.recoil_str_label = ttk.Label(f_rc2, text=f"{self.recoil_strength_var.get():.2f}")
        self.recoil_str_label.pack(side="right")
        tk.Scale(right, from_=0.0, to=100.0, orient="horizontal", variable=self.recoil_strength_var,
                 resolution=0.5, command=lambda v: self.recoil_str_label.configure(text=f"{float(v):.1f}")).pack(fill="x")
        ttk.Label(right, text="1.0=标准 <1弱补偿 >1强补偿 (按灵敏度调)", font=("", 8)).pack(anchor="w")

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=6)

        # Save button
        ttk.Button(right, text="保存配置到 config.py", command=self.save_config).pack(fill="x", pady=4)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -------------------------------------------------- key polling thread
    def _key_poll_loop(self):
        """Background thread: poll aim key at ~1000 Hz so we never miss a press."""
        while self._key_poll_running:
            try:
                cur_key = KEY_OPTIONS.get(self.key_var.get(), 0x02)
                if win32api is not None and win32api.GetAsyncKeyState(cur_key) & 0x8000:
                    self._key_is_down = True
                else:
                    self._key_is_down = False
                # Also track left mouse button for recoil spray counting
                if win32api is not None and win32api.GetAsyncKeyState(0x01) & 0x8000:
                    self._lmb_is_down = True
                else:
                    self._lmb_is_down = False
            except Exception:
                self._key_is_down = False
                self._lmb_is_down = False
            time.sleep(0.001)  # 1ms = ~1000 Hz polling

    # -------------------------------------------------------- config save
    def save_config(self):
        vals = {
            "aaFOV": self.fov_var.get(),
            "aaTargetPart": TARGET_OPTIONS.get(self.target_var.get(), "head"),
            "aaSmoothFactor": round(self.smooth_var.get(), 1),
            "aaActivateKey": KEY_OPTIONS.get(self.key_var.get(), 0x02),
            "aaMovementAmp": round(self.amp_var.get(), 2),
            "confidence": round(self.conf_var.get(), 2),
            "crosshairYOffset": self.crosshair_y_offset_var.get(),
            "captureFPS": self.fps_var.get(),
            "recoilWeapon": self.recoil_weapon_var.get(),
            "recoilStrength": round(self.recoil_strength_var.get(), 2),
        }
        try:
            save_config_values(vals)
            messagebox.showinfo("成功", "配置已保存到 config.py\n(当前运行中的设置已实时生效，无需重启)")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

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
            # Detect output format: v5=[1,N,85] vs v8=[1,5+nc,anchors]
            out_shape = new_model.get_outputs()[0].shape
            if len(out_shape) == 3 and out_shape[1] is not None and out_shape[2] is not None:
                out_fmt = "v8" if int(out_shape[1]) < int(out_shape[2]) else "v5"
            else:
                out_fmt = "v5"
            with self._model_lock:
                self.model = new_model
                self._model_input_size = (model_w, model_h)
                self._model_input_dtype = model_dtype
                self._model_input_name = inp.name
                self._model_output_format = out_fmt
            self.model_status_label.configure(text=f"已加载: {self.model_var.get()} ({model_w}x{model_h} {out_fmt})", foreground="green")
            print(f"[MODEL] Switched to: {path}  input={inp.name} {model_w}x{model_h} dtype={inp.type} format={out_fmt} out_shape={out_shape}")
        except Exception as e:
            self.model_status_label.configure(text=f"加载失败!", foreground="red")
            messagebox.showerror("模型加载失败", str(e))

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
        if len(out_shape) == 3 and out_shape[1] is not None and out_shape[2] is not None:
            self._model_output_format = "v8" if int(out_shape[1]) < int(out_shape[2]) else "v5"
        else:
            self._model_output_format = "v5"
        self.model_status_label.configure(text=f"已加载: {self.model_var.get()} ({model_w}x{model_h} {self._model_output_format})", foreground="green")
        self.status_var.set("模型已加载。")
        print(f"[MODEL] Loaded: {path}  input={inp.name} {model_w}x{model_h} dtype={self._model_input_dtype} format={self._model_output_format}")

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
        left = ((window.left + window.right) // 2) - (screenShotWidth // 2)
        top = window.top + (window.height - screenShotHeight) // 2
        region = (left, top, left + screenShotWidth, top + screenShotHeight)
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
        self.status_var.set("运行中 | 按 Q 关闭预览窗口")
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
        recoil_accum_x = 0.0     # Cumulative recoil mouse offset applied
        recoil_accum_y = 0.0

        # EMA output smoothing to reduce jitter
        ema_mx = 0.0             # EMA-smoothed mouse move X
        ema_my = 0.0             # EMA-smoothed mouse move Y

        print("===== Aim loop started =====")
        print(f"  win32api loaded = {win32api is not None}")
        print(f"  screenShot = {screenShotWidth}x{screenShotHeight}")
        print(f"  target_fps = {current_fps}")
        print("=============================")

        while self.running:
            t_frame_start = time.perf_counter()
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
            im = np.expand_dims(im_resized, 0).astype(model_dtype) / 255.0
            im = np.ascontiguousarray(np.moveaxis(im, 3, 1))

            t_infer_start = time.perf_counter()
            try:
                with self._model_lock:
                    input_name = self._model_input_name
                    out_fmt = self._model_output_format
                    outputs = self.model.run(None, {input_name: im})
                raw = outputs[0]

                if out_fmt == "v8":
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
            display = image.copy() if do_render else None
            for det in pred:
                if len(det) == 0:
                    continue
                for *xyxy, conf_val, cls in det:
                    if int(cls) != 0 or float(conf_val) < cur_conf:
                        continue
                    # Scale coordinates from model space back to capture space
                    x1 = float(xyxy[0]) * scale_x
                    y1 = float(xyxy[1]) * scale_y
                    x2 = float(xyxy[2]) * scale_x
                    y2 = float(xyxy[3]) * scale_y
                    mid_x = (x1 + x2) / 2
                    mid_y = (y1 + y2) / 2
                    box_h = y2 - y1
                    dist = ((mid_x - cWidth)**2 + (mid_y - cHeight)**2) ** 0.5
                    targets.append({"mid_x": mid_x, "mid_y": mid_y, "box_h": box_h,
                                    "dist": dist, "conf": float(conf_val),
                                    "xyxy": (int(x1), int(y1), int(x2), int(y2))})
                    if display is not None:
                        cv2.rectangle(display, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                        cv2.putText(display, f"{float(conf_val):.0%}", (int(x1), max(20, int(y1)-8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            # --- Aim assist ---
            cur_y_offset = self.crosshair_y_offset_var.get()
            # Key state comes from background 1000Hz polling thread
            keyDown = self._key_is_down

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
                    # Y percentages from top of bounding box:
                    #   head  = 8% from top (head center)
                    #   chest = 35% from top (upper chest)
                    #   body  = 50% from top (center mass)
                    #   nearest = 50% (center)
                    x1_box, y1_box = t["xyxy"][0], t["xyxy"][1]
                    x2_box = t["xyxy"][2]
                    box_w = x2_box - x1_box
                    if cur_target == "head":
                        aim_y_abs = y1_box + box_h * 0.08
                        # Head X: use center of upper 15% of box (more accurate
                        # when model is side-facing, since head is at the top)
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

                    # Raw pixel offset from screen center to aim point
                    rawX = aim_x_abs - cWidth
                    rawY = aim_y_abs - (cHeight + cur_y_offset)
                    raw_dist = (rawX**2 + rawY**2) ** 0.5

                    # --- Proportional move + EMA smoothing ---
                    # Step 1: Proportional — move a fraction of remaining distance.
                    #   smooth=1 → move 100% per frame (instant snap)
                    #   smooth=3 → move 33% per frame (smooth tracking)
                    #   smooth=5 → move 20% per frame (very smooth)
                    # amp scales the base speed.
                    frac = cur_amp / max(cur_smooth, 0.5)
                    desiredX = rawX * frac
                    desiredY = rawY * frac

                    # Step 2: EMA on output — filters detection jitter.
                    # Higher EMA_ALPHA = more responsive, lower = smoother
                    EMA_ALPHA = 0.5
                    ema_mx = EMA_ALPHA * desiredX + (1 - EMA_ALPHA) * ema_mx
                    ema_my = EMA_ALPHA * desiredY + (1 - EMA_ALPHA) * ema_my

                    mX, mY = round(ema_mx), round(ema_my)

                    # Dead zone: ignore sub-pixel jitter when on target
                    if abs(mX) <= 1 and abs(mY) <= 1 and raw_dist < 3:
                        mX, mY = 0, 0
                        ema_mx, ema_my = 0.0, 0.0

                    if keyDown and (mX != 0 or mY != 0):
                        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, mX, mY, 0, 0)
                        # Log at most once per second to avoid spam
                        if now_t - aim_log_timer > 1:
                            print(f"[AIM] raw=({rawX:.1f},{rawY:.1f}) dist={raw_dist:.1f} move=({mX},{mY}) amp={cur_amp} smooth={cur_smooth}")
                            aim_log_timer = now_t
                    else:
                        # Reset EMA when not aiming to avoid stale momentum
                        ema_mx, ema_my = 0.0, 0.0

                    if display is not None:
                        cv2.circle(display, (int(aim_x_abs), int(aim_y_abs)), 5, (0, 0, 255), -1)

            # --- Recoil compensation (independent of aim assist) ---
            cur_lmb = self._lmb_is_down
            rc_weapon = self.recoil_weapon_var.get()
            rc_strength = self.recoil_strength_var.get()
            rc_mag = get_mag_size(rc_weapon)

            if rc_mag > 0 and rc_strength > 0:
                if cur_lmb and not prev_lmb:
                    # LMB just pressed — start spray
                    spray_start_time = time.perf_counter()
                    last_recoil_idx = -1
                    recoil_accum_x = 0.0
                    recoil_accum_y = 0.0

                if cur_lmb and spray_start_time > 0:
                    # Calculate which bullet we're on using per-bullet timing
                    elapsed_ms = (time.perf_counter() - spray_start_time) * 1000.0
                    # Walk through pattern, summing each bullet's delay to find current bullet
                    cumulative_ms = 0.0
                    bullet_idx = 0
                    for bi in range(rc_mag):
                        interval = get_fire_interval_ms(rc_weapon, bi)
                        if cumulative_ms + interval > elapsed_ms:
                            break
                        cumulative_ms += interval
                        bullet_idx = bi + 1
                    bullet_idx = min(bullet_idx, rc_mag - 1)

                    # Apply deltas for all new bullets since last frame
                    while last_recoil_idx < bullet_idx:
                        last_recoil_idx += 1
                        dx, dy = get_bullet_delta(rc_weapon, last_recoil_idx)
                        rc_mx = round(dx * rc_strength)
                        rc_my = round(dy * rc_strength)

                        if win32api is not None and (rc_mx != 0 or rc_my != 0):
                            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, rc_mx, rc_my, 0, 0)

                        recoil_accum_x += rc_mx
                        recoil_accum_y += rc_my

                    # Log recoil (at most once per second)
                    now_rc = time.time()
                    if now_rc - debug_timer > 0.5:
                        print(f"[RECOIL] weapon={rc_weapon} bullet={last_recoil_idx} accum=({recoil_accum_x:.0f},{recoil_accum_y:.0f}) strength={rc_strength}")

                if not cur_lmb and prev_lmb:
                    # LMB released — reset spray
                    spray_start_time = 0.0
                    last_recoil_idx = -1
                    recoil_accum_x = 0.0
                    recoil_accum_y = 0.0

            prev_lmb = cur_lmb

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
