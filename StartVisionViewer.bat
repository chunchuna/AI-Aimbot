@echo off
setlocal
title AI Aimbot - Vision Viewer + Config

pushd "%~dp0"

echo Starting AI Aimbot (Vision Viewer + Config Panel)...
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found in PATH.
    echo Install Python 3.11 and try again.
    pause
    exit /b 1
)

python vision_viewer_gui.py

if errorlevel 1 (
    echo.
    echo ERROR: AI Vision Viewer exited with an error.
    echo Install dependencies if needed:
    echo pip install -r requirements.txt
    echo pip install bettercam
    echo pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
)

echo.
echo Done.
pause
popd
endlocal
