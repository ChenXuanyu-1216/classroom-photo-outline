# Security policy

[简体中文](SECURITY.md) · **English**

This project is a source-only developer preview. It has no hosted public service or guaranteed security-support period. Production operators need their own deployment and security review. Keep private development archives and credential-bearing packages private.

## Report a vulnerability privately

Check whether the repository's Security page offers **Report a vulnerability**. Use that private channel when available. This document does not assume that the repository has enabled private reporting.

If no private channel is available, a public issue may ask the maintainer to provide one, without technical details. Do not post exploit instructions, API keys, session tokens, personal material or affected classroom photos. No unverified email address is designated as a reporting channel.

If a credential you control was exposed, revoke or rotate it with the provider and inspect usage. Deleting it from a file or Git history does not revoke it. Coordinate history cleanup separately.

## Deployment boundaries

- Keep `reviewer.py` and its one-time login URL on loopback. It does not provide production authentication.
- Keep model keys and WeChat AppSecret on the backend. Use private object storage and retain ownership checks.
- Supply your own mini-program AppID, cloud environment and service configuration before deployment.
- Do not publish `.reviewer-data`, populated `.env` files, sessions, databases, uploads, prompts containing private input, audit mirrors or private packages.
- The localhost TLS key in backend tests is intentionally public test material. Never use it as a real service credential.

Separate permissions are required to upload user material to a model or redistribute it. The [MIT License](LICENSE) covers project code and documentation; it does not grant permission for other people's classroom material.
