@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "E:\weixinai\python.exe" (
  "E:\weixinai\python.exe" netcheck.py
) else (
  python netcheck.py
)
pause
