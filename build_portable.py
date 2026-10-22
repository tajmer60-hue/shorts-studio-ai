"""Build a self-contained Windows GUI archive from already installed dependencies."""
from pathlib import Path
import argparse
import os
import struct
import sys
import zipfile
from version import __version__


def imports(path):
    """Read PE import names so the GUI includes only its required Qt DLLs."""
    data = path.read_bytes()
    if data[:2] != b'MZ':
        return []
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    sections, optional_size = struct.unpack_from('<H', data, pe + 6)[0], struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    directories = optional + (112 if struct.unpack_from('<H', data, optional)[0] == 0x20b else 96)
    import_rva = struct.unpack_from('<I', data, directories + 8)[0]
    if not import_rva:
        return []
    table = optional + optional_size
    entries = [struct.unpack_from('<IIII', data, table + i * 40 + 8) for i in range(sections)]

    def offset(rva):
        for virtual_size, virtual_address, raw_size, raw_address in entries:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return raw_address + rva - virtual_address
        return rva

    cursor, result = offset(import_rva), []
    while any(data[cursor:cursor + 20]):
        name_rva = struct.unpack_from('<I', data, cursor + 12)[0]
        start = offset(name_rva)
        result.append(data[start:data.index(b'\0', start)].decode('ascii'))
        cursor += 20
    return result


def build(target):
    root = Path(__file__).resolve().parent
    base = Path(sys.base_prefix)
    packages = root / '.venv' / 'Lib' / 'site-packages'
    qt = packages / 'PySide6'
    files = {}

    def add(source, destination):
        if source.is_file():
            files[destination.replace('\\', '/')] = source

    for source in base.iterdir():
        if source.suffix.lower() in ('.exe', '.dll') or source.name == 'LICENSE.txt':
            add(source, 'runtime/' + source.name)
    for folder in ('Lib', 'DLLs'):
        for source in (base / folder).rglob('*'):
            relative = source.relative_to(base)
            if not any(p in relative.parts for p in ('site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip')):
                add(source, 'runtime/' + relative.as_posix())
    for source in qt.rglob('*.py'):
        if '__pycache__' not in source.parts:
            add(source, 'runtime/site-packages/PySide6/' + source.relative_to(qt).as_posix())
    for source in (packages / 'shiboken6').rglob('*'):
        if '__pycache__' not in source.parts:
            add(source, 'runtime/site-packages/shiboken6/' + source.relative_to(packages / 'shiboken6').as_posix())
    binaries = [qt / (name + '.pyd') for name in ('QtCore', 'QtGui', 'QtWidgets')]
    binaries += [qt / 'plugins/platforms/qwindows.dll', qt / 'plugins/platforms/qoffscreen.dll']
    for folder in ('imageformats', 'styles'):
        binaries.extend((qt / 'plugins' / folder).glob('*.dll'))
    available = {p.name.lower(): p for p in qt.glob('*.dll')}
    visited = set()
    while binaries:
        source = binaries.pop()
        if source in visited:
            continue
        visited.add(source)
        add(source, 'runtime/site-packages/PySide6/' + source.relative_to(qt).as_posix())
        for name in imports(source):
            if name.lower() in available:
                binaries.append(available[name.lower()])
    for name in ('app.py', 'jobs.py', 'render.py', 'portable_launch.py', 'portable_selftest.py', 'version.py', 'runtime_support.py', 'pipeline.py', 'process_guard.py', 'analysis_lite.py', 'speech.py', 'edit_plan.py', 'cuda_runtime.py', 'MODELS.md', 'PRODUCT_DIRECTION.md', 'README.md', 'PROJECT_SPEC.md', 'AUTHENTICATION.md', 'REFERENCE_ANALYSIS.md'):
        add(root / name, name)
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        add(root / 'tools' / name, 'tools/' + name)
    for source in qt.glob('*.txt'):
        add(source, 'licenses/PySide6/' + source.name)
    for distribution in packages.glob('*.dist-info'):
        if distribution.name.lower().startswith(('pyside6', 'shiboken6')):
            for source in (distribution / 'licenses').rglob('*'):
                add(source, 'licenses/' + distribution.name + '/' + source.relative_to(distribution / 'licenses').as_posix())
    for source in (qt / 'Qt' / 'licenses').rglob('*'):
        add(source, 'licenses/Qt/' + source.relative_to(qt / 'Qt' / 'licenses').as_posix())
    for source in (root / '.tmp' / 'ffmpeg').rglob('LICENSE'):
        add(source, 'licenses/FFmpeg-LICENSE.txt')
    extras = {
        f'runtime/python{sys.version_info.major}{sys.version_info.minor}._pth': '.\nLib\nDLLs\nsite-packages\n..\nimport site\n',
        'START.bat': '@echo off\r\ncd /d "%~dp0"\r\nset PYTHONDONTWRITEBYTECODE=1\r\nstart "" "%~dp0runtime\\pythonw.exe" "%~dp0portable_launch.py"\r\n',
        'TEST.bat': '@echo off\r\ncd /d "%~dp0"\r\nset PYTHONDONTWRITEBYTECODE=1\r\n"%~dp0runtime\\python.exe" -X utf8 "%~dp0portable_selftest.py"\r\npause\r\n',
        'HOW-TO-TEST.txt': 'Shorts Studio AI — тестовая версия M1\n\n1. Распакуйте всю папку Shorts-Studio-AI на диск D:. Не запускайте из ZIP.\n2. Откройте START.bat двойным щелчком. Python устанавливать не нужно.\n3. Выберите видео, начало 0 секунд и длительность 30 секунд.\n4. Нажмите «Добавить в очередь», сохраните под новым именем на D:.\n5. Дождитесь «Готово» и откройте папку результата.\n\nРаботают ручная нарезка, вертикальный MP4, очередь, отмена.\nAI-выбор сцен, автоcубтитры и Smart Censor пока отсутствуют.\nБаза, кэш и журнал запуска сохраняются рядом с приложением.\nПри ошибке смотрите data/startup.log.\n',
        'THIRD-PARTY.txt': 'Bundled CPython from python.org, PySide6/Shiboken6 from The Qt Company, FFmpeg from Gyan essentials.\nFFmpeg origin: https://www.gyan.dev/ffmpeg/builds/\nFFmpeg source: https://ffmpeg.org/download.html\nQt/PySide source and license information: https://code.qt.io/pyside/pyside-setup.git/ and https://www.qt.io/licensing/open-source-lgpl-obligations\nThis archive contains the editable Python application and dynamically loaded Qt libraries.\n',
    }
    extras['VERSION.txt'] = __version__ + '\n'
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for destination, source in sorted(files.items()):
            archive.write(source, 'Shorts-Studio-AI/' + destination)
        for name, text in extras.items():
            archive.writestr('Shorts-Studio-AI/' + name, text.encode('utf-8'))
    print(f'Portable archive: {target}, {target.stat().st_size / 2**20:.1f} MiB, {len(files)} files')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.output)

