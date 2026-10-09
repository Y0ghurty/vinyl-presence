@echo off
rem Starts Vinyl Presence without a console window.
cd /d "%~dp0"
where pythonw >nul 2>nul || (
  echo Python isn't installed. Get it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)
rem Pillow (album covers) and pystray (tray icon) are optional extras. Installed once, if missing.
python -c "import PIL, pystray" 2>nul || python -m pip install --user --quiet -r "%~dp0requirements.txt"
start "" pythonw "%~dp0app.py"
