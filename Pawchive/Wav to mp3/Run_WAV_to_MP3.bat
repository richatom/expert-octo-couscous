@echo off
echo ============================================
echo   WAV to MP3 Converter - Setup ^& Run
echo ============================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is NOT installed!
    echo Download from: https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b
)

echo Launching WAV to MP3 Converter...
python "%~dp0wav_to_mp3.py"
pause
