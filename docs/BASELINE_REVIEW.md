# V14 基线审查

本轮 `fk_color_matcher_fixed.zip` 是唯一业务基线，SHA256 为
`57f427a04646b0724ba379f8a949e0f854f4ed3fa54f9d8bb163c649794ef8db`。
ZIP 有 18 个条目，其中 14 个文件、4 个目录。

| 内容 | 实际文件 |
| --- | --- |
| 应用 | `app.py`、`branding.py`、`color_utils.py`、`database.py`、`excel_export.py`、`ratio_search.py`、`sample_preview.py` |
| 资源 | `assets/logo.png`、`data/fk_colors.csv`、`.streamlit/config.toml` |
| 启动与说明 | `start.bat`、`start.sh`、`requirements.txt`、`README.md` |

原 Windows 启动依次查找 PATH Python 与两个 Anaconda 位置，试导入 Streamlit、
SciPy 和 openpyxl，失败时用宽松范围安装，再启动 Streamlit。
六个直接依赖为 Streamlit、pandas、NumPy、Pillow、SciPy 和 openpyxl。
V14 没有 Windows 运行时管理、离线资源或依赖锁。

公开 GitHub 的 `main` 指向 `e2aa0d9a2d565438a33389ee0fe666a555ecbc1a`，
没有标签和 Release。其 `app.py`、`ratio_search.py` 等 Git blob 与本轮 ZIP
不同，并且没有 `excel_export.py`。V14 ZIP 没有 Git 元数据，无法确定其原始
commit。本次以 ZIP 内容创建源码快照，GitHub 的旧 commit 仅用于核对及后续
接入历史。

本次增加 Windows 原生启动器、Python 环境探测脚本、依赖锁、构建与验收脚本和
GitHub Release 工作流。两个包使用相同应用源文件。原 V14 通过 Google Fonts 请求
Inter，本次随包提供同款字型，防止离线浏览器发起该请求。原有配色、预览、
导出、色库及历史读写逻辑均沿用 V14。
