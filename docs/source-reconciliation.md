# Source reconciliation and draft candidate

[Certain] Date: 2026-11-11 (America/Los_Angeles). This work reconciles the selected local application/evaluation/reviewer changes with remote `main` at `4e599ba1830711ae4236169429a62db3cce2f89a`. The managed branch is `codex/open-source-reconcile`; the original dirty checkout is preserved.

## Merge decisions

- Retain upstream cloud storage, MySQL support, migration/import tools, task coordination/quotas, runtime safeguards, TLS handling, mini-program cloud transport, Docker files, and CI jobs.
- Retain upstream files when local bytes match an older version in upstream history. These are stale copies, not new deltas to apply.
- Add local owner-scoped evaluation APIs/UI, immutable terminal snapshots, append-only human reviews, module-binding checks, and local reviewer runtime/maintainer source.
- Resolve startup/router and job-capture overlaps explicitly. Cloud startup still requires explicit schema migration, and embedded consumers still start only in local mode.
- Replace both held UI photo copies with the existing synthetic fixture. See [image provenance](ui-assets.md).
- Keep historical reports, private ZIPs, runtime data, owner credentials, and the optional real-photo pilot outside the draft source archive.

## Explicit cloud schema upgrade

[Certain] The evaluation tables require revision `cloud-0002`. Fresh databases initialize to that revision. A previously valid `cloud-0001` database is upgraded only by the existing explicit command, adding `evaluation_snapshot` and `evaluation_review` without dropping existing business tables. Startup does not perform this upgrade.

Before upgrading a deployed database, stop the API and workers and create a restorable database/storage backup. Use the reconciled code with the intended operator configuration; from `lecture-syllabus-generator/backend`, run:

```powershell
python -m app.manage migrate-cloud
```

[Certain] The command retains the upstream MySQL migration lock and table encoding/collation settings. It rejects unknown/nonversioned or incomplete legacy schemas. Intact added tables may be reused after an interrupted upgrade; malformed existing evaluation tables are rejected. Old database/code combinations should be rolled back together from the operator's verified backup if rollback is needed; this task performs no deployment or database upgrade.

[Certain] Offline SQLite-to-cloud import now includes evaluation snapshots/reviews when present and accepts older source databases that predate both tables. It still excludes active sessions and pending jobs. Historical job IDs inside immutable evidence remain historical references, not replayable imported jobs. Generate a fresh inventory fingerprint using the reconciled code before any import.

## Cloud evidence

[Certain] Evaluation capture reads cloud originals through the existing bounded storage abstraction. It records their byte hash without putting the object reference or credentials in the evidence. An unavailable source records a null hash and does not prevent saving the terminal evidence. Capture/review/export do not call a model.

## Verification and limits

[Certain] The remote baseline passed 755 backend tests with 11 real-MySQL tests skipped, plus 97 website and 70 mini-program tests. A first baseline run reached all cases but exited during cleanup of an inaccessible shared pytest link; the successful rerun used a unique temporary directory without changing that shared link.

[Certain] Focused reconciliation tests first reproduced missing migration/evidence behavior. The integration-focused run then passed 50 tests; website and mini-program checks passed 103 and 70 respectively, and source reviewer checks passed two tests with the private-ZIP acceptance check skipped because no private ZIP is bundled. Final full-suite and candidate results are recorded in the verification record accompanying this work.

[Certain] Checks use temporary databases, the synthetic slide, and mocked external transports. They do not establish actual MySQL upgrade behavior, Docker image behavior, WeChat device acceptance, public hosting readiness, or real-model accuracy on this host. No live model calls or production-data migration were performed.

## Release status

This document records the 2026-11-11 integration. The copied source has since received configuration placeholders, current documentation and MIT licensing; see [current release preparation](open-source-release.md). Its earlier test counts remain historical evidence. Existing private-repository history is not cleared for publication by this source-only preparation.
