@echo off
rem Codex 中转站切换助手 启动器（通用版）
chcp 65001 >nul
cd /d "%~dp0"
rem 方式1：本目录若放了 pythonw_path.txt（里面是本机 pythonw 完整路径）则用它
if exist "pythonw_path.txt" (
    set /p PW=<pythonw_path.txt
    if exist "%PW%" (
        start "" "%PW%" "%~dp0relay_switcher.py"
        exit /b
    )
)
rem 方式2：用 PATH 里的 pythonw
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0relay_switcher.py"
    exit /b
)
rem 方式3：用 PATH 里的 python
where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "%~dp0relay_switcher.py"
    exit /b
)
echo 找不到 python/pythonw。请安装 Python 3，或在本目录放 pythonw_path.txt 写明本机路径。
pause
