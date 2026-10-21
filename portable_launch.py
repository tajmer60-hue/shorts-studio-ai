"""Portable entry point: all writable state stays beside the extracted app."""
from pathlib import Path
import os
import sys
import traceback


def prepare():
    root = Path(__file__).resolve().parent
    folders = {
        'SHORTS_STUDIO_DATA_DIR': root / 'data',
        'TEMP': root / 'cache' / 'temp', 'TMP': root / 'cache' / 'temp',
        'PYTHONPYCACHEPREFIX': root / 'cache' / 'pycache',
        'HF_HOME': root / 'models' / 'huggingface',
        'NUMBA_CACHE_DIR': root / 'cache' / 'numba',
        'MPLCONFIGDIR': root / 'cache' / 'matplotlib',
        'PIP_CACHE_DIR': root / 'cache' / 'pip',
        'XDG_CACHE_HOME': root / 'cache',
        'CUDA_CACHE_PATH': root / 'cache' / 'cuda',
        'TORCH_HOME': root / 'models' / 'torch',
    }
    for name, folder in folders.items():
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(folder)
    sys.pycache_prefix = str(root / 'cache' / 'pycache')
    os.chdir(root)
    return root


if __name__ == '__main__':
    root = prepare()
    log = root / 'data' / 'startup.log'
    try:
        with log.open('a', encoding='utf-8') as stream:
            sys.stdout = stream
            sys.stderr = stream
            from app import main
            result = main()
        raise SystemExit(result)
    except Exception:
        log.write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'Ошибка запуска. Подробности: {log}', 'Shorts Studio AI', 16)
        raise
