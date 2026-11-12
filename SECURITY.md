# Security policy

This is a source-only developer preview without a hosted service or a guaranteed security-support period. Keep the existing private development repository and all credential-bearing review packages private. Production use requires your own deployment and security review.

## Report privately

Before reporting, check whether the published repository offers **Security → Report a vulnerability**. Use that private channel only if it is actually available. Private vulnerability reporting for a future repository has not been enabled or verified by this source preparation.

If the private channel is unavailable, do not place exploit details, keys, session tokens, personal material or affected photos in a public issue. At most, open a generic issue asking the maintainer to provide a private reporting channel, without technical details. No unverified email address is designated here.

For an exposed credential under your control, revoke/rotate it through its provider and inspect usage. Removing it from a current file or a Git commit does not revoke it. Coordinate any history/ref cleanup separately.

## Runtime boundaries

- Never expose `reviewer.py` or its one-time login URL through a public proxy. It is a local-only workflow, not production authentication.
- Keep API keys and WeChat AppSecret on the server; use private object storage and ownership checks.
- Shared mini-program configuration contains blank identifiers; configure your own environment before use.
- Do not publish `.reviewer-data`, `.env`, sessions, uploads, databases, model prompts containing private inputs, audit mirrors or private package outputs.
- The localhost TLS key in backend tests is intentionally public test material. Never deploy it as a real service key.
