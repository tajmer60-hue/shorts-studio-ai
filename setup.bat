@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
echo Python dependencies installed. Put FFmpeg and FFprobe in tools or PATH.
pause
exit /b 0
:error
echo Setup failed. Check the error above.
pause
exit /b 1
