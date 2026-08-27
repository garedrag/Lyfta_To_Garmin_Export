@echo off
setlocal

cd /d "%~dp0"

"C:\Program Files\Python311\python.exe" "%~dp0garmin_health_reporter.py" --uninstall-task
pause
