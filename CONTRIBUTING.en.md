# Contributing

[简体中文](CONTRIBUTING.md) · **English**

Start with the [source quickstart](docs/local-quickstart.md) and your own private configuration. Use synthetic inputs and temporary test data. For a pull request, explain what you changed, the reason, the checks you ran and what remains unverified.

## Before changing code

For a bug, provide reproducible steps and a redacted error. For a change that affects existing behavior, describe the expected result and the affected entry points. Keep unrelated changes separate so the result is easier to review.

Install runtime and development requirements, then run the backend, website, mini-program and source-reviewer checks in [README.en.md](README.en.md#tests). Real MySQL and container jobs need their own environments. Mocked provider results do not count as live-model evidence.

## Keep private material out of contributions

Do not commit API keys, WeChat AppSecret, sessions, populated configuration, databases, uploads, classroom photos, private permission conversations, raw logs or credential-bearing packages. Do not put your own AppID or cloud target into shared defaults.

The empty `.env.example`, `.env.cloud.example` and `.env.reviewer.example` files are public references. Their populated copies are private. Use the synthetic slide or material that you have permission to redistribute. Permission for local use, model upload and public distribution must be confirmed separately. Obtain authorization for both the input and costs before a paid test.

`scripts/build_reviewer_bundle.py` is historical maintainer tooling that can produce a private package containing credentials. Do not use its output as a public release asset.

## License and attribution

Submit only material you have the right to contribute under the [MIT License](LICENSE). Preserve existing attribution and dependency notices. Identify copied code, generated assets and additional dependencies with their applicable permissions and terms; see [third-party notices](THIRD_PARTY_NOTICES.en.md).

Report vulnerabilities through the [security policy](SECURITY.en.md). The [branching guide](docs/branching.md) describes local branch and review conventions.
