@echo off
setlocal

set "TASK_NAME=LyftaToGarminDailySync"

schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
    echo Failed to remove scheduled task, or it was not installed.
    pause
    exit /b 1
)

echo Removed scheduled task "%TASK_NAME%".
pause
