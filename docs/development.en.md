# Development and configuration reference

Current startup, tests and release state: [quickstart](local-quickstart.md), [README](../README.en.md), [release preparation](open-source-release.md). Historical examples below are not fresh acceptance evidence. Local storage uses SQLite/files; explicit cloud mode uses MySQL/private COS.


[Project overview](../README.en.md) · [Documentation index](README.md) · [简体中文](development.md)

Detailed installation, preview, model configuration, upload contracts, tests, and database notes. Run commands from their stated working directories. Live model integration remains a prototype; real recognition quality and performance have not been accepted.

## Technology stack

- Backend: Python, FastAPI, Uvicorn, and Pydantic.
- Storage: SQLAlchemy and SQLite; uploaded files are stored locally.
- Frontend: plain HTML, CSS, and JavaScript served by FastAPI, with no Node.js build step.
- Model calls: HTTPX with a multimodal API compatible with Chat Completions.
- Document export and testing: python-docx and pytest.

## Quick start

Use the [current source quickstart](local-quickstart.md) for installation, loopback reviewer startup with your own key, or normal WeChat authentication. Mock model mode does not bypass login.

## Model and upload configuration

Edit `lecture-syllabus-generator/backend/.env`:

```dotenv
APP_MODEL_PROVIDER=mock
APP_CLUSTER_PROVIDER=rule
APP_MODEL_API_KEY=
APP_MODEL_BASE_URL=https://api.deepseek.com
APP_MODEL_NAME=deepseek-flash
APP_MODEL_TIMEOUT_SECONDS=60
APP_MODEL_TRUST_ENV=false
APP_MODEL_MAX_RETRY=1
APP_MODEL_MAX_TOKENS=4096
APP_ALLOWED_EXT=.jpg,.jpeg,.png,.pdf
APP_MAX_FILE_MB=10
APP_MAX_FILES_PER_BATCH=20
APP_RECOMMENDED_BATCH=10
APP_LOW_CONFIDENCE_THRESHOLD=0.60
```

These are the project template defaults. When integrating a service, use the actual provider's endpoint and the name of a model that supports image input.

Model requests use a direct connection by default instead of implicitly inheriting Windows system proxies or `HTTP_PROXY` / `HTTPS_PROXY`. This avoids depending on a desktop proxy being running when the app is reopened. Networks that require a proxy can opt in with `APP_MODEL_TRUST_ENV=true` and restart. Recognition, grouping, and expansion use the same setting. Transport logs contain only the stage, route, exception types, and OS error codes; credentials and slide content are excluded.

- `mock`: demonstrate the local workflow using predefined results.
- `deepseek`: call the official `deepseek-flash` image endpoint with thinking disabled and validated JSON. Missing keys or invalid configuration fail explicitly.
- `multimodal`: send compatible image/JSON requests to `/chat/completions` under `APP_MODEL_BASE_URL`; a missing key fails explicitly.
- `fallback`: return fallback content indicating that recognition is unavailable.

