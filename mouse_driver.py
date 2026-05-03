"""
Unified mouse movement driver with multiple backends.

Backends (user-selectable):
  "interception" — Interception kernel driver (~0.1ms, no INJECTED flag)
  "sendinput"    — Win32 SendInput API (~1-2ms)
  "win32api"     — win32api.mouse_event (~2-3ms, legacy)
  "auto"         — try Interception → SendInput

Usage:
    from mouse_driver import MouseDriver
    driver = MouseDriver("auto")
    driver.move(dx, dy)
    print(driver.backend_name)
"""

import ctypes
import ctypes.wintypes
import os

# Backend display names for GUI (key=config value, value=display label)
MOUSE_BACKEND_OPTIONS = {
    "auto":          "自动 (Auto)",
    "interception":  "Interception (内核级 最低延迟)",
    "sendinput":     "SendInput (用户级 推荐)",
    "win32api":      "win32api (兼容 较慢)",
}

# Reverse lookup: display label → config key
MOUSE_BACKEND_REVERSE = {v: k for k, v in MOUSE_BACKEND_OPTIONS.items()}


# ---------------------------------------------------------------------------
# win32api backend (legacy, always available via pywin32)
# ---------------------------------------------------------------------------

def _win32api_move(dx, dy):
    try:
        import win32api as _w32
        import win32con as _w32c
        _w32.mouse_event(_w32c.MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)
        return True
    except Exception as e:
        print(f"[MouseDriver] win32api.mouse_event failed: {e}")
        return False


# ---------------------------------------------------------------------------
# SendInput backend (always available on Windows)
# ---------------------------------------------------------------------------

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", _MOUSEINPUT)]

_MOUSEEVENTF_MOVE = 0x0001
_SendInput = ctypes.windll.user32.SendInput


def _sendinput_move(dx, dy):
    try:
        inp = _INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.mi.dx = int(dx)
        inp.mi.dy = int(dy)
        inp.mi.dwFlags = _MOUSEEVENTF_MOVE
        inp.mi.time = 0
        inp.mi.mouseData = 0
        inp.mi.dwExtraInfo = None
        _SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        return True
    except Exception as e:
        print(f"[MouseDriver] SendInput failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Interception backend (requires driver installed + interception.dll)
# ---------------------------------------------------------------------------

class _InterceptionMouseStroke(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_ushort),
        ("flags", ctypes.c_ushort),
        ("rolling", ctypes.c_short),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("information", ctypes.c_uint),
    ]

_INTERCEPTION_MOUSE_MOVE_RELATIVE = 0x000


