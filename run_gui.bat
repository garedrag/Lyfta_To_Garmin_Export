@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=C:\Program Files\Python311\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Python was not found at "%PYTHON_EXE%".
    echo Install Python 3.8 or newer, or edit PYTHON_EXE in this file.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    pause
    exit /b 1
)

start "" "%PYTHON_EXE%" "%~dp0lyfta_garmin_app.py"
