@echo off
setlocal

cd /d "%~dp0"

set "TASK_NAME=LyftaToGarminDailySync"
set "RUNNER=%~dp0run_daily_sync.bat"
set "START_TIME=08:00"

for /f "tokens=1,* delims==" %%A in ('findstr /b "DAILY_SYNC_TIME=" ".env" 2^>nul') do set "START_TIME=%%B"

schtasks /Create /TN "%TASK_NAME%" /TR "\"%RUNNER%\"" /SC DAILY /ST %START_TIME% /F
if errorlevel 1 (
    echo Failed to install scheduled task.
    pause
    exit /b 1
)

echo Installed scheduled task "%TASK_NAME%" to run daily at %START_TIME%.
echo You can change the time in Windows Task Scheduler.
pause
