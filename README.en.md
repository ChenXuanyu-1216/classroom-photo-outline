# Classroom PPT Photo Outline Generator

[简体中文](README.md) · [Documentation](docs/README.md) · [MIT license](LICENSE)

Turn slide photos into editable study outlines with source-page references and Word export. This source-only developer preview includes a FastAPI backend, a browser interface and a native WeChat mini-program. It is not a hosted service or a Windows executable.

## Features

- Ordered uploads, recognition, cross-page grouping, outline editing, saved history, source inspection and Word export.
- Owner-scoped evaluation snapshots, append-only human reviews and JSON/Markdown evidence export.
- A loopback-only source-reviewer entry point using your own DeepSeek key, without WeChat setup.
- Server-verified WeChat accounts for the normal browser/mini-program entry points.
- SQLite/local files for development; an explicit MySQL/private COS cloud profile with separate workers and migration tools.

## Start locally

Requires Python 3.10+ and pip. Offline JavaScript tests use Node.js 24. The website has no npm installation or frontend build step.

From the repository root, install the backend:

```powershell
cd lecture-syllabus-generator/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

On macOS/Linux use `python3 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt`.

Then choose a startup path in the [local quickstart](docs/local-quickstart.md):

| Path | Requirements | Scope |
| --- | --- | --- |
| Source reviewer | Your own DeepSeek key in a private `.env.reviewer` | Local browser workflow, loopback only; real recognition may incur charges |
| Normal app | Your own WeChat AppID/AppSecret and configured client | Browser QR login and mini-program; mock model mode still requires authentication |
| Visual preview | Open [frontend-preview.html](frontend-preview.html) | Static sample UI; no backend or recognition |

For a reusable input use `lecture-syllabus-generator/backend/tests/fixtures/deepseek-synthetic-slide.png`. It is synthetic, not evidence of classroom recognition accuracy. No real classroom-photo dataset is distributed.

## Tests

The full backend suite includes provenance tests that require Git and an existing HEAD commit. In a copied folder before Git initialization, start with `pytest -q tests/test_smoke.py`; source-reviewer startup does not require Git.

Run from the backend after installing dependencies:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

From the repository root:

```powershell
node --test lecture-syllabus-generator/frontend/tests/*.test.cjs
node --test miniprogram/tests/*.test.cjs
.\lecture-syllabus-generator\backend\.venv\Scripts\python.exe -m pytest scripts/tests/test_reviewer_runtime.py -q
```

On macOS/Linux replace the Python executable path with `lecture-syllabus-generator/backend/.venv/bin/python`. Tests use temporary databases and mocked external transports. Dedicated real-MySQL checks require their own test server. See [verification and remaining gates](docs/open-source-release.md); historical test counts are not a claim about this modified source snapshot.

## Configuration and limitations

- Copy the empty configuration examples and supply your own values. The mini-program AppID, cloud environment and service fields are intentionally blank. See [client setup](miniprogram/README.md).
- Keep model keys and WeChat AppSecret on the backend. Never commit local configuration, sessions, photos, runtime databases or private packages.
- Mock mode returns predefined content; live recognition requires a configured provider. Review formulas, code, terminology and page attribution before studying from or sharing an outline.
- The local reviewer does not validate WeChat login and must remain on loopback. Its one-time login URL is private.
- Cloud hosting, MySQL/COS migration, production operations and WeChat device acceptance require separate deployment verification. See [cloud setup](docs/wechat-cloud-hosting.md).
- The existing private reviewer builder is maintainer-only and can create credential-bearing output. It is not a public packaging recipe.

## License and contributions

Project source and documentation are licensed under the [MIT License](LICENSE). Dependencies retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md) and [synthetic image provenance](docs/ui-assets.md). This license does not grant rights to excluded classroom photos, uploaded materials, external services or platform trademarks.

See [contribution guidance](CONTRIBUTING.md) and [security reporting](SECURITY.md). This folder is prepared for a fresh source repository; publication and release tagging remain separate actions.
