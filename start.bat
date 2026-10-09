@echo off
rem Starts Vinyl Presence without a console window.
cd /d "%~dp0"
where pythonw >nul 2>nul || (
  echo Python isn't installed. Get it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)
rem Pillow is only needed to show album covers inside the window. Installed once, if missing.
python -c "import PIL" 2>nul || python -m pip install --user --quiet pillow
start "" pythonw "%~dp0app.py"
