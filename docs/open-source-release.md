# Source release preparation

Prepared 2026-11-12 for a new, source-only repository. Project code/documentation use the [MIT License](../LICENSE); the owner confirmed permission for the included contributions. Dependencies and assets have the separate boundaries in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Included and excluded

Included: backend, browser, mini-program, prompts, tests, required synthetic fixtures, evaluation templates, Docker/CI source and maintainer source. Owner AppID and cloud defaults are blank. Local secrets and reviewer data are ignored. The source reviewer uses the operator's own key and loopback authentication; normal WeChat ownership enforcement remains enabled.

Excluded: old Git history, real classroom photos/pilot records, private packages, local configuration, runtime databases/uploads, course submission reports and raw audit output. The obsolete file-selection inventory and old internal release plan were removed from this public preparation folder; their originals remain in the private development archive.

## Verification

The [2026-11-11 reconciliation record](verification/2026-11-11-source-reconciliation.md) describes the earlier draft, before this configuration/documentation pass. Current checks and limits are recorded in [2026-11-12 source preparation](verification/2026-11-12-public-source-preparation.md). Historical numbers must not be presented as new execution results.

## Remaining publication gates

1. Fresh Windows/Python 3.10 dependency installation and offline reviewer/smoke checks passed; retain that evidence and verify other advertised platforms independently.
2. Run the required full regression and real-MySQL/container checks on the exact release commit; record unavailable environments honestly.
3. If advertising live recognition acceptance, perform a bounded authorized provider call using synthetic or permission-cleared input and record defects/costs separately.
4. Initialize fresh Git history with intended public attribution, create a new private repository and verify CI on the same commit.
5. Configure a private vulnerability-reporting channel if available, review the final diff/manifest and obtain authorization before making the repository public or tagging a release.

The existing development repository must remain private: its retained history still contains held classroom material. This source preparation does not clear that history or change repository visibility. No public repository URL is invented before the repository exists.