Live web recognition and topic grouping are available; follow the [live setup guide](deepseek-live-application.md#english). The repository template remains in `mock`. Set `APP_MODEL_PROVIDER=deepseek` and `APP_CLUSTER_PROVIDER=model` to enable live photos and grouping, or use `multimodal` for another compatible provider. Configuration is loaded from fixed `backend/.env`, which is ignored by Git. Live recognition loads versioned `recognize.yaml` and validates structured results. Confidence is a model-reported readability estimate, not calibrated accuracy. Live image recognition supports JPG/JPEG/PNG; convert PDF pages to images first.

### Topic clustering with original text and sources preserved

`APP_CLUSTER_PROVIDER` controls clustering independently. Restart the backend after changing it:

- `rule` (default): group by the first source page without calling a clustering model.
- `model`: use the `APP_MODEL_*` connection settings above to group knowledge points across pages by topic. The provider must support Chat Completions text input and `response_format: {"type": "json_object"}`.

To enable live image recognition and model-based topic clustering together:

```dotenv
APP_MODEL_PROVIDER=deepseek
APP_CLUSTER_PROVIDER=model
# Also configure APP_MODEL_API_KEY, APP_MODEL_BASE_URL, and APP_MODEL_NAME
```

You can also combine `APP_MODEL_PROVIDER=mock` with `APP_CLUSTER_PROVIDER=model` to test clustering independently. Its input is still predefined course content, so this combination cannot evaluate real image recognition quality. Model clustering always requires a valid key and makes a real call even when recognition is set to `mock`.

After deduplication, the application assigns temporary IDs such as `kp_1` and `kp_2` to the current clustering input. The model may return only this structure:

```json
{"modules": [{"title": "索引与查询优化", "point_ids": ["kp_1", "kp_3"]}, {"title": "事务", "point_ids": ["kp_2"]}]}
```

Each input ID must appear exactly once. Unknown, duplicate, or missing IDs, empty modules, invalid titles, extra fields, invalid JSON, and truncated results are rejected. Titles must be single-line text with no more than 120 characters. The application restores the original knowledge point objects by ID; the model does not rewrite their text, knowledge point types, source types, or source page numbers. Module source pages are computed as the union of their points' source pages. Identical text with different source types remains separate. Temporary IDs are valid only for the current clustering request; they are neither stored in the database nor used as references across versions.

A missing key or a missing or damaged prompt returns `CLUSTER_CONFIG_ERROR`. Invalid clustering output or service failure returns `CLUSTERING_FAILED`; a timeout returns `MODEL_TIMEOUT`. Retries are bounded by `APP_MODEL_MAX_RETRY` and apply only to timeouts, connection failures, and HTTP 408, 429, or 5xx responses. Format and ID validation failures are not automatically retried. On failure, the application preserves existing outlines and knowledge points, marks the task as failed, and does not silently switch to rule-based grouping. To use rules instead, set the provider to `rule` and restart. The clustering prompt is in `backend/prompts/cluster.yaml`; restart after editing it to refresh the prompt cache.

### Upload and task creation contract

`POST /api/tasks` retains the successful response fields `task_id`, `page_count`, and `rejected`. Partial acceptance is supported: unsupported extensions, empty files, and oversized files enter the rejection list. Valid files are saved in the specified order and receive consecutive logical page numbers starting at 1. `page_count` matches the number of saved pages. If all files are rejected, the endpoint returns 400 without creating an empty task.

When `order` is empty, upload order is used. Otherwise, it must contain every original file index in the batch, starting at 1, exactly once. Missing, duplicate, out-of-range, non-integer, or excessively long values return `INVALID_FILE_ORDER` without writing a task or files. Rejected files are filtered after ordering, so accepted pages have no gaps in their page numbers.

The service reads files in chunks of at most 64 KiB and validates their actual byte counts. Files are first written to an exclusive staging directory within storage, flushed, and then moved together to a unique directory. Task and Page records are saved in one database commit. Ordinary read, write, publication, and database failures roll back the request and clean up its directories without touching existing task files. New task directories use `task-<task ID>-<random value>`; older tasks continue to use their stored `file_path` values, without file migration.

If a database commit reports an error, an independent connection checks the outcome. A confirmed commit preserves the files and returns success. If the outcome cannot be confirmed, files are preserved and the endpoint returns `UPLOAD_STATE_UNCERTAIN` with `retryable=false`; check history and service logs before retrying to avoid duplicate uploads. Cleanup failure returns `UPLOAD_CLEANUP_ERROR` and requires a maintainer to inspect residual directories. Ordinary persistence failure returns the retryable `UPLOAD_PERSISTENCE_ERROR`.

These guarantees cover ordinary request exceptions. Crash recovery and transport limits before the request reaches the service remain outside scope. Upload acceptance checks extensions and bytes; before live recognition, actual image decoding, orientation, size and metadata processing are now applied. See the [AI core flow](ai-core-flow.md#english). PDF decoding is not implemented. Earlier upload guarantees are recorded in the upload verification (historical material not distributed).

## Testing

Tests cover health metadata, upload validation, source tag parsing, source preservation across staged recognition and generation, cross-page deduplication, saved edits after reopening, Word content comparison, timeouts, disconnections, partial failures, retries, and error recovery.

**Before importing the application, pytest creates an isolated temporary database and upload directory and forces mock recognition with default rule-based clustering. Tests do not clear the everyday database at `backend/data/app.db`.** Each test resets the temporary database, and the test directory is removed at the end of the run. Recognition and clustering tests use simulated HTTP transports without making real API requests.

Clustering tests cover cross-page grouping, complete and unique ID assignment, rejection of model-rewritten fields, source preservation, saving and reopening, Word content comparison, bounded retries, and preservation of old data when regeneration fails.

Install dependencies and run tests from the `backend` directory:

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

```bash
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Create the virtual environment using the quick start instructions before the first run. Tests configure their isolated environment automatically; `.env` does not need to be changed. These tests do not establish real model recognition quality or replace manual browser acceptance checks.

Frontend interaction tests can run from the repository root using Node.js, without installing npm dependencies:

```powershell
node --test lecture-syllabus-generator/frontend/tests/*.test.cjs
```

These cases cover history rendering, empty states, network failures and retries, time conversion, background speed controls, progress recovery, preservation of editing drafts, and read-only recovery when a result is unconfirmed. They do not call real APIs. Export is disabled while editing unsaved content. If regeneration's response or the subsequent result fetch fails, the current result must be reloaded before editing and export are enabled again.

After installing the test dependencies and Node.js, you can run all engineering checks from the repository root with:

```powershell
pwsh -NoProfile -File lecture-syllabus-generator/scripts/verify_exp03.ps1
```

### Existing database upgrades and source preservation

At startup, older SQLite databases receive a nullable `recognized_structured` column in the `page` table. Existing records are preserved, and initialization can run repeatedly. New recognition results store titles, knowledge points, and source types together, and subsequent generation reuses them. The actual source types of older records that never stored this information cannot be recovered automatically; regeneration marks them as “来源待核对” (source needs verification). Previously saved outlines are not rewritten in bulk.

Points with identical text but different source types remain separate. Source page numbers are merged only when both the normalized text and source type match. If some pages fail, successful pages can still be processed. If all pages fail, the application returns an explicit error. Failed regeneration preserves the previously saved outline and knowledge points.

## V2 local background processing

Browser model operations use a durable SQLite queue. Restart the backend to load the changes; startup commands remain unchanged. `APP_TASK_QUEUE_ENABLED=true` and `APP_TASK_QUEUE_CAPACITY=20` enable one consumer per database with up to 20 waiting jobs, without external services. Disabled queues reject asynchronous requests with 503. Existing scripts without `Prefer: respond-async` keep synchronous behavior.

Refreshing reads existing job state. Accepted waiting jobs survive restart; interrupted running jobs require explicit retry and are not automatically replayed. Existing drafts remain readable and exportable while model work excludes saves, deletion and other generation. Transport retries use bounded backoff; authentication, balance, bad images and invalid structured output do not trigger automatic retries. See the V2 verification record (historical material not distributed) for scope, evidence and limits.

Response decoding now lives in the pure `model_response_parser.py` module, while `model_output.py` contains business schemas. Empty HTTP bodies return `MODEL_EMPTY_RESPONSE`; normally completed responses with empty content return `MODEL_EMPTY_CONTENT`. Other malformed outputs retain existing stage errors. Business error mapping and state persistence remain separate. This does not add a complete per-attempt audit trail; see the classification record (historical material not distributed).

## Current limitations

- Deduplication still uses text rules. Model clustering only groups topics; it does not perform semantic deduplication or rewrite points. The default remains source-page grouping. Model clustering quality and performance on real courses require measurement.
- Live recognition validates structured JSON and retains code and formula content. Readability confidence is model-reported, not calibrated accuracy; inferred answers and grouping still require human review.
- Mock upload settings include PDF, but live image recognition rejects PDF and multi-page splitting is not implemented. Convert pages to JPG/JPEG/PNG and update allowed formats for live use.
- The launcher binds to `0.0.0.0:8000` by default; the computer can use localhost and phones use the current LAN IP. Set `APP_SERVER_HOST=127.0.0.1` behind a local reverse proxy. Business routes now require WeChat sessions and task ownership. The website uses QR login; see [authentication and legacy ownership](wechat-auth.md). Public HTTPS deployment, quotas and operational acceptance remain pending.
