@echo off
setlocal
cd /d "%~dp0"
python "converter.py" %*
if errorlevel 1 pause
