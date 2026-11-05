@echo off
setlocal
if not exist "%~dp0.build-env\Scripts\pythonw.exe" (
  echo Python runtime missing: .build-env\Scripts\pythonw.exe
  pause
  exit /b 1
)
start "" "%~dp0.build-env\Scripts\pythonw.exe" "%~dp0daruma_standalone_gui.py"
