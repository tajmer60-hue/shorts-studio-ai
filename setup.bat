@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto dependencies
python -m venv .venv
if not errorlevel 1 goto dependencies
py -3 -m venv .venv
if errorlevel 1 goto error
:dependencies
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
echo Python dependencies installed. Put FFmpeg and FFprobe in tools or PATH.
pause
exit /b 0
:error
echo Setup failed. Check the error above.
pause
exit /b 1
