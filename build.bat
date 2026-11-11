@echo off
setlocal
cd /d "%~dp0"
py -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1
py -m PyInstaller --noconfirm --clean --onefile --windowed --name PulsePC --icon assets\pulsepc.ico --add-data "assets;assets" main.py
if errorlevel 1 exit /b 1
echo.
echo Gotowe! Plik: dist\PulsePC.exe
pause