def _find_interception_dll():
    """Search for interception.dll in common locations."""
    candidates = []
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "interception.dll"))
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    candidates.append(os.path.join(sys32, "interception.dll"))
    syswow = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64")
    candidates.append(os.path.join(syswow, "interception.dll"))
    for p in os.environ.get("PATH", "").split(";"):
        if p:
            candidates.append(os.path.join(p.strip(), "interception.dll"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class _InterceptionBackend:
    """Low-level Interception driver wrapper via ctypes."""

    def __init__(self):
        self._ctx = None
        self._device = 0
        self._dll = None
        self._ok = False
        self._error = ""
        self._init()

    def _init(self):
        dll_path = _find_interception_dll()
        if dll_path is None:
            self._error = "interception.dll not found (not in project dir, System32, or PATH)"
            return

        try:
            self._dll = ctypes.CDLL(dll_path)
        except OSError as e:
            self._error = f"Failed to load interception.dll: {e}"
            return

        try:
            self._dll.interception_create_context.restype = ctypes.c_void_p
            self._dll.interception_create_context.argtypes = []
            self._ctx = self._dll.interception_create_context()
        except Exception as e:
            self._error = f"interception_create_context failed: {e}"
            return

        if not self._ctx:
            self._error = "interception_create_context returned NULL (driver not installed?)"
            return

        self._dll.interception_send.restype = ctypes.c_int
        self._dll.interception_send.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(_InterceptionMouseStroke), ctypes.c_uint
        ]

        # Mouse devices are 11..20 in interception
        self._device = 11
        self._ok = True
        self._error = ""

    @property
    def available(self):
        return self._ok

    @property
    def error(self):
        return self._error

    def move(self, dx, dy):
        if not self._ok:
            return False
        stroke = _InterceptionMouseStroke()
        stroke.state = 0
        stroke.flags = _INTERCEPTION_MOUSE_MOVE_RELATIVE
        stroke.rolling = 0
        stroke.x = int(dx)
        stroke.y = int(dy)
        stroke.information = 0
        self._dll.interception_send(self._ctx, self._device, ctypes.byref(stroke), 1)
        return True

    def destroy(self):
        if self._ctx and self._dll:
            try:
                self._dll.interception_destroy_context.restype = None
                self._dll.interception_destroy_context.argtypes = [ctypes.c_void_p]
                self._dll.interception_destroy_context(self._ctx)
            except Exception:
                pass
            self._ctx = None

    def __del__(self):
        self.destroy()


# ---------------------------------------------------------------------------
# Check backend availability (can be called before creating driver)
# ---------------------------------------------------------------------------

def check_backends():
    """Return dict of backend availability and error messages."""
    results = {}

    # win32api
    try:
        import win32api
        results["win32api"] = {"available": True, "error": ""}
    except ImportError:
        results["win32api"] = {"available": False, "error": "pywin32 not installed (pip install pywin32)"}

    # SendInput (always available on Windows)
    results["sendinput"] = {"available": True, "error": ""}

    # Interception
    ic = _InterceptionBackend()
    results["interception"] = {"available": ic.available, "error": ic.error}
    if ic.available:
        ic.destroy()

    return results


# ---------------------------------------------------------------------------
# Unified MouseDriver
# ---------------------------------------------------------------------------

class MouseDriver:
    """Unified mouse driver with selectable backend.

    Args:
        backend: "auto", "interception", "sendinput", or "win32api"
    """

    def __init__(self, backend="auto"):
        self._interception = None
        self._backend = ""
        self._move_fn = None
        self._move_count = 0
        self._fail_count = 0

        requested = backend.lower().strip()
        print(f"[MouseDriver] Requested backend: {requested}")

        if requested == "interception":
            self._try_interception(fallback=False)
        elif requested == "sendinput":
            self._set_sendinput()
        elif requested == "win32api":
            self._set_win32api()
        elif requested == "auto":
            if not self._try_interception(fallback=True):
                self._set_sendinput()
        else:
            print(f"[MouseDriver] Unknown backend '{requested}', using SendInput")
            self._set_sendinput()

        print(f"[MouseDriver] Active backend: {self._backend}")

    def _try_interception(self, fallback=True):
        try:
            ic = _InterceptionBackend()
            if ic.available:
                self._interception = ic
                self._backend = "interception"
                self._move_fn = self._move_interception
                print("[MouseDriver] ✓ Interception driver loaded (kernel-level, lowest latency)")
                return True
            else:
                print(f"[MouseDriver] ✗ Interception not available: {ic.error}")
                if not fallback:
                    print("[MouseDriver]   → How to install:")
                    print("[MouseDriver]     1. Download: https://github.com/oblitum/Interception/releases")
                    print("[MouseDriver]     2. Run as admin: install-interception.exe /install")
                    print("[MouseDriver]     3. Reboot PC")
                    print("[MouseDriver]     4. Put interception.dll in project folder")
                    self._set_sendinput()
                return False
        except Exception as e:
            print(f"[MouseDriver] ✗ Interception init exception: {e}")
            if not fallback:
                self._set_sendinput()
            return False

    def _set_sendinput(self):
        self._backend = "sendinput"
        self._move_fn = self._move_sendinput
        print("[MouseDriver] ✓ Using SendInput (user-level, ~1-2ms)")

    def _set_win32api(self):
        try:
            import win32api
            self._backend = "win32api"
            self._move_fn = self._move_win32api
            print("[MouseDriver] ✓ Using win32api.mouse_event (legacy, ~2-3ms)")
        except ImportError:
            print("[MouseDriver] ✗ win32api not available (pip install pywin32)")
            print("[MouseDriver]   → Falling back to SendInput")
            self._set_sendinput()

    @property
    def backend_name(self):
        return self._backend

    @property
    def display_name(self):
        return MOUSE_BACKEND_OPTIONS.get(self._backend, self._backend)

    def move(self, dx, dy):
        """Move mouse by (dx, dy) pixels relative to current position."""
        self._move_count += 1
        if self._move_fn:
            result = self._move_fn(dx, dy)
            if not result:
                self._fail_count += 1
                if self._fail_count <= 5:
                    print(f"[MouseDriver] Move failed (backend={self._backend}, dx={dx}, dy={dy}, "
                          f"fail_count={self._fail_count})")
                elif self._fail_count == 6:
                    print(f"[MouseDriver] Suppressing further move-fail messages...")

    def _move_interception(self, dx, dy):
        return self._interception.move(dx, dy)

    def _move_sendinput(self, dx, dy):
        return _sendinput_move(dx, dy)

    def _move_win32api(self, dx, dy):
        return _win32api_move(dx, dy)

    def destroy(self):
        if self._interception:
            self._interception.destroy()
            self._interception = None
        if self._move_count > 0:
            print(f"[MouseDriver] Session stats: {self._move_count} moves, "
                  f"{self._fail_count} failures ({self._backend})")
