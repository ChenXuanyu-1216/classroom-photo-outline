# Public source preparation — 2026-11-12

Configuration placeholders, public documentation and MIT licensing are complete in this source-only folder. The owner selected MIT, named ChenXuanyu-1216 as copyright holder and confirmed permission for the included contributions on 2026-11-12. This record does not announce a published repository or release.

## Changes

- Blank mini-program AppID, cloud environment and service fields; retain localhost development and explicit failure for missing cloud configuration.
- Empty `.env.reviewer.example` for the operator's own key, documented loopback startup and source-focused diagnostics. Normal WeChat authentication remains enforced.
- Git ignore exceptions retain all three empty examples while excluding populated configuration and `.reviewer-data`; private reviewer data is also excluded from Docker context.
- Chinese/English READMEs, local quickstart, documentation index, evaluation instructions, contribution/security guidance, changelog and asset/dependency notices now describe this included source.
- Remove old inventory/plan/private-draft control files from this copy; retain originals in the private development archive. Remove links to excluded historical material and the old private repository.
- Blank historical budget evidence; add a denied-by-default JSON authorization template with the exact machine-readable schema, separate from human permission records.
- Add `LICENSE` with copyright (c) 2026 ChenXuanyu-1216. Dependencies keep their own terms. Excluded classroom photos/user uploads receive no rights under the code license.
- Rebuild `MANIFEST.json` for this modified folder; it no longer claims the original ZIP's per-file contents are unchanged.

## Fresh validation

| Check | Result |
| --- | --- |
| Fresh Python 3.10 dependency installation | Runtime and corrected development requirements installed successfully in a new isolated environment; `pip check` passed |
| Full backend in temporary Git checkout | 775 passed, 11 real-MySQL cases skipped, 0 failures; 8 framework/JUnit warnings |
| Website | 103 passed, 0 failures |
| Mini-program with blank shared defaults | 70 passed, 0 failures |
| Source reviewer from the Gitless source folder | 2 passed, 1 private-ZIP check skipped, 0 failures |
| Gitless backend smoke | 4 passed, 0 failures; overlaps backend coverage |
| Documentation | Relative Markdown paths and heading anchors checked; no broken target/anchor remains |
| Private configuration boundaries | Nine positive/negative Git-ignore cases passed; Docker reviewer-data exclusion checked |
| Redacted Gitleaks 8.30.1 scan | One intentional localhost TLS test-key finding, no additional finding; no blanket suppression |

An unconstrained fresh install initially resolved pytest 9.2.0, which failed before collection with `NodeId._str_cache` under Python 3.10. The same failure reproduced with an empty directory and `--noconftest`. `requirements-dev.txt` now uses `pytest>=8.0.0,<9.2`; fresh installation selected 9.1.1 and independent bootstrap succeeded, followed by the passing suite above. No application code was changed to hide that dependency failure.

The existing provenance tests require a Git HEAD commit. Running the full suite directly in a never-initialized source folder exposed that prerequisite; public instructions now state it and offer Git-independent smoke tests. For full acceptance, identical source was copied to a separate temporary checkout under the private working evidence directory, with a local verification-only commit. The user's source folder remains without `.git`; no old history or remote was copied or created there.

Tests use temporary data, synthetic fixtures and mocked external transports. No owner credential, live model call, private-package execution, production migration or remote publication was used. The runtime reviewer tests exercise saved work and Word export through mocked provider responses; they do not establish real-model quality or WeChat authentication.

## Review and limits

One independent read-only review found no Critical or Important issue. Its three documentation/help findings were corrected: historical budget defaults, authorization JSON instructions and the reviewer example-file reference. Runtime execution and final manifest verification were performed separately by the implementer. No deferred reviewer finding remains.

This source preparation does not independently establish legal ownership beyond the owner's confirmation, exhaustively inventory future bundled dependencies, validate live provider availability/pricing, or verify MySQL, Docker, WeChat devices or a deployed service. Platform-specific commands for macOS/Linux are documented but were not executed on those operating systems.

Fresh installation and offline source checks are now recorded, so they need not be inferred from the older reconciliation counts. Remaining release work is to create a fresh repository/commit with intended public attribution, verify remote CI and any advertised deployment/live/device behavior, review the exact release commit, and obtain publication authorization. The existing development repository must stay private because its retained history contains held classroom material.

The original audited ZIP remains unchanged, SHA-256 `9b685322138fbf9078f9bfb14a9c1e28c8723045ac0578279db1de9adbbdc3ae`. This folder is a modified successor. Private raw logs, backups, dependency metadata and the temporary verification checkout remain outside this source folder under ignored `output/public-prep-2026-11-12/` in the original workspace; do not publish that working evidence directory.

See [release preparation](../open-source-release.md), [quickstart](../local-quickstart.md), [MIT license](../../LICENSE) and [third-party notices](../../THIRD_PARTY_NOTICES.md).
