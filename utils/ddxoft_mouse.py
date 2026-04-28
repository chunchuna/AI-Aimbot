"""
ddxoft Virtual Input Driver - Python wrapper
Based on Aimmy's C# implementation of ddxoft mouse driver.
Requires ddxoft.dll in the project root directory.
Must be run as Administrator.
"""
import ctypes
import os
import sys


class DdxoftMouse:
    """Python wrapper for ddxoft.dll virtual input driver."""

    def __init__(self):
        self._dll = None
        self._loaded = False

    def load(self, dll_path="ddxoft.dll"):
        """
        Load the ddxoft.dll library.
        Returns True on success, False on failure.
        """
        if self._loaded:
            return True

        # Resolve to absolute path
        if not os.path.isabs(dll_path):
            # Look relative to the main script directory
            script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            dll_path = os.path.join(script_dir, dll_path)

        if not os.path.exists(dll_path):
            print(f"[ddxoft] ERROR: {dll_path} not found!")
            print("[ddxoft] Please place ddxoft.dll in the project root directory.")
            return False

        try:
            self._dll = ctypes.WinDLL(dll_path)
        except OSError as e:
            print(f"[ddxoft] ERROR: Failed to load DLL: {e}")
            print("[ddxoft] Make sure you are running as Administrator.")
            return False

        # Verify the DLL works by calling DD_btn(0) - should return 1
        try:
            ret = self._dll.DD_btn(0)
            if ret != 1:
                print(f"[ddxoft] ERROR: DD_btn(0) returned {ret}, expected 1. Driver may not be compatible.")
                self._dll = None
                return False
        except Exception as e:
            print(f"[ddxoft] ERROR: DD_btn test call failed: {e}")
            self._dll = None
            return False

        self._loaded = True
        print("[ddxoft] Virtual input driver loaded successfully.")
        return True

    @property
    def is_loaded(self):
        return self._loaded

    def move_relative(self, dx, dy):
        """Move mouse by relative offset (dx, dy) pixels."""
        if not self._loaded:
            return -1
        return self._dll.DD_movR(int(dx), int(dy))

    def move_absolute(self, x, y):
        """Move mouse to absolute position (x, y)."""
        if not self._loaded:
            return -1
        return self._dll.DD_mov(int(x), int(y))

    def click(self, button_code):
        """
        Mouse button action.
        button_code: 1 = left down, 2 = left up, 4 = right down, 8 = right up,
                     16 = middle down, 32 = middle up
        """
        if not self._loaded:
            return -1
        return self._dll.DD_btn(int(button_code))

    def scroll(self, amount):
        """Mouse wheel scroll. Positive = up, negative = down."""
        if not self._loaded:
            return -1
        return self._dll.DD_whl(int(amount))


# Global singleton instance
ddxoft_instance = DdxoftMouse()
