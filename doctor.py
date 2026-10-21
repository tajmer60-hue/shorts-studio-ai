"""Read-only diagnostics; no installations, downloads, model loading or settings changes."""
import argparse
import ctypes
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return dict(exit_code=result.returncode, output=(result.stdout + result.stderr).strip())
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(error=str(error))


def diagnostics():
    from cuda_runtime import configure_cuda
    cuda_directories = configure_cuda()
    root = Path(__file__).resolve().parent
    result = dict(python=dict(version=platform.python_version(), executable=sys.executable,
        isolated=sys.prefix != sys.base_prefix), windows=platform.platform(), packages={}, tools={},
        cuda={'process_local_dll_directories': cuda_directories})
    for name in ('PySide6', 'faster-whisper', 'ctranslate2', 'numpy', 'opencv-python',
                 'scenedetect', 'librosa', 'soundfile', 'onnxruntime', 'av', 'pytest',
                 'torch', 'transformers', 'nvidia-cublas-cu12', 'nvidia-cudnn-cu12',
                 'nvidia-cuda-runtime-cu12', 'nvidia-cuda-nvrtc-cu12'):
        try:
            result['packages'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result['packages'][name] = None
    for name in ('git', 'ffmpeg', 'ffmpeg-nvenc', 'ffprobe', 'nvidia-smi', 'nvcc'):
        local = root / 'tools' / (name + '.exe')
        resolved = str(local) if local.is_file() else shutil.which(name)
        result['tools'][name] = dict(path=resolved)
        if resolved:
            args = [resolved, '--version' if name in ('git', 'nvcc') else '-version']
            if name == 'nvidia-smi':
                args = [resolved, '--query-gpu=name,driver_version,memory.total,memory.free', '--format=csv,noheader']
            result['tools'][name].update(command(args))
    if sys.platform == 'win32':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ('total', 'available', 'total_page',
                'available_page', 'total_virtual', 'available_virtual', 'extended')]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result['ram'] = dict(total_GiB=round(memory.total / 2**30, 2), available_GiB=round(memory.available / 2**30, 2))
        try:
            driver = ctypes.WinDLL('nvcuda.dll')
            version = ctypes.c_int()
            result['cuda']['cuInit'] = driver.cuInit(0)
            driver.cuDriverGetVersion(ctypes.byref(version))
            result['cuda']['driver_api_version'] = version.value
        except OSError as error:
            result['cuda']['driver_error'] = str(error)
        result['cuda']['dlls'] = {}
        for name in ('cublas64_12.dll', 'cudnn64_9.dll', 'cudnn64_8.dll', 'cudart64_12.dll'):
            try:
                ctypes.WinDLL(name)
                result['cuda']['dlls'][name] = 'loadable'
            except OSError:
                result['cuda']['dlls'][name] = 'not loadable in current process search paths'
    result['disk'] = {}
    for drive in ('C:/', 'D:/') if os.name == 'nt' else ('/',):
        if Path(drive).exists():
            usage = shutil.disk_usage(drive)
            result['disk'][drive] = dict(free_GiB=round(usage.free / 2**30, 2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = json.dumps(diagnostics(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text)
