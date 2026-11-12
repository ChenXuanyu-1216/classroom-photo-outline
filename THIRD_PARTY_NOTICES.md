# 第三方声明与素材范围

**简体中文** · [English](THIRD_PARTY_NOTICES.en.md)

项目源码和文档采用 [MIT 许可证](LICENSE)，版权所有 © 2026 ChenXuanyu-1216。所有者于 2026 年 11 月 12 日选定该许可证，并确认拥有许可所含贡献的权限。已有贡献者的署名应继续保留，第三方依赖仍适用各自条款。

## 依赖

源码通过 pip requirements 引用依赖，不附带安装后的依赖库、Python、Node.js 或便携运行环境。下表来自 2026 年 11 月 12 日已有开发环境的直接依赖元数据，用于说明当时检查的版本，不是锁定文件，也不穷尽间接依赖或原生组件。分发依赖时，应查看实际版本随附的许可证文件和上游声明。

| 依赖 | 已检查版本 | 声明的许可证 | 来源 |
| --- | --- | --- | --- |
| fastapi | 0.115.14 | MIT | [项目来源](https://github.com/fastapi/fastapi) |
| uvicorn | 0.34.3 | BSD-3-Clause | [项目来源](https://www.uvicorn.org/) |
| SQLAlchemy | 2.0.54 | MIT | [项目来源](https://www.sqlalchemy.org) |
| PyMySQL | 1.2.3 | MIT | [项目来源](https://github.com/PyMySQL/PyMySQL) |
| cos-python-sdk-v5 | 1.9.44 | MIT | [项目来源](https://github.com/tencentyun/cos-python-sdk-v5) |
| pydantic | 2.14.1 | MIT | [项目来源](https://github.com/pydantic/pydantic) |
| pydantic-settings | 2.15.0 | MIT | [项目来源](https://github.com/pydantic/pydantic-settings) |
| python-dotenv | 1.2.4 | BSD-3-Clause | [项目来源](https://github.com/theskumar/python-dotenv) |
| python-multipart | 0.0.32 | Apache-2.0 | [项目来源](https://github.com/Kludex/python-multipart) |
| PyYAML | 6.0.3 | MIT | [项目来源](https://pyyaml.org/) |
| python-docx | 1.2.0 | MIT | [项目来源](https://github.com/python-openxml/python-docx) |
| httpx | 0.28.1 | BSD-3-Clause | [项目来源](https://github.com/encode/httpx) |
| certifi | 2026.7.22 | MPL-2.0 | [项目来源](https://github.com/certifi/python-certifi) |
| Pillow | 12.3.0 | MIT-CMU | [项目来源](https://python-pillow.github.io) |
| pytest | 9.1.1 | MIT | [项目来源](https://docs.pytest.org/en/latest/) |

Python 依赖保留各自的版权和许可声明。certifi 的 MPL-2.0 与 Pillow 的 MIT-CMU 条款独立于项目 MIT 许可。以后若分发二进制、容器或完整运行环境，还需要检查全部间接依赖与原生组件，保留适用声明；本表不代表那项工作已经完成。

## 随源码提供的素材

- 项目生成的合成课件测试图及其两份 UI 副本采用项目许可，见[图片来源](docs/ui-assets.md)。这些文件不包含真实课堂照片。
- `lecture-syllabus-generator/backend/tests/fixtures/` 中的 `synthetic-cloudrun-ca.pem` 与 `synthetic-cloudrun-server-key.pem` 用于 localhost 测试，不能充当生产 TLS 凭据。
- 源码不附带真实课堂照片、历史试标数据包、私有标注、私有评审包、模型凭据、字体或已安装的依赖库。
- 平台名称、外部服务、用户上传资料和模型输出保留各自适用的权利与条款。代码许可不额外授予课堂资料的上传、公开或再分发权限。
