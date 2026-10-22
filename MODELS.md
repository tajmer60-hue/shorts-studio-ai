# Локальная речь

Первый ASR backend: [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small), конверсия Whisper small для CTranslate2. Model card указывает MIT. Зафиксирован revision `536b0662742c02347bc0e980a01041f333bce120`; все файлы около 486 MB. [Документация faster-whisper](https://github.com/SYSTRAN/faster-whisper) описывает word timestamps и Silero VAD.

Размещение: `models/faster-whisper-small` в проекте на D:. Веса и cache исключены из Git.
`download_speech_model.py` — явный запуск скачивания, с проверкой SHA-256 весов. Сам анализ не скачивает модель и не использует сеть.

В установленном окружении проекта есть faster-whisper/CTranslate2/ONNX Runtime и локальные CUDA DLL. Запуск приложения: `start.bat`. Старый небольшой portable-архив Lite не содержит AI-зависимости; новая кнопка речи требует окружения проекта, пока не собран отдельный AI-дистрибутив.

GPU: int8_float16, CPU: int8. Одна модель, блоки аудио до 62 секунд, mono 16 kHz; по завершении блока JSON checkpoint. Повтор использует завершённые блоки. Видео целиком в RAM не загружается.

Whisper timestamps приблизительные. SRT и предложения пауз требуют проверки; смысл, реакции и слова-паразиты автоматически безопасными для удаления не считаются. ASR не является семантическим монтажом или Smart Censor.
