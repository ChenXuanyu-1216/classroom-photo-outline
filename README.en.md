<h1 align="center">Classroom Photo Outline</h1>

<p align="center">
  <b>Turn slide photos into a study outline you can edit and check against the source.</b><br>
  Upload pages in order, review the content, save your changes and export to Word.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-6b5b4b?style=flat-square" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-6b5b4b?style=flat-square" alt="Python 3.10 or later">
  <a href="https://github.com/ChenXuanyu-1216/classroom-photo-outline/actions/workflows/checks.yml"><img src="https://github.com/ChenXuanyu-1216/classroom-photo-outline/actions/workflows/checks.yml/badge.svg?branch=main" alt="Engineering checks"></a>
</p>

<p align="center"><a href="README.md">简体中文</a> · <b>English</b></p>
<p align="center">
  <a href="#what-it-does">Introduction</a> ·
  <a href="#run-it-locally">Local setup</a> ·
  <a href="#adapt-it-to-your-course">Course setup</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="https://github.com/ChenXuanyu-1216/classroom-photo-outline/releases/tag/v0.1.0">Download v0.1.0</a>
</p>

## What it does

Upload a set of slide photos in page order. The application recognizes the pages, groups related points and builds an outline with source-page references. Check it against the images, edit and save it, reopen it from history, or export a Word document.

The source includes a FastAPI backend, a browser interface and a native WeChat mini-program. Image processing, recognition prompts, grouping, knowledge expansion and export are available in the code so you can follow and change the actual workflow.

