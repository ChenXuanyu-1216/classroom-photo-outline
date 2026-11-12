"""Build a private Windows x64 distribution using the original DeepSeek API.

Run with backend/.venv/Scripts/python.exe. Credential inclusion is opt-in.
"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import struct
import sys
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'lecture-syllabus-generator' / 'backend'
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '*.log', '.env', '.env.*')
sys.path.insert(0, str(BACKEND))
from reviewer import MODEL_ENV_NAMES


def runtime_dependencies():
    pending = [Requirement(line.strip()) for line in (BACKEND / 'requirements.txt').read_text().splitlines()
               if line.strip() and not line.lstrip().startswith('#')]
    result, seen = {}, set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        distribution = metadata.distribution(name)
        if distribution.version not in requirement.specifier:
            raise RuntimeError(f'Installed {name} does not satisfy {requirement.specifier}.')
        result[name] = distribution
        for raw in distribution.requires or []:
            child = Requirement(raw)
            if child.marker is None or any(child.marker.evaluate({'extra': extra})
                                           for extra in {'', *requirement.extras}):
                pending.append(child)
    return result


def build(destination: Path, *, include_model_credentials=False):
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise RuntimeError('Build this distribution with Windows x64 Python.')
    destination = destination.resolve()
    archive = destination.with_suffix('.zip')
    if destination.exists() or archive.exists():
        raise FileExistsError('Choose a new output name; existing packages are never overwritten.')
    if not include_model_credentials:
        raise ValueError('This private DeepSeek package needs explicit --include-model-credentials authorization; the ZIP will contain the configured API key.')
    from dotenv import dotenv_values
    original = dotenv_values(BACKEND / '.env', interpolate=False)
    model_settings = {key: value for key, value in original.items()
                      if key in MODEL_ENV_NAMES and value is not None}
    # The user requires real recognition even if the source .env was in mock mode.
    model_settings['APP_MODEL_PROVIDER'] = 'deepseek'
    model_settings.setdefault('APP_MODEL_BASE_URL', 'https://api.deepseek.com')
    model_settings.setdefault('APP_MODEL_NAME', 'deepseek-flash')
    model_settings.setdefault('APP_CLUSTER_PROVIDER', 'model')
    model_settings.setdefault('APP_KNOWLEDGE_EXPANSION_ENABLED', 'true')
    if not (model_settings.get('APP_MODEL_API_KEY') or '').strip():
        raise ValueError('The original backend configuration has no DeepSeek API key.')
    if (model_settings['APP_MODEL_BASE_URL'].strip().rstrip('/') not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}
            or model_settings['APP_MODEL_NAME'] != 'deepseek-flash'):
        raise ValueError('The private bundle requires the original official DeepSeek endpoint and deepseek-flash model.')
    dependencies = runtime_dependencies()
    base = Path(sys.base_prefix)
    runtime = destination / 'runtime'
    runtime.mkdir(parents=True)
    for name in ('python.exe', 'pythonw.exe', 'python3.dll',
                 f'python{sys.version_info.major}{sys.version_info.minor}.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        source = base / name
        if source.exists():
            shutil.copy2(source, runtime / name)
        elif name not in {'pythonw.exe', 'vcruntime140_1.dll'}:
            raise FileNotFoundError(source)
    shutil.copytree(base / 'DLLs', runtime / 'DLLs', ignore=IGNORE)
    shutil.copytree(base / 'Lib', runtime / 'Lib', ignore=shutil.ignore_patterns(
        'site-packages', '__pycache__', '*.pyc', '*.pyo', 'test', 'tests',
        'idlelib', 'tkinter', 'turtledemo', 'ensurepip'))
    for distribution in dependencies.values():
        site_root = Path(distribution.locate_file('')).resolve()
        for relative in distribution.files or []:
            source = Path(distribution.locate_file(relative)).resolve()
            if not source.is_relative_to(site_root) or not source.is_file() or '__pycache__' in source.parts:
                continue  # Skip host console-script wrappers and bytecode.
            target = runtime / 'packages' / source.relative_to(site_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Isolated search paths: no registry Python, PYTHONPATH or host site-packages.
    pth = runtime / f'python{sys.version_info.major}{sys.version_info.minor}._pth'
    pth.write_text('Lib\nDLLs\npackages\n../app/backend\n', encoding='utf-8')

    backend_target = destination / 'app' / 'backend'
    backend_target.mkdir(parents=True)
    for name in ('app', 'prompts', 'db'):
        shutil.copytree(BACKEND / name, backend_target / name, ignore=IGNORE)
    for name in ('reviewer.py', 'run.py', 'requirements.txt', 'requirements-dev.txt'):
        shutil.copy2(BACKEND / name, backend_target / name)
    shutil.copytree(BACKEND.parent / 'frontend', destination / 'app' / 'frontend', ignore=IGNORE)
    (destination / 'samples').mkdir()
    shutil.copy2(BACKEND / 'tests' / 'fixtures' / 'deepseek-synthetic-slide.png',
                 destination / 'samples' / 'synthetic-slide.png')

    cmd = r'''@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime\python.exe" (
  echo Missing runtime. Extract the entire ZIP before starting.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" -u "%~dp0app\backend\reviewer.py" --data-dir "%~dp0reviewer-data" %*
set "REVIEW_EXIT_CODE=%ERRORLEVEL%"
if not "%REVIEW_EXIT_CODE%"=="0" (
  echo.
  echo Startup stopped with code %REVIEW_EXIT_CODE%. Please include the error above in your report.
  pause
)
exit /b %REVIEW_EXIT_CODE%
'''
    (destination / 'Start Review.cmd').write_text(cmd, encoding='ascii', newline='\r\n')
    ps1 = '''$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    & (Join-Path $PSScriptRoot 'runtime/python.exe') -u (Join-Path $PSScriptRoot 'app/backend/reviewer.py') --data-dir (Join-Path $PSScriptRoot 'reviewer-data') @args
    if ($LASTEXITCODE -ne 0) { throw "Reviewer exited with code $LASTEXITCODE" }
} catch {
    Write-Host $_ -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}
'''
    (destination / 'Start Review.ps1').write_text(ps1, encoding='utf-8-sig')
    (destination / 'README.txt').write_text(
        'CLASSROOM PPT OUTLINE - WINDOWS X64 REVIEW PACKAGE\n\n'
        '1. Extract the entire ZIP to a writable folder.\n'
        '2. Double-click Start Review.cmd. The browser opens automatically.\n'
        '3. Keep the console open. Press Ctrl+C there to stop.\n\n'
        'No Python, Node, pip, PowerShell policy change or administrator setup is needed.\n'
        'Windows 10/11 x64 with a browser is the supported target.\n'
        'If a browser does not open, copy the Reviewer ready URL from the console.\n'
        'The URL works once for 5 minutes. Restart to obtain a new login link.\n'
        'Recognition calls the original official DeepSeek API using the included configured key.\n'
        'Internet access and a valid funded DeepSeek account are required. No mock fallback is used.\n'
        'Upload samples/synthetic-slide.png or your own JPG/PNG to test the workflow.\n'
        'Edit/save, reopen history, inspect the source, export Word, then restart to check persistence.\n'
        'Local reviewer-data stores this machine\'s own records. Existing photos and databases are not bundled.\n'
        'The review account is local; this package does not test WeChat login.\n\n'
        'No credential entry or configuration step is needed for this authorized private build.\n'
        'PRIVATE: This ZIP contains the owner\'s API key. Share only with intended reviewers; do not publish it.\n'
        'Requests use the owner\'s DeepSeek balance.\n'
        'Use Start Review.cmd if PowerShell policy blocks Start Review.ps1.\n'
        'If Windows blocks a downloaded package, follow your organization\'s software approval process.\n'
        'This package is unsigned. It does not install services or change Windows settings.\n', encoding='utf-8')
    (destination / '.env.example').write_text(
        '# Reference only; the private package already includes reviewer-data/.env.reviewer.\n'
        'APP_MODEL_PROVIDER=deepseek\nAPP_CLUSTER_PROVIDER=model\n'
        'APP_MODEL_BASE_URL=https://api.deepseek.com\nAPP_MODEL_NAME=deepseek-flash\n'
        'APP_MODEL_API_KEY=\nAPP_KNOWLEDGE_EXPANSION_ENABLED=true\n'
        'APP_MODEL_BUDGET_ENFORCED=false\nAPP_MODEL_BUDGET_CNY=5\n', encoding='utf-8')
    private_config = destination / 'reviewer-data' / '.env.reviewer'
    private_config.parent.mkdir()
    private_config.write_text(''.join(key + '=' + json.dumps(value, ensure_ascii=False) + '\n'
                                     for key, value in sorted(model_settings.items())), encoding='utf-8')
    manifest = {
        'target': 'Windows x64', 'python': platform.python_version(),
        'mode': 'original official DeepSeek API; no mock fallback',
        'contains_model_credentials': True,
        'dependencies': {name: dist.version for name, dist in sorted(dependencies.items())},
        'files': {},
    }
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            manifest['files'][path.relative_to(destination).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                # Keep extraction paths short on Windows without changing OS policy.
                bundle.write(path, 'ClassroomReview/' + path.relative_to(destination).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'Package: {archive}\nSize: {archive.stat().st_size / 1024 / 1024:.1f} MiB\nSHA256: {digest}')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'ClassroomOutline-Review-DeepSeek-PRIVATE')
    parser.add_argument('--include-model-credentials', action='store_true', help='Explicitly authorize placing the original API key in this PRIVATE review ZIP.')
    args = parser.parse_args()
    build(args.output, include_model_credentials=args.include_model_credentials)
