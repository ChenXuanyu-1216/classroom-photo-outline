# Reconciled source verification — 2026-11-11

Historical record for the 2026-11-11 draft. Current licensing, configuration and release gates are documented in [source preparation](../open-source-release.md); statements below describe the earlier scope.


[Certain] Base: remote `main` at `4e599ba1830711ae4236169429a62db3cce2f89a`. Candidate: managed working branch `codex/open-source-reconcile`, with uncommitted reviewed changes. Source selection is a private draft; it is not a published or licensed release.

| Check | Result |
| --- | --- |
| Full final backend regression | 775 passed, 11 skipped, 0 failures; 274.74 seconds |
| Website tests | 103 passed, 0 failures |
| Mini-program tests | 70 passed, 0 failures |
| Source reviewer runtime | 2 passed, 0 failures |
| Private Windows ZIP acceptance | 1 skipped; no private package included or executed |
| Focused integration before review | 50 passed; overlapping coverage, not additional to the full suite |
| Schema-integrity review correction | Seven malformed-schema cases failed before the fix; the correction and intact partial-migration recovery passed in a 28-test focused run |
| UI assets | Both PNGs match the synthetic fixture; HTTP bytes/MIME and old-image URL removal verified |
| Source preservation | All 376 original inventory hashes remain unchanged |
| Static checks | Python application modules parse, no duplicate top-level definitions or SQL table declarations, and git diff whitespace checks pass |

[Certain] The backend's 11 skipped cases require a separately configured real-MySQL test server. No Docker CLI/server or WeChat device acceptance was used. The full run emitted eight existing framework/JUnit warnings. No live model calls, private package builds, production database migrations, remote pushes, merges, or visibility changes were made.

## Reproduction

Use a new test-only temporary directory for each pytest invocation. On this host, the shared pytest-current link is inaccessible and must not be removed or reused. From the backend with runtime/dev dependencies installed:

```powershell
$testDirectory = Join-Path $env:TEMP ('kezhao-check-' + [guid]::NewGuid().ToString('N'))
python -m pytest -q --basetemp=$testDirectory
```

From the repository root:

```powershell
node --test lecture-syllabus-generator/frontend/tests/*.test.cjs
node --test miniprogram/tests/*.test.cjs
$testDirectory = Join-Path $env:TEMP ('kezhao-reviewer-' + [guid]::NewGuid().ToString('N'))
python -m pytest scripts/tests/test_reviewer_runtime.py scripts/tests/test_reviewer_bundle.py -q --basetemp=$testDirectory
```

[Certain] Tests used the existing installed development environment, isolated databases/storage, synthetic inputs, and mocked provider transports. This is not a fresh dependency-installation or clean-machine acceptance claim. The source-only reviewer CI step was added while preserving the upstream offline, MySQL-matrix, and container-smoke jobs; remote CI has not run for these unpushed changes.

## Independent review

[Certain] One independent read-only review covered changed and newly added application, tests, migration/import integration, authentication, job capture, reviewer code, image provenance, and CI. It found one Important defect: malformed pre-existing evidence tables with complete column names could be accepted during a partial migration. The correction now validates compatible types/length/precision, nullability, primary/foreign keys, uniqueness, and required indexes before promotion. The seven regression cases were observed failing before the correction and passing afterward, followed by the passing full final suite. There were no additional actionable Minor findings.

[Certain] The reviewer's independent targeted execution was cancelled after three passes when the default sandbox stalled; it is not counted as a passing run. The complete verification counts above come from the primary agent's recorded commands/JUnit.

## Decisions and remaining limits

- Preserve the original dirty checkout and the managed integration worktree. Leave this branch uncommitted for review; do not merge, push, publish, select a license, or rewrite Git history.
- Retain newer upstream cloud implementation, safeguards, test fixtures, Docker files, and CI. Apply only genuine local evaluation/reviewer changes; do not restore obsolete ancestor copies.
- Reuse the existing synthetic slide for UI imagery. Hold the optional real-photo dataset outside the draft archive, keeping repository-upload permission separate from redistribution/model-upload permission.
- Actual MySQL DDL recovery/concurrency, Docker behavior, and WeChat device flows require their own environments. Preserve their CI/tests and report these limits rather than infer success from SQLite.
- Live paid-provider behavior and general recognition quality remain separate checks; this reconciliation needed no paid calls.
- License/redistribution rights, full current/history credential/privacy clearance, public quickstart and excluded-link cleanup, and publication remain future release work.
- Private credential-bearing Windows ZIP execution and redistribution are excluded. Keep its maintainer source/tests separate from public release assets.
- Archive file selection and hash fidelity are checked separately during assembly. A source-only archive without current private files does not clear the existing repository's branches/history for publication.

[Certain] Raw logs/JUnit and the per-source SHA-256 evidence are retained locally under ignored `output/open-source-reconcile-2026-11-11/`. See [reconciliation decisions](../source-reconciliation.md) and [UI image provenance](../ui-assets.md). No deferred Minor findings remain from the independent review.
