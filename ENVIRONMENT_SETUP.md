# Среда разработки

Используйте отдельную `.venv` проекта. Глобальные пакеты, PATH Windows, драйверы и системные настройки эти инструкции не меняют. Существующее окружение не требуется удалять или пересоздавать.

## Python и инструменты

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --only-binary=:all: --no-cache-dir -r requirements-dev.txt
.venv\Scripts\python -m pip check
.venv\Scripts\python doctor.py --output cache/environment.json
.venv\Scripts\python verify_ai.py cache/ai-verification.json
.venv\Scripts\python -m unittest discover -s tests -v
```

Если `.venv` уже существует и изолирована, используйте её без пересоздания. При отсутствии команды `python` можно использовать зарегистрированный `py -3`. На некоторых машинах launcher `py` не видит установленный Python; в этом случае используйте полный путь к python.exe. `setup.bat` сначала использует имеющуюся `.venv`, затем пытается `python`, затем `py`.

`requirements.txt` содержит GUI; `requirements-ai.txt` — библиотеки распознавания речи, анализа сцен/звука и их основные рантаймы; `requirements-dev.txt` также добавляет pytest. Предпочтительны готовые бинарные wheels: C++ Build Tools не нужны для этого набора. Машинную диагностику и кэши не коммитить.

`requirements-lock-windows-py313.txt` фиксирует полный состав проверенного Windows-окружения, включая дополнительные GPU-пакеты, если они установлены. Это снимок для Windows/Python 3.13, а не переносимый lock для всех платформ.

FFmpeg и FFprobe размещаются в `tools/` либо в уже существующем PATH. Изменять системный PATH не обязательно. Библиотеки бесплатные, платных API нет. Git может быть доступен из среды Codex; доступность из обычного терминала Windows следует проверять отдельно.

## GPU

Наличие GPU в `nvidia-smi` и успешный `cuInit` подтверждают работу драйвера, но строка CUDA Version не является доказательством установки CUDA Toolkit/cuDNN. `nvcc` нужен для сборки CUDA-кода, а не для обычного использования готовых Python wheels или NVENC.

Для выбранного faster-whisper/CTranslate2 нужны cuBLAS для CUDA 12 и cuDNN 9 для CUDA 12, доступные процессу. Проверять надо загрузку DLL и затем реальное распознавание моделью. Одна проверка `get_supported_compute_types('cuda')` этого не доказывает. Требования: [faster-whisper](https://github.com/SYSTRAN/faster-whisper#gpu).

Если DLL отсутствуют, CPU-путь остаётся доступным. Перед системной установкой CUDA/cuDNN, Visual C++ Runtime, драйверов или изменением Windows требуется подтверждение пользователя. Предпочтительны локальные библиотеки с путями только для процесса; их размер и место на диске нужно проверить до установки. Не скачивать крупные модели без явного уведомления и выбора папки.

В качестве необязательного локального пути подготовлен `requirements-gpu-windows.txt`: готовые Windows wheels NVIDIA устанавливаются только в `.venv`. Перед установкой нужно проверить свободное место: скачивание cuBLAS/cuDNN занимает около 1.2 GiB, установленным DLL и временным файлам требуется дополнительное место.

```powershell
.venv\Scripts\python -m pip install --only-binary=:all: --no-cache-dir -r requirements-gpu-windows.txt
.venv\Scripts\python verify_gpu.py cache/gpu-verification.json
```

`cuda_runtime.configure_cuda()` находит DLL из этих пакетов и добавляет каталоги только в текущий процесс. Ни реестр, ни пользовательский/системный PATH Windows не меняются. AI-worker должен вызвать этот helper до загрузки CTranslate2. `doctor.py`, `verify_ai.py` и `verify_gpu.py` вызывают его сами.

`verify_gpu.py` выполняет настоящее умножение маленьких матриц через CUDA/cuBLAS, проверяет результат и создаёт/освобождает cuDNN handle. Модель Whisper при этом не скачивается. Проверка полного распознавания и качества требует отдельного выбранного набора весов.

NVENC проверяется реальным кодированием синтетического кадра, а не наличием encoder в списке FFmpeg. Новая сборка FFmpeg может требовать более новый драйвер. Сохраняйте рабочий CPU-экспорт; совместимую portable-сборку для NVENC храните отдельно и не заменяйте исходную без необходимости. Текущий UI всё ещё использует CPU libx264.

## Что пока не нужно

PyTorch, transformers, CUDA Toolkit, Visual Studio и Docker не обязательны для текущего конвейера PySide6 + FFmpeg + faster-whisper/CTranslate2. Подключайте их, если конкретная выбранная модель потребует этого. Модели Smart Censor и семантического анализа ещё не выбраны: наличие базовых библиотек не означает готовую реализацию этих функций.

`verify_ai.py` проверяет синтетические данные и поставляемый с faster-whisper VAD. Он не скачивает Whisper/vision-веса и не выполняет полноценную проверку качества распознавания речи.
