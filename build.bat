@echo off
setlocal
cd /d "%~dp0"
pyinstaller converter.spec
