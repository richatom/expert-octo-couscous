@echo off
echo ============================================
echo  Pawchive Audio Downloader - Setup ^& Run
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is NOT installed!
    echo.
    echo Please download and install Python from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: During install, tick the box that says
    echo   "Add Python to PATH"
    echo.
    pause
    exit /b
)

echo Python found. Installing required packages...
python -m pip install requests --quiet

echo.
echo Launching Pawchive Audio Downloader...
echo.
python "%~dp0pawchive_downloader.py"
pause
