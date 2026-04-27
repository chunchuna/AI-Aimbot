import os
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

from config import confidence, screenShotHeight, screenShotWidth

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ONNX_MODEL_PATH = os.path.join(SCRIPT_DIR, "yolov5s320Half.onnx")


class VisionViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Vision Viewer")
        self.root.geometry("900x520")
        self.root.minsize(760, 420)

        self.windows = []
        self.camera = None
        self.model = None
        self.running = False
        self.worker = None
        self.device_name = "Not initialized"

        self.status_var = tk.StringVar(value="Ready")
        self.device_var = tk.StringVar(value="Device: Not initialized")

        self._build_ui()
        self.refresh_windows()

    def _build_ui(self):
        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="Select a visible window to inspect:").pack(side=tk.LEFT)
        ttk.Button(top_frame, text="Refresh", command=self.refresh_windows).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(top_frame, text="Start Viewer", command=self.start_viewer).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(top_frame, text="Stop", command=self.stop_viewer).pack(side=tk.RIGHT)

        columns = ("index", "title", "process", "pid", "size")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("index", text="#")
        self.tree.heading("title", text="Window Title")
        self.tree.heading("process", text="Process")
        self.tree.heading("pid", text="PID")
        self.tree.heading("size", text="Size")
        self.tree.column("index", width=50, anchor=tk.CENTER)
        self.tree.column("title", width=430)
        self.tree.column("process", width=160)
        self.tree.column("pid", width=90, anchor=tk.CENTER)
        self.tree.column("size", width=100, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        bottom_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom_frame.pack(fill=tk.X)
        ttk.Label(bottom_frame, textvariable=self.device_var).pack(side=tk.LEFT)
        ttk.Label(bottom_frame, textvariable=self.status_var).pack(side=tk.RIGHT)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def refresh_windows(self):
        if self.running:
            messagebox.showinfo("Viewer Running", "Stop the viewer before refreshing windows.")
            return

        self.tree.delete(*self.tree.get_children())
        self.windows = []

        for window in pygetwindow.getAllWindows():
            title = window.title.strip()
            if not title:
                continue
            if window.width <= 0 or window.height <= 0:
                continue

            hwnd = getattr(window, "_hWnd", None)
            pid = "Unknown"
            process_name = "Unknown"
            if hwnd:
                try:
                    _, pid_value = win32process.GetWindowThreadProcessId(hwnd)
                    pid = str(pid_value)
                    if psutil is not None:
                        process_name = psutil.Process(pid_value).name()
                except Exception:
                    pass

            item = {
                "window": window,
                "title": title,
                "process": process_name,
                "pid": pid,
                "size": f"{window.width}x{window.height}",
            }
            self.windows.append(item)
            self.tree.insert(
                "",
                tk.END,
                values=(len(self.windows) - 1, title, process_name, pid, item["size"]),
            )

        self.status_var.set(f"Found {len(self.windows)} windows")

    def get_selected_window(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Window Selected", "Please select a window first.")
            return None

        values = self.tree.item(selected[0], "values")
        index = int(values[0])
        if index < 0 or index >= len(self.windows):
            messagebox.showerror("Invalid Selection", "The selected window is no longer available.")
            return None

        return self.windows[index]["window"]

    def load_model(self):
        if ort is None:
            raise RuntimeError("onnxruntime is not installed. Run: pip install onnxruntime_directml")
        if not os.path.exists(ONNX_MODEL_PATH):
            raise RuntimeError(f"ONNX model not found: {ONNX_MODEL_PATH}")
        if torch is None or non_max_suppression is None:
            raise RuntimeError("PyTorch is not installed. Run: pip install torch")

        providers = ort.get_available_providers()
        if "DmlExecutionProvider" in providers:
            chosen_provider = "DmlExecutionProvider"
            self.device_name = "DirectML (GPU)"
        elif "CUDAExecutionProvider" in providers:
            chosen_provider = "CUDAExecutionProvider"
            self.device_name = "CUDA (GPU)"
        else:
            chosen_provider = "CPUExecutionProvider"
            self.device_name = "CPU"

        self.device_var.set(f"Device: {self.device_name}")
        self.status_var.set("Loading local ONNX model...")
        self.root.update_idletasks()

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.model = ort.InferenceSession(ONNX_MODEL_PATH, sess_options=so, providers=[chosen_provider])
        self.status_var.set("Model loaded.")

    def start_viewer(self):
        if self.running:
            return

        if bettercam is None:
            messagebox.showerror("Missing Dependency", "bettercam is not installed. Run: pip install bettercam")
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
        right = left + screenShotWidth
        bottom = top + screenShotHeight
        region = (left, top, right, bottom)

        try:
            if self.model is None:
                self.load_model()
            self.camera = bettercam.create(region=region, output_color="BGRA", max_buffer_len=64)
            if self.camera is None:
                raise RuntimeError("Failed to create capture camera.")
            self.camera.start(target_fps=60, video_mode=True)
        except Exception as exc:
            self.status_var.set("Start failed")
            messagebox.showerror("Start Failed", str(exc))
            return

        self.running = True
        self.status_var.set("Viewer running. Press Q in preview window or click Stop.")
        self.worker = threading.Thread(target=self.viewer_loop, daemon=True)
        self.worker.start()

    def viewer_loop(self):
        last_time = time.time()
        frame_count = 0
        fps = 0.0

        while self.running:
            frame = self.camera.get_latest_frame() if self.camera is not None else None
            if frame is None:
                time.sleep(0.005)
                continue

            image = np.array(frame)
            if image.shape[2] == 4:
                image = image[:, :, :3]

            # Preprocess for ONNX: NHWC -> NCHW, float16, normalized
            im = np.array([image])
            im = im / 255.0
            im = im.astype(np.float16)
            im = np.moveaxis(im, 3, 1)  # NHWC -> NCHW

            try:
                outputs = self.model.run(None, {'images': im})
                pred = torch.from_numpy(outputs[0]).to('cpu')
                pred = non_max_suppression(pred, confidence, confidence, 0, False, max_det=10)
            except Exception as exc:
                self.root.after(0, self.status_var.set, f"Detection error: {exc}")
                break

            display = image.copy()
            for det in pred:
                if len(det) == 0:
                    continue
                for *xyxy, conf, cls in det:
                    if int(cls) != 0 or float(conf) < confidence:
                        continue
                    x1, y1, x2, y2 = int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])
                    cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    label = f"person {float(conf):.2f}"
                    cv2.putText(display, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            frame_count += 1
            now = time.time()
            if now - last_time >= 1.0:
                fps = frame_count / (now - last_time)
                frame_count = 0
                last_time = now
                self.root.after(0, self.status_var.set, f"Viewer running | FPS: {fps:.1f}")

            cv2.putText(display, f"Device: {self.device_name}", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(display, f"FPS: {fps:.1f}", (8, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imshow("AI Vision Viewer", display)

            if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
                break

        self.root.after(0, self.stop_viewer)

    def stop_viewer(self):
        if not self.running and self.camera is None:
            return

        self.running = False
        try:
            if self.camera is not None:
                self.camera.stop()
        except Exception:
            pass
        self.camera = None
        cv2.destroyAllWindows()
        self.status_var.set("Stopped")

    def on_close(self):
        self.stop_viewer()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = VisionViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
