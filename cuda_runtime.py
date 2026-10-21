"""Expose optional venv CUDA DLLs to this process only. Never changes Windows PATH."""
import importlib.metadata
import os
from pathlib import Path
import sys

_handles = []
_configured = None


def configure_cuda():
    global _configured
    if _configured is not None:
        return _configured
    directories = set()
    if sys.platform == 'win32':
        for package in ('nvidia-cublas-cu12', 'nvidia-cudnn-cu12', 'nvidia-cuda-runtime-cu12', 'nvidia-cuda-nvrtc-cu12'):
            try:
                dist = importlib.metadata.distribution(package)
                for file in dist.files or []:
                    if str(file).lower().endswith('.dll'):
                        parent = Path(dist.locate_file(file)).resolve().parent
                        if parent.is_dir():
                            directories.add(str(parent))
            except importlib.metadata.PackageNotFoundError:
                pass
        for directory in sorted(directories):
            _handles.append(os.add_dll_directory(directory))
        if directories:
            # CTranslate2 also uses native LoadLibrary; process PATH covers that loader.
            os.environ['PATH'] = os.pathsep.join(sorted(directories)) + os.pathsep + os.environ.get('PATH', '')
    _configured = sorted(directories)
    return _configured
