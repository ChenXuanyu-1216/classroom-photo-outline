# Local source quickstart / 本地快速开始

This guide starts from this source folder, not a private Windows package. Use your own credentials. Python 3.10+ is required; Node.js 24 is needed for JavaScript tests. The normal app and the source reviewer have different authentication requirements.

## Install

From the repository root:

```powershell
cd lecture-syllabus-generator/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

macOS/Linux:

```bash
cd lecture-syllabus-generator/backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

No Python interpreter, environment or dependency wheel is bundled. A website build is unnecessary. Installation may need access to your package index; this document does not claim a fresh installation has passed until recorded in the release verification.

## Source reviewer: local browser without WeChat

This is an existing loopback-only entry point using the official DeepSeek API and the configured `deepseek-flash` model. It has no mock fallback. Recognition/grouping/expansion may make paid requests when you submit work. Check your own provider balance and permissions before doing so.

From `lecture-syllabus-generator/backend`, create private local data and a configuration file without overwriting an existing one:

```powershell
New-Item -ItemType Directory -Force ../../.reviewer-data | Out-Null
if (-not (Test-Path ../../.reviewer-data/.env.reviewer)) {
    Copy-Item .env.reviewer.example ../../.reviewer-data/.env.reviewer
}
# Edit ../../.reviewer-data/.env.reviewer locally and fill APP_MODEL_API_KEY.
# Do not paste the key into a command, issue, screenshot or commit.
.\.venv\Scripts\python.exe reviewer.py --data-dir ../../.reviewer-data
```

macOS/Linux, from the same backend directory:

```bash
mkdir -p ../../.reviewer-data
test -e ../../.reviewer-data/.env.reviewer || cp .env.reviewer.example ../../.reviewer-data/.env.reviewer
# Edit the private file and enter your own APP_MODEL_API_KEY before starting.
.venv/bin/python reviewer.py --data-dir ../../.reviewer-data
```

The example enables the application's local CNY 5 budget accounting. It is not a provider-side billing cap. Keep provider billing controls and review the settings yourself. An empty key fails explicitly; it does not fall back to sample recognition.

The process prints a private one-time login URL and normally opens a browser. The link works once for five minutes. Use `--no-browser` for a headless terminal and open the printed link on the same computer. Use `--port 8765` if you need a specific available port. The process listens only on `127.0.0.1`; do not expose it through a public proxy. Press Ctrl+C to stop. Restart to obtain another login link; reuse the same data directory to retain saved work.

`.reviewer-data/` is ignored. It contains `.env.reviewer`, SQLite data, uploads and exports. Protect and back it up together; never copy it into a release. An alternative private directory can be passed to `--data-dir`, with its `.env.reviewer` created there. Environment variables and the ordinary backend `.env` do not substitute for this file: the reviewer isolates its configuration.

Upload `backend/tests/fixtures/deepseek-synthetic-slide.png` (relative to `lecture-syllabus-generator`) for a reusable example. Check the result, save an edit, reopen history and export Word. Synthetic/offline success is not a general recognition-accuracy claim. The reviewer path does not exercise WeChat authentication.

## Normal app: WeChat browser login and mini-program

From the backend directory:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Edit .env locally. Fill your own APP_WECHAT_APP_ID and APP_WECHAT_APP_SECRET.
.\.venv\Scripts\python.exe run.py
```

macOS/Linux:

```bash
test -e .env || cp .env.example .env
# Edit .env locally before starting.
.venv/bin/python run.py
```

Open `http://127.0.0.1:8000/login.html`. The matching mini-program must be configured and reachable so the user can scan and confirm. Configure `APP_WECHAT_QRCODE_ENV` for the installed develop/trial/release client. Mock model mode needs no model key but does not remove authentication. Model and WeChat secrets stay on the server.

`run.py` defaults to LAN-capable `0.0.0.0` for phone development. Use `APP_SERVER_HOST=127.0.0.1` when only this computer should connect. A phone needs the computer's reachable LAN address, a trusted network and the appropriate platform permissions; its `127.0.0.1` points to the phone itself. See [client setup](../miniprogram/README.md) and [authentication](wechat-auth.md).

For live recognition, set `APP_MODEL_PROVIDER=deepseek`, `APP_CLUSTER_PROVIDER=model` and your own `APP_MODEL_API_KEY` in `.env`, then restart. The template defaults to mock content. Cloud hosting uses the separate `.env.cloud.example` and [explicit schema/deployment procedure](wechat-cloud-hosting.md); do not use reviewer authentication as a production shortcut.

## Tests and evidence

The full backend acceptance suite requires Git and a HEAD commit for version provenance. An extracted source folder can run `python -m pytest -q tests/test_smoke.py` from the backend before creating Git history. The application and source reviewer do not require a Git checkout.

The [root README](../README.en.md#tests) lists backend, website, mini-program and source-reviewer commands. On Windows, if the shared pytest temporary link causes cleanup errors, use a unique `--basetemp` directory for that invocation instead of deleting a shared link:

```powershell
$testDirectory = Join-Path $env:TEMP ('outline-tests-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest -q --basetemp=$testDirectory
```

See [release status](open-source-release.md) for what has actually been tested. Private-ZIP acceptance, real MySQL, Docker, live provider calls and device checks are separate from the default offline tests.
