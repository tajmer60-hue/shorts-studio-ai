@echo off
rem Устанавливает AI-зависимости и модель речи. Запускается двойным кликом.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Сначала один раз запустите setup.bat двойным кликом.
  pause
  exit /b 1
)
echo Ставлю зависимости анализа речи и видео...
.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
if errorlevel 1 goto error
echo Ставлю локальные CUDA-библиотеки для видеокарты NVIDIA...
.venv\Scripts\python.exe -m pip install -r requirements-gpu-windows.txt
if errorlevel 1 goto error
if exist models\faster-whisper-small\model.bin (
  echo Модель речи уже на месте, скачивание пропущено.
) else (
  echo Скачиваю модель речи около 486 МБ, один раз...
  .venv\Scripts\python.exe download_speech_model.py
  if errorlevel 1 goto error
)
echo.
echo Готово. Теперь запускайте start.bat двойным кликом.
pause
exit /b 0
:error
echo.
echo Установка прервалась. Скопируйте текст ошибки из этого окна и пришлите его.
pause
exit /b 1
