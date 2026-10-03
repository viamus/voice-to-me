@echo off
setlocal
cd /d "%~dp0"
set "VOICE_UV_GPU="
set "VOICE_PIP_EXTRAS=dev"
where nvidia-smi >nul 2>nul
if errorlevel 1 goto detect_done
nvidia-smi --query-gpu=name --format=csv,noheader >nul 2>nul
if errorlevel 1 goto detect_done
set "VOICE_UV_GPU=--extra gpu"
set "VOICE_PIP_EXTRAS=dev,gpu"
:detect_done
where uv >nul 2>nul
if not errorlevel 1 goto use_uv
py -3.12 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -e ".[%VOICE_PIP_EXTRAS%]"
if errorlevel 1 goto failed
goto validate
:use_uv
uv --quiet --cache-dir .runtime\uv-cache sync --python 3.12 --no-python-downloads --locked --extra dev %VOICE_UV_GPU%
if errorlevel 1 goto failed
:validate
.venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'"
if errorlevel 1 goto failed
echo Setup complete. Open run.bat and choose Settings to configure Voice to Me.
echo Download the selected transcription model in Settings before recording if needed.
exit /b 0
:failed
echo Setup failed. Install Python 3.12 and inspect the error above.
pause
exit /b 1
