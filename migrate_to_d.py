"""User-launched migration. Copies and verifies before removing only known chat files."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid

WORKSPACE = None
SOURCE = None
ARTIFACTS = None
DESTINATION = Path(r'D:\ShortsStudioAI')


def manifest(folder):
    result = {}
    paths = sorted(folder.rglob('*'))
    for index, path in enumerate(paths):
        # is_junction exists only on WindowsPath; a POSIX symlink check is enough elsewhere.
        if path.is_symlink() or (os.name == 'nt' and path.is_junction()):
            raise RuntimeError('Refusing a link/junction: ' + str(path))
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        result[path.relative_to(folder).as_posix()] = (path.stat().st_size, digest.hexdigest())
        if index and index % 1000 == 0:
            print('  verified', index, 'entries', flush=True)
    return result


def copy_verified(source, destination):
    if destination.exists():
        raise RuntimeError('Destination already exists; nothing will be overwritten: ' + str(destination))
    before = manifest(source)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    if manifest(destination) != before:
        raise RuntimeError('Copy verification failed: ' + str(source))
    return before


def guarded_remove(source, destination, expected):
    if WORKSPACE is None or SOURCE is None or ARTIFACTS is None:
        raise RuntimeError('Cleanup has no validated configuration.')
    allowed = (SOURCE.resolve(), ARTIFACTS.resolve())
    resolved = source.resolve()
    target = destination.resolve()
    if resolved not in allowed or not resolved.is_relative_to(WORKSPACE.resolve()):
        raise RuntimeError('Unsafe source path; cleanup refused.')
    if target.drive.lower() != 'd:' or not target.is_relative_to(DESTINATION.resolve()):
        raise RuntimeError('Unsafe destination path; cleanup refused.')
    if manifest(source) != expected:
        raise RuntimeError('Source changed during migration. C: was preserved: ' + str(source))
    # Compare every originally copied file again, allowing generated test data on D:.
    for relative, (size, digest) in expected.items():
        path = target / relative
        if not path.is_file() or path.stat().st_size != size:
            raise RuntimeError('Destination changed; C: was preserved.')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest:
            raise RuntimeError('Destination changed; C: was preserved.')
    shutil.rmtree(resolved)


def main():
    global WORKSPACE, SOURCE, ARTIFACTS
    portable = Path(__file__).resolve().parent
    if portable.drive.lower() != 'd:':
        raise RuntimeError('Сначала распакуйте приложение на D: и запускайте MOVE-PROJECT-TO-D.bat оттуда.')
    if not (portable / 'runtime' / 'python.exe').is_file():
        raise RuntimeError('Сначала распакуйте ВЕСЬ архив, затем запустите файл переноса.')
    configuration = json.loads((portable / 'MIGRATION_CONFIG.json').read_text(encoding='utf-8'))
    workspace = Path(configuration['workspace']).resolve()
    codex = (Path.home() / 'Documents' / 'Codex').resolve()
    if workspace.drive.lower() != 'c:' or not workspace.is_relative_to(codex) or len(workspace.relative_to(codex).parts) < 2:
        raise RuntimeError('Путь исходного проекта не прошёл проверку безопасности.')
    WORKSPACE = workspace
    SOURCE = WORKSPACE / 'work' / 'shorts-studio-ai'
    ARTIFACTS = WORKSPACE / 'outputs'
    if not SOURCE.is_dir():
        raise RuntimeError('Исходный проект не найден. Возможно, он уже перенесён: ' + str(SOURCE))
    if DESTINATION.exists() and (DESTINATION.is_junction() or DESTINATION.is_symlink()):
        raise RuntimeError('Папка назначения является ссылкой; перенос остановлен.')
    if DESTINATION.resolve().drive.lower() != 'd:':
        raise RuntimeError('Папка назначения должна находиться на D:.')
    final_project, final_downloads = DESTINATION / 'project', DESTINATION / 'downloads'
    if final_project.exists() or final_downloads.exists():
        raise RuntimeError('Папка project или downloads уже существует в D:\\ShortsStudioAI. Перезапись запрещена.')
    required = sum(p.stat().st_size for root in (SOURCE, ARTIFACTS) for p in root.rglob('*') if p.is_file())
    if shutil.disk_usage('D:/').free < required + 1024 ** 3:
        raise RuntimeError('Недостаточно свободного места на D:.')
    DESTINATION.mkdir(exist_ok=True)
    stage = DESTINATION / ('migration-' + uuid.uuid4().hex)
    stage.mkdir()
    project, downloads = stage / 'project', stage / 'downloads'
    print('1/5 Copying project and checking SHA-256. This may take several minutes.', flush=True)
    project_snapshot = copy_verified(SOURCE, project)
    print('2/5 Copying downloads and checking SHA-256.', flush=True)
    downloads_snapshot = copy_verified(ARTIFACTS, downloads)
    environment = os.environ.copy()
    environment.update(TEMP=str(stage), TMP=str(stage), PYTHONDONTWRITEBYTECODE='1',
        PYTHONPYCACHEPREFIX=str(stage / 'pycache'), HF_HUB_OFFLINE='1',
        HF_HOME=str(stage / 'huggingface'), NUMBA_CACHE_DIR=str(stage / 'numba'),
        CUDA_CACHE_PATH=str(stage / 'cuda-cache'),
        SHORTS_STUDIO_DATA_DIR=str(project / 'data'))
    python = project / '.venv' / 'Scripts' / 'python.exe'
    print('3/5 Checking copied Python environment and GPU.', flush=True)
    subprocess.run([str(python), '-c', 'import sys; from pathlib import Path; assert Path(sys.prefix).resolve() == Path.cwd().joinpath(".venv").resolve(); import PySide6'], cwd=project, env=environment, check=True, timeout=60)
    subprocess.run([str(python), 'verify_gpu.py'], cwd=project, env=environment, check=True, timeout=120)
    print('4/5 Checking standalone app and real video export.', flush=True)
    subprocess.run([str(portable / 'runtime/python.exe'), str(portable / 'portable_selftest.py')],
        cwd=portable, env=environment, check=True, timeout=120)
    project.rename(final_project)
    downloads.rename(final_downloads)
    os.chdir(DESTINATION)
    # Retain the launcher package on D: before removing the original verified C: copies.
    print('5/5 Tests passed. Removing the verified original project copies from C:.', flush=True)
    guarded_remove(SOURCE, final_project, project_snapshot)
    guarded_remove(ARTIFACTS, final_downloads, downloads_snapshot)
    report = dict(project=str(final_project), downloads=str(final_downloads), application=str(portable),
        removed_C_project=True, removed_C_outputs=True, bytes_transferred=required,
        note='System Python and user programs were preserved. All app data/cache is local to the D: application folder.')
    (DESTINATION / 'MIGRATION_REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\nDONE. Project:', final_project, '\nApplication:', portable, flush=True)
    os.startfile(str(portable / 'START.bat'))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import traceback
        print('\nMIGRATION STOPPED:', error, '\nOriginal files are not removed before successful verification.', flush=True)
        print(traceback.format_exc(), flush=True)
        input('Press Enter to close...')
        sys.exit(1)
    input('Migration completed. Press Enter to close...')
