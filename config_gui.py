import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import re
import subprocess

# Key name to hex code mapping
KEY_OPTIONS = {
    "鼠标右键 (Right Click)": 0x02,
    "鼠标左键 (Left Click)": 0x01,
    "鼠标侧键1 (X1)": 0x05,
    "鼠标侧键2 (X2)": 0x06,
    "Shift": 0x10,
    "Ctrl": 0x11,
    "Alt": 0x12,
    "Caps Lock": 0x14,
    "空格 (Space)": 0x20,
    "E": 0x45,
    "F": 0x46,
    "R": 0x52,
    "X": 0x58,
    "Z": 0x5A,
}

# Reverse mapping: hex code -> display name
KEY_CODE_TO_NAME = {v: k for k, v in KEY_OPTIONS.items()}

# Target part options
TARGET_OPTIONS = {
    "头部 (Head)": "head",
    "身体 (Body)": "body",
    "最近位置 (Nearest)": "nearest",
}
TARGET_VALUE_TO_NAME = {v: k for k, v in TARGET_OPTIONS.items()}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")


def read_config():
    """Read current values from config.py"""
    config = {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    def extract(name, default, cast=str):
        pattern = rf'^{name}\s*=\s*(.+)$'
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            val = match.group(1).strip().strip('"').strip("'")
            try:
                return cast(val)
            except (ValueError, TypeError):
                return default
        return default

    config["aaFOV"] = extract("aaFOV", 150, int)
    config["aaTargetPart"] = extract("aaTargetPart", "head", str)
    config["aaSmoothFactor"] = extract("aaSmoothFactor", 3.0, float)
    config["aaMovementAmp"] = extract("aaMovementAmp", 0.4, float)
    config["confidence"] = extract("confidence", 0.4, float)
    config["visuals"] = extract("visuals", "False", str)
    config["cpsDisplay"] = extract("cpsDisplay", "True", str)
    config["screenShotHeight"] = extract("screenShotHeight", 320, int)
    config["screenShotWidth"] = extract("screenShotWidth", 320, int)

    # aaActivateKey needs special handling (hex value)
    match = re.search(r'^aaActivateKey\s*=\s*(.+)$', content, re.MULTILINE)
    if match:
        try:
            config["aaActivateKey"] = int(match.group(1).strip(), 0)
        except ValueError:
            config["aaActivateKey"] = 0x02
    else:
        config["aaActivateKey"] = 0x02

    return config


def write_config(config):
    """Write updated values back to config.py using regex replacement"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    def replace_val(content, name, value):
        if isinstance(value, str) and not value.startswith("0x"):
            replacement = f'{name} = "{value}"'
        elif isinstance(value, bool):
            replacement = f'{name} = {value}'
        elif isinstance(value, float):
            replacement = f'{name} = {value}'
        elif isinstance(value, int) and name == "aaActivateKey":
            replacement = f'{name} = {hex(value)}'
        else:
            replacement = f'{name} = {value}'
        pattern = rf'^{name}\s*=\s*.+$'
        return re.sub(pattern, replacement, content, flags=re.MULTILINE)

    content = replace_val(content, "aaFOV", config["aaFOV"])
    content = replace_val(content, "aaTargetPart", config["aaTargetPart"])
    content = replace_val(content, "aaSmoothFactor", config["aaSmoothFactor"])
    content = replace_val(content, "aaActivateKey", config["aaActivateKey"])
    content = replace_val(content, "aaMovementAmp", config["aaMovementAmp"])
    content = replace_val(content, "confidence", config["confidence"])
    content = replace_val(content, "screenShotHeight", config["screenShotHeight"])
    content = replace_val(content, "screenShotWidth", config["screenShotWidth"])

    vis_str = "True" if config["visuals"] else "False"
    content = re.sub(r'^visuals\s*=\s*.+$', f'visuals = {vis_str}', content, flags=re.MULTILINE)

    cps_str = "True" if config["cpsDisplay"] else "False"
    content = re.sub(r'^cpsDisplay\s*=\s*.+$', f'cpsDisplay = {cps_str}', content, flags=re.MULTILINE)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)


class ConfigGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Aimbot 配置面板")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e2e")

        # Set window icon if possible
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self.config = read_config()
        self._build_styles()
        self._build_ui()
        self._center_window()

    def _build_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Dark theme colors
        bg = "#1e1e2e"
        fg = "#cdd6f4"
        accent = "#89b4fa"
        entry_bg = "#313244"
        btn_bg = "#45475a"
        btn_active = "#585b70"
        green = "#a6e3a1"
        red = "#f38ba8"

        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg, font=("Microsoft YaHei UI", 10))
        style.configure("Header.TLabel", background=bg, foreground=accent, font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Section.TLabel", background=bg, foreground=accent, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TLabelframe", background=bg, foreground=accent, font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabelframe.Label", background=bg, foreground=accent, font=("Microsoft YaHei UI", 10, "bold"))

        style.configure("TCombobox", fieldbackground=entry_bg, background=entry_bg,
                         foreground=fg, arrowcolor=fg, font=("Microsoft YaHei UI", 10))
        style.map("TCombobox", fieldbackground=[("readonly", entry_bg)],
                  selectbackground=[("readonly", entry_bg)], selectforeground=[("readonly", fg)])

        style.configure("TScale", background=bg, troughcolor=entry_bg)

        style.configure("Green.TButton", background=green, foreground="#1e1e2e",
                         font=("Microsoft YaHei UI", 11, "bold"), padding=(20, 10))
        style.map("Green.TButton", background=[("active", "#74d491")])

        style.configure("Save.TButton", background=accent, foreground="#1e1e2e",
                         font=("Microsoft YaHei UI", 10, "bold"), padding=(15, 8))
        style.map("Save.TButton", background=[("active", "#6a9eea")])

        style.configure("Red.TButton", background=red, foreground="#1e1e2e",
                         font=("Microsoft YaHei UI", 10, "bold"), padding=(15, 8))
        style.map("Red.TButton", background=[("active", "#e07090")])

        style.configure("TCheckbutton", background=bg, foreground=fg, font=("Microsoft YaHei UI", 10))
        style.map("TCheckbutton", background=[("active", bg)])

        self.bg = bg
        self.fg = fg
        self.accent = accent
        self.entry_bg = entry_bg

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill="both", expand=True)

        # Title
        ttk.Label(main_frame, text="AI Aimbot 配置面板", style="Header.TLabel").pack(pady=(0, 15))

        # ============ Aim Assist Section ============
        aim_frame = ttk.LabelFrame(main_frame, text="  自瞄设置  ", padding=15)
        aim_frame.pack(fill="x", pady=(0, 10))

        # --- Target Part ---
        row1 = ttk.Frame(aim_frame)
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="锁定位置:").pack(side="left")
        self.target_var = tk.StringVar()
        current_target = self.config.get("aaTargetPart", "head")
        self.target_var.set(TARGET_VALUE_TO_NAME.get(current_target, "头部 (Head)"))
        target_combo = ttk.Combobox(row1, textvariable=self.target_var,
                                     values=list(TARGET_OPTIONS.keys()),
                                     state="readonly", width=25)
        target_combo.pack(side="right")

        # --- Activate Key ---
        row2 = ttk.Frame(aim_frame)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="自瞄按键:").pack(side="left")
        self.key_var = tk.StringVar()
        current_key = self.config.get("aaActivateKey", 0x02)
        self.key_var.set(KEY_CODE_TO_NAME.get(current_key, "鼠标右键 (Right Click)"))
        key_combo = ttk.Combobox(row2, textvariable=self.key_var,
                                  values=list(KEY_OPTIONS.keys()),
                                  state="readonly", width=25)
        key_combo.pack(side="right")

        # --- FOV ---
        row3 = ttk.Frame(aim_frame)
        row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="自瞄范围 (FOV):").pack(side="left")
        self.fov_label = ttk.Label(row3, text=str(self.config.get("aaFOV", 150)))
        self.fov_label.pack(side="right", padx=(10, 0))

        self.fov_var = tk.IntVar(value=self.config.get("aaFOV", 150))
        fov_scale = tk.Scale(aim_frame, from_=0, to=500, orient="horizontal",
                              variable=self.fov_var, length=350,
                              bg=self.bg, fg=self.fg, troughcolor=self.entry_bg,
                              highlightbackground=self.bg, activebackground=self.accent,
                              font=("Microsoft YaHei UI", 9),
                              command=lambda v: self.fov_label.configure(text=str(int(float(v)))))
        fov_scale.pack(fill="x", pady=(0, 4))

        fov_hint = ttk.Label(aim_frame, text="(0 = 无限制，推荐 100~300)", font=("Microsoft YaHei UI", 8))
        fov_hint.pack(anchor="w")

        # --- Smooth Factor ---
        row4 = ttk.Frame(aim_frame)
        row4.pack(fill="x", pady=4)
        ttk.Label(row4, text="自瞄平滑度:").pack(side="left")
        self.smooth_label = ttk.Label(row4, text=f"{self.config.get('aaSmoothFactor', 3.0):.1f}")
        self.smooth_label.pack(side="right", padx=(10, 0))

        self.smooth_var = tk.DoubleVar(value=self.config.get("aaSmoothFactor", 3.0))
        smooth_scale = tk.Scale(aim_frame, from_=1.0, to=10.0, orient="horizontal",
                                 variable=self.smooth_var, resolution=0.1, length=350,
                                 bg=self.bg, fg=self.fg, troughcolor=self.entry_bg,
                                 highlightbackground=self.bg, activebackground=self.accent,
                                 font=("Microsoft YaHei UI", 9),
                                 command=lambda v: self.smooth_label.configure(text=f"{float(v):.1f}"))
        smooth_scale.pack(fill="x", pady=(0, 4))

        smooth_hint = ttk.Label(aim_frame, text="(1.0 = 瞬间锁定，越大越平滑自然)", font=("Microsoft YaHei UI", 8))
        smooth_hint.pack(anchor="w")

        # --- Movement Amp ---
        row5 = ttk.Frame(aim_frame)
        row5.pack(fill="x", pady=4)
        ttk.Label(row5, text="鼠标移动倍率:").pack(side="left")
        self.amp_label = ttk.Label(row5, text=f"{self.config.get('aaMovementAmp', 0.4):.2f}")
        self.amp_label.pack(side="right", padx=(10, 0))

        self.amp_var = tk.DoubleVar(value=self.config.get("aaMovementAmp", 0.4))
        amp_scale = tk.Scale(aim_frame, from_=0.1, to=2.0, orient="horizontal",
                              variable=self.amp_var, resolution=0.05, length=350,
                              bg=self.bg, fg=self.fg, troughcolor=self.entry_bg,
                              highlightbackground=self.bg, activebackground=self.accent,
                              font=("Microsoft YaHei UI", 9),
                              command=lambda v: self.amp_label.configure(text=f"{float(v):.2f}"))
        amp_scale.pack(fill="x", pady=(0, 4))

        amp_hint = ttk.Label(aim_frame, text="(推荐 0.3~0.8，越大移动越快)", font=("Microsoft YaHei UI", 8))
        amp_hint.pack(anchor="w")

        # ============ Detection Section ============
        det_frame = ttk.LabelFrame(main_frame, text="  检测设置  ", padding=15)
        det_frame.pack(fill="x", pady=(0, 10))

        # --- Confidence ---
        row6 = ttk.Frame(det_frame)
        row6.pack(fill="x", pady=4)
        ttk.Label(row6, text="检测置信度:").pack(side="left")
        self.conf_label = ttk.Label(row6, text=f"{self.config.get('confidence', 0.4):.2f}")
        self.conf_label.pack(side="right", padx=(10, 0))

        self.conf_var = tk.DoubleVar(value=self.config.get("confidence", 0.4))
        conf_scale = tk.Scale(det_frame, from_=0.1, to=0.9, orient="horizontal",
                               variable=self.conf_var, resolution=0.05, length=350,
                               bg=self.bg, fg=self.fg, troughcolor=self.entry_bg,
                               highlightbackground=self.bg, activebackground=self.accent,
                               font=("Microsoft YaHei UI", 9),
                               command=lambda v: self.conf_label.configure(text=f"{float(v):.2f}"))
        conf_scale.pack(fill="x", pady=(0, 4))

        conf_hint = ttk.Label(det_frame, text="(越高越精准但可能漏检，推荐 0.3~0.6)", font=("Microsoft YaHei UI", 8))
        conf_hint.pack(anchor="w")

        # --- Screenshot size ---
        row7 = ttk.Frame(det_frame)
        row7.pack(fill="x", pady=4)
        ttk.Label(row7, text="截图尺寸:").pack(side="left")
        self.ss_label = ttk.Label(row7, text=str(self.config.get("screenShotHeight", 320)))
        self.ss_label.pack(side="right", padx=(10, 0))

        self.ss_var = tk.IntVar(value=self.config.get("screenShotHeight", 320))
        ss_scale = tk.Scale(det_frame, from_=128, to=640, orient="horizontal",
                             variable=self.ss_var, resolution=32, length=350,
                             bg=self.bg, fg=self.fg, troughcolor=self.entry_bg,
                             highlightbackground=self.bg, activebackground=self.accent,
                             font=("Microsoft YaHei UI", 9),
                             command=lambda v: self.ss_label.configure(text=str(int(float(v)))))
        ss_scale.pack(fill="x", pady=(0, 4))

        ss_hint = ttk.Label(det_frame, text="(越大检测范围越大但越慢，推荐 320)", font=("Microsoft YaHei UI", 8))
        ss_hint.pack(anchor="w")

        # ============ Other Options ============
        opt_frame = ttk.LabelFrame(main_frame, text="  其他选项  ", padding=15)
        opt_frame.pack(fill="x", pady=(0, 15))

        self.visuals_var = tk.BooleanVar(value=self.config.get("visuals", "False") == "True")
        ttk.Checkbutton(opt_frame, text="显示检测窗口 (Visuals)", variable=self.visuals_var).pack(anchor="w", pady=2)

        self.cps_var = tk.BooleanVar(value=self.config.get("cpsDisplay", "True") == "True")
        ttk.Checkbutton(opt_frame, text="显示 CPS (每秒校正次数)", variable=self.cps_var).pack(anchor="w", pady=2)

        # ============ Buttons ============
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=(5, 0))

        save_btn = ttk.Button(btn_frame, text="保存配置", style="Save.TButton", command=self.save_config)
        save_btn.pack(side="left", padx=(0, 10))

        reset_btn = ttk.Button(btn_frame, text="恢复默认", style="Red.TButton", command=self.reset_defaults)
        reset_btn.pack(side="left", padx=(0, 10))

        # Spacer
        ttk.Frame(btn_frame).pack(side="left", expand=True)

        start_btn = ttk.Button(btn_frame, text="保存并启动 Aimbot", style="Green.TButton", command=self.save_and_start)
        start_btn.pack(side="right")

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")

    def _gather_config(self):
        """Gather current GUI values into a config dict"""
        config = {}
        config["aaFOV"] = self.fov_var.get()
        config["aaTargetPart"] = TARGET_OPTIONS.get(self.target_var.get(), "head")
        config["aaSmoothFactor"] = round(self.smooth_var.get(), 1)
        config["aaActivateKey"] = KEY_OPTIONS.get(self.key_var.get(), 0x02)
        config["aaMovementAmp"] = round(self.amp_var.get(), 2)
        config["confidence"] = round(self.conf_var.get(), 2)
        config["visuals"] = self.visuals_var.get()
        config["cpsDisplay"] = self.cps_var.get()
        ss = self.ss_var.get()
        config["screenShotHeight"] = ss
        config["screenShotWidth"] = ss
        return config

    def save_config(self):
        config = self._gather_config()
        try:
            write_config(config)
            messagebox.showinfo("成功", "配置已保存到 config.py")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def reset_defaults(self):
        if not messagebox.askyesno("确认", "确定要恢复默认设置吗?"):
            return
        self.fov_var.set(150)
        self.fov_label.configure(text="150")
        self.target_var.set("头部 (Head)")
        self.key_var.set("鼠标右键 (Right Click)")
        self.smooth_var.set(3.0)
        self.smooth_label.configure(text="3.0")
        self.amp_var.set(0.4)
        self.amp_label.configure(text="0.40")
        self.conf_var.set(0.4)
        self.conf_label.configure(text="0.40")
        self.ss_var.set(320)
        self.ss_label.configure(text="320")
        self.visuals_var.set(False)
        self.cps_var.set(True)

    def save_and_start(self):
        config = self._gather_config()
        try:
            write_config(config)
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")
            return

        self.root.destroy()

        # Launch main.py in the same directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        main_script = os.path.join(script_dir, "main.py")
        subprocess.Popen([sys.executable, main_script], cwd=script_dir)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = ConfigGUI()
    app.run()
