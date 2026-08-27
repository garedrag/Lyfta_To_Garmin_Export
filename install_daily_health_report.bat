@echo off
setlocal

cd /d "%~dp0"

set "START_TIME=08:10"

for /f "tokens=1,* delims==" %%A in ('findstr /b "DAILY_REPORT_TIME=" ".env" 2^>nul') do set "START_TIME=%%B"

"C:\Program Files\Python311\python.exe" "%~dp0garmin_health_reporter.py" --install-task %START_TIME%
if errorlevel 1 (
    echo Failed to install scheduled health report.
    pause
    exit /b 1
)

echo Installed daily Garmin health report at %START_TIME%.
pause