[v0.1.0](https://github.com/ChenXuanyu-1216/classroom-photo-outline/releases/tag/v0.1.0) is published as a source-only developer preview. Formulas, code, terminology and source references still need human review. No public hosted service or Windows installer is provided.

## What you can use

| Task | Available behavior |
| --- | --- |
| Organize a set of photos | Reorder pages, recognize their content, group across pages and retain source references |
| Keep working on an outline | Edit, save, reopen from history, inspect source images and export Word |
| Separate slide content from added explanations | Review source labels and AI-generated markers |
| Record an evaluation | Save terminal snapshots, append human reviews and export JSON or Markdown evidence |
| Try it on your computer | Start the local reviewer with your own DeepSeek key, without WeChat setup |
| Use WeChat or a cloud deployment | Browser QR login, mini-program accounts and ownership checks, with explicitly configured MySQL, private COS and workers |

Model results are checked for format and required business fields. Timeouts, empty results and malformed content return corresponding errors. Mock mode returns predefined examples; recognizing your photos requires your own model configuration.

## Take a look

Download the source and open [frontend-preview.html](frontend-preview.html) in a browser for a static UI preview. It does not connect to the backend or process uploaded photos.

For an actual upload, editing and export workflow, start the application below. A reusable [synthetic slide](lecture-syllabus-generator/backend/tests/fixtures/deepseek-synthetic-slide.png) is included. Real classroom photos, private annotations and other people's credentials are excluded.

## Run it locally

Install Git and Python 3.10 or later, and prepare your own DeepSeek API key. The website needs no npm installation or frontend build. These steps start the local-only reviewer.

In Windows PowerShell, run the following.

```powershell
git clone https://github.com/ChenXuanyu-1216/classroom-photo-outline.git
cd classroom-photo-outline/lecture-syllabus-generator/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Force ../../.reviewer-data | Out-Null
if (-not (Test-Path ../../.reviewer-data/.env.reviewer)) {
    Copy-Item .env.reviewer.example ../../.reviewer-data/.env.reviewer
}
```

Open the private `.reviewer-data/.env.reviewer` file and enter your own key in `APP_MODEL_API_KEY`. Then start from the backend directory.

```powershell
.\.venv\Scripts\python.exe reviewer.py --data-dir ../../.reviewer-data
```

<details>
<summary>Equivalent commands for macOS and Linux</summary>

After cloning, enter `lecture-syllabus-generator/backend` and run the following.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p ../../.reviewer-data
test -e ../../.reviewer-data/.env.reviewer || cp .env.reviewer.example ../../.reviewer-data/.env.reviewer
# Enter your own APP_MODEL_API_KEY in the private file before starting.
.venv/bin/python reviewer.py --data-dir ../../.reviewer-data
```

</details>

Startup prints a one-time login URL and tries to open a browser. The link works once within five minutes. The service listens only on `127.0.0.1`; press Ctrl+C to stop. Reuse the same data directory when restarting to retain saved work.

Upload the synthetic example, check the result, edit and save a paragraph, then reopen it from history and export Word. Recognition, grouping and knowledge expansion can incur API charges. The example enables the application's local budget checks; those checks do not replace provider billing controls or invoices.

The local reviewer uses the official DeepSeek configuration and has no mock fallback. Normal browser and WeChat login use a different entry point, and mock model mode retains authentication. See the [complete quickstart](docs/local-quickstart.md) and [WeChat authentication guide](docs/wechat-auth.md).

## Adapt it to your course

Course adaptation starts with prompts and evaluation examples. Describe the course, the kinds of slides you use and the content that must survive recognition. For formula-heavy material, check symbols and conditions; for code-heavy material, check capitalization and indentation.

Start with the page-recognition requirements in `recognize.yaml`, then inspect how `cluster.yaml` groups the results. Use `expand.yaml` to adjust the scope of added explanations while retaining source references and AI-generated markers. These files are under `lecture-syllabus-generator/backend/prompts/`.

If you use a coding tool, fill in a request like this.

```text
Read README.en.md, docs/ai-core-flow.md and docs/classroom-photo-rubric.md first.
I want to use this project for [course name].
The slides usually contain [describe formulas, code, charts or other material].
The outline must preserve [specific terminology, symbols, sources or other requirements].
Inspect the relevant prompts and propose necessary changes, explaining each affected file.
Preserve existing JSON fields, source-page references and AI-generated markers.
Validate with shareable synthetic examples and offline tests, then list changes and unverified behavior.
Do not read private configuration, make paid model calls automatically or commit real classroom photos.
```

After editing, use a few examples you can check independently. Compare the same input before and after the change, including symbols, code and source pages. Make live provider calls only with explicit authorization. See the [evaluation templates](evaluation/classroom-photos/README.md) for human expectations and scoring.

For other configuration and interface changes, start here.

| Location | Purpose |
| --- | --- |
| `backend/.env.example`, `.env.reviewer.example`, `.env.cloud.example` | Local, reviewer and cloud examples; keep populated copies private |
| `lecture-syllabus-generator/backend/prompts/` | Recognition, grouping and knowledge-expansion prompts |
| `lecture-syllabus-generator/backend/app/` | APIs, tasks, model calls, storage and export |
| `lecture-syllabus-generator/frontend/` | Browser interface and interactions |
| `miniprogram/` | WeChat pages, transport and client configuration |
| `evaluation/classroom-photos/templates/` | Your dataset, authorization and human-review records |

The three backend examples are under `lecture-syllabus-generator/backend/`. Mini-program AppID, cloud environment and service fields are blank until you supply your own configuration. A phone needs the computer's reachable address; `127.0.0.1` on the phone refers to the phone itself.

Local development uses SQLite and files. Cloud mode also requires MySQL, private COS, separate workers and explicit database migration. Read the [cloud guide](docs/wechat-cloud-hosting.md) before deploying.

## Tests

From the backend directory, run the following.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Website and mini-program tests require Node.js 24. Run these from the repository root.

```powershell
node --test lecture-syllabus-generator/frontend/tests/*.test.cjs
node --test miniprogram/tests/*.test.cjs
.\lecture-syllabus-generator\backend\.venv\Scripts\python.exe -m pytest scripts/tests/test_reviewer_runtime.py -q
```

Use the corresponding `.venv/bin/python` paths on macOS and Linux. Full backend acceptance includes version-provenance checks requiring Git and an existing commit. In an extracted ZIP without Git history, start with `.\.venv\Scripts\python.exe -m pytest -q tests/test_smoke.py` from the backend on Windows, or `.venv/bin/python -m pytest -q tests/test_smoke.py` on macOS/Linux.

The [v0.1.0 engineering run](https://github.com/ChenXuanyu-1216/classroom-photo-outline/actions/runs/43195153409) passed offline regression, MySQL 8.0 and 8.4, and container build/startup checks. Offline tests use temporary data and mocked upstreams. They do not establish classroom recognition accuracy, WeChat device acceptance or production readiness.

## Documentation

| Document | Contents |
| --- | --- |
| [Documentation index](docs/README.md) | Setup, models, storage, evaluation and verification records |
| [Local quickstart](docs/local-quickstart.md) | Your own key, local reviewer and normal WeChat entry points |
| [Mini-program guide](miniprogram/README.md) | Import, phone networking, cloud configuration and platform requirements |
| [AI processing flow](docs/ai-core-flow.md) | Image handling, context and structured-result validation |
| [Evaluation workspace](docs/exp05-web-evaluation.md) | Evidence snapshots, human reviews and export |
| [Evaluation templates](evaluation/classroom-photos/README.md) | Your dataset, usage permissions and scoring methods |
| [Release status](RELEASE_STATUS.en.md) | Published scope and remaining validation |

Detailed technical guides are currently a mixture of Chinese and English; this README and the project entry policies have separate language versions.

## Questions and contributions

[Open an issue](https://github.com/ChenXuanyu-1216/classroom-photo-outline/issues/new) for a reproducible problem. Include the entry point, steps, expected result and redacted error information. Read the [contribution guide](CONTRIBUTING.en.md) before changing code.

Operators supply their own mini-program release setup, model budget and deployment environment. Do not attach keys, sessions or classroom photos without permission when reporting an issue.

## Security and privacy

Keep model keys and WeChat AppSecret on the backend. Local `.env` files, `.reviewer-data`, databases, uploaded originals and private packages belong in controlled local or server storage.

Do not expose `reviewer.py` or its one-time login URL through a public proxy. Follow the [security policy](SECURITY.en.md) for private reporting, and keep vulnerability details and sensitive material out of public issues.

## A final note

If you develop better prompts for a course, contributions that include a shareable synthetic example are welcome. Mark the incorrect formula symbol or indentation so others can check the same input.

A reproducible bug or a note that explains a limitation can be a contribution. Start with a page you understand well.

## MIT license

Source and documentation use the [MIT License](LICENSE). Use, modification, redistribution and commercial use are permitted under its terms, including preservation of the copyright and permission notice. A [Chinese reference translation](LICENSE.zh-CN.md) is available; the English `LICENSE` remains the governing project text.

Dependencies retain their own terms in [third-party notices](THIRD_PARTY_NOTICES.en.md). Publishing the code grants no additional rights to real classroom photos, user uploads, external services or platform trademarks. [Synthetic-image provenance](docs/ui-assets.md) is documented separately.
