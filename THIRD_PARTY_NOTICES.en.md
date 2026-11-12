# Third-party notices and asset boundaries

[简体中文](THIRD_PARTY_NOTICES.md) · **English**

The project source and documentation use the MIT License in `LICENSE`, with copyright (c) 2026 ChenXuanyu-1216. The owner selected this license and confirmed permission for the included contributions on 2026-11-12. Existing contributor attribution must be preserved; this statement does not relicense third-party packages.

## Dependencies

This source distribution references dependencies through pip requirements; it does not bundle their installed libraries, Python, Node.js or a portable runtime. The following direct dependency metadata was inspected in the existing development environment on 2026-11-12. These versions are an inventory of that environment, not a lockfile, fresh-installation result or exhaustive inventory of transitive packages/native components. Consult each installed distribution's license files and the corresponding upstream version when redistributing dependencies.

| Package | Reviewed version | Declared license | Source |
| --- | --- | --- | --- |
| fastapi | 0.115.14 | MIT | [upstream](https://github.com/fastapi/fastapi) |
| uvicorn | 0.34.3 | BSD-3-Clause | [upstream](https://www.uvicorn.org/) |
| SQLAlchemy | 2.0.54 | MIT | [upstream](https://www.sqlalchemy.org) |
| PyMySQL | 1.2.3 | MIT | [upstream](https://github.com/PyMySQL/PyMySQL) |
| cos-python-sdk-v5 | 1.9.44 | MIT | [upstream](https://github.com/tencentyun/cos-python-sdk-v5) |
| pydantic | 2.14.1 | MIT | [upstream](https://github.com/pydantic/pydantic) |
| pydantic-settings | 2.15.0 | MIT | [upstream](https://github.com/pydantic/pydantic-settings) |
| python-dotenv | 1.2.4 | BSD-3-Clause | [upstream](https://github.com/theskumar/python-dotenv) |
| python-multipart | 0.0.32 | Apache-2.0 | [upstream](https://github.com/Kludex/python-multipart) |
| PyYAML | 6.0.3 | MIT | [upstream](https://pyyaml.org/) |
| python-docx | 1.2.0 | MIT | [upstream](https://github.com/python-openxml/python-docx) |
| httpx | 0.28.1 | BSD-3-Clause | [upstream](https://github.com/encode/httpx) |
| certifi | 2026.7.22 | MPL-2.0 | [upstream](https://github.com/certifi/python-certifi) |
| Pillow | 12.3.0 | MIT-CMU | [upstream](https://python-pillow.github.io) |
| pytest | 9.1.1 | MIT | [upstream](https://docs.pytest.org/en/latest/) |

Python packages keep their own copyright and license notices. In particular, certifi's MPL-2.0 and Pillow's MIT-CMU terms remain distinct from the project MIT license. A future binary/container distribution must inventory its complete transitive/native runtime and retain the required notices; this source-only table does not complete that work.

## Included assets and test material

- The project-created synthetic slide fixture and its two identical UI copies are covered by the project license. See [image provenance](docs/ui-assets.md). They are not classroom photographs.
- `backend/tests/fixtures/synthetic-cloudrun-ca.pem` and `synthetic-cloudrun-server-key.pem` under `lecture-syllabus-generator/` are intentional localhost-only test material. They must never be used as production TLS credentials.
- No real classroom photos, optional historical pilot package, private annotations, private review package, model credentials, fonts or installed dependency libraries are bundled in this source folder.
- Platform names, external service terms, user-uploaded materials and any provider output retain their applicable rights. The code license grants no additional permission to upload, publish or redistribute classroom material.
