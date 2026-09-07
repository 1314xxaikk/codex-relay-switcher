@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PW="
if exist "pythonw_path.txt" set /p PW=<pythonw_path.txt
if defined PW if exist "%PW%" goto launch_file
where pythonw >nul 2>nul
if %errorlevel%==0 (
    set "PW=pythonw"
    goto launch_cmd
)
where python >nul 2>nul
if %errorlevel%==0 (
    set "PW=python"
    goto launch_cmd
)
echo Python not found. Create pythonw_path.txt with your full pythonw path next to this bat.
pause
exit /b

:launch_file
start "" "%PW%" "%~dp0relay_switcher.py"
exit /b

:launch_cmd
start "" %PW% "%~dp0relay_switcher.py"
exit /b
