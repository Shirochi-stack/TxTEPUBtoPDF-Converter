@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail
python -m PyInstaller --noconfirm --clean converter.spec
if errorlevel 1 goto :fail
echo.
echo Build finished: dist\converter.exe
exit /b 0
:fail
echo.
echo Build FAILED.
exit /b 1
