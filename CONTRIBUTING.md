# Contributing

Install from [the source quickstart](docs/local-quickstart.md). Use your own private configuration, temporary test data and synthetic inputs. Keep changes focused and record what you changed, why, the checks run and remaining limits in your pull request.

## Required checks

Install `requirements.txt` and `requirements-dev.txt`, then run backend pytest, both Node.js test suites and the source-reviewer runtime checks listed in [README.en.md](README.en.md#tests). Real MySQL and container jobs have their own environments. Never report mocked provider behavior as a live model result.

## Data, credentials and costs

Do not commit API keys, WeChat AppSecret, sessions, owner identifiers in shared defaults, local configuration, databases, uploads, classroom photos, private permission conversations, generated private ZIPs or raw logs. Empty `.env.example`, `.env.cloud.example` and `.env.reviewer.example` files are safe configuration references; their populated copies are private.

Use the synthetic slide or data you are allowed to redistribute. Local permission, cloud-model upload and public redistribution are separate decisions. Do not run paid recognition as part of an unsolicited test; obtain authorization for the input and costs first.

`scripts/build_reviewer_bundle.py` is historical maintainer tooling for a credential-bearing private package. It is not a public build recipe, and its output must never become a public release asset.

## Licensing and attribution

By submitting a contribution, you confirm that you have the right to submit it under the project's [MIT License](LICENSE). Preserve existing attribution and upstream notices. Declare copied code, generated assets and additional dependencies with their applicable permissions and terms; see [third-party notices](THIRD_PARTY_NOTICES.md).

For vulnerabilities, follow [SECURITY.md](SECURITY.md) rather than posting sensitive details publicly. Local branch naming and review guidance is in [docs/branching.md](docs/branching.md).
