"""Smoke checks on synthetic data. Does not download speech or vision models."""
import json
from pathlib import Path
import sys
import traceback


def verify():
    from cuda_runtime import configure_cuda
    configure_cuda()
    results = {}

    def check(name, action):
        try:
            results[name] = dict(ok=True, detail=action())
        except Exception as error:
            results[name] = dict(ok=False, error=str(error), traceback=traceback.format_exc())

    def numerical():
        import numpy as np
        from scipy.signal import resample_poly
        signal = np.sin(np.arange(16000, dtype=np.float32) * (2 * np.pi * 440 / 16000))
        assert resample_poly(signal, 1, 2).shape == (8000,)
        return 'NumPy and SciPy: synthetic audio resampled'

    def images():
        import cv2
        import numpy as np
        from scenedetect import SceneManager, ContentDetector
        gray = cv2.cvtColor(np.zeros((32, 32, 3), dtype=np.uint8), cv2.COLOR_BGR2GRAY)
        assert gray.shape == (32, 32)
        manager = SceneManager()
        manager.add_detector(ContentDetector())
        return 'OpenCV transforms and PySceneDetect detector initialized'

    def sound():
        import librosa
        import numpy as np
        import soundfile as sf
        import io
        signal = np.zeros(16000, dtype=np.float32)
        with io.BytesIO() as buffer:
            sf.write(buffer, signal, 16000, format='WAV')
            buffer.seek(0)
            decoded, rate = sf.read(buffer)
            assert rate == 16000 and decoded.size == 16000
        rms = librosa.feature.rms(y=signal)
        assert not np.any(rms)
        return 'WAV encode/decode and librosa RMS on synthetic silence'

    def speech():
        from faster_whisper import WhisperModel
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        import numpy as np
        assert get_speech_timestamps(np.zeros(16000, dtype=np.float32), VadOptions()) == []
        return 'WhisperModel import; packaged Silero VAD inference on CPU, no speech weights downloaded'

    def runtime():
        import onnxruntime
        import av
        return dict(onnx_providers=onnxruntime.get_available_providers(), pyav_version=av.__version__)

    def cpu():
        import ctranslate2
        return sorted(ctranslate2.get_supported_compute_types('cpu'))

    def gpu():
        import ctranslate2
        return dict(device_count=ctranslate2.get_cuda_device_count(),
                    compute_types=sorted(ctranslate2.get_supported_compute_types('cuda')),
                    note='Device/type query only; does not prove a full Whisper model can execute')

    for name, action in [('numerical', numerical), ('images', images), ('sound', sound),
                         ('speech_cpu', speech), ('runtimes', runtime), ('ctranslate_cpu', cpu),
                         ('ctranslate_cuda_query', gpu)]:
        check(name, action)
    return results


if __name__ == '__main__':
    results = verify()
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding='utf-8')
    print(text)
    sys.exit(0 if all(item['ok'] for name, item in results.items() if name != 'ctranslate_cuda_query') else 1)
