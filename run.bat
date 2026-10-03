@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto missing
.venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 12)" >nul 2>nul
if errorlevel 1 goto missing
if "%~1"=="" (
  start "" ".venv\Scripts\pythonw.exe" -m voice_to_me run
) else (
  start "" ".venv\Scripts\pythonw.exe" -m voice_to_me %*
)
exit /b 0
:missing
echo Run setup.bat first. Voice to Me requires a local Python 3.12 environment.
pause
exit /b 1
