@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=C:\Program Files\Python311\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Python was not found at "%PYTHON_EXE%".
    echo Install Python 3.8 or newer, or edit PYTHON_EXE in this file.
    exit /b 1
)

"%PYTHON_EXE%" "%~dp0lyfta_garmin_app.py" --sync
