# ZT Color Match Studio v14.1.5

本版修复 Windows 卸载在成功删除应用目录后仍可能返回失败的问题。卸载可按需清理软件目录、应用管理的 Python 和依赖，
同时备份个人色库和 SQLite 历史。升级可导入旧版数据，并复用已验证的兼容环境。

两种 Windows 10 / 11 x64 发行包均包含
批量配色、推荐方案 / 方案探索、手动配色、材料预览及 Excel / PNG / SVG 导出。

| 发行包 | 适用场景 |
| --- | --- |
| **Windows Lite** | 下载体积优先。会复用兼容的 Python 与锁定版本依赖，缺少组件时首次联网。 |
| **Windows Full Offline** | 公司渲染机及受限网络。已带 CPython 3.13.17 x64 与完整 wheelhouse，首次启动可在断网条件下初始化。 |

完整解压后双击 `start.bat`。用 `SHA256SUMS.txt` 核对下载的 ZIP。
运行环境保存在本机用户目录。如果 360 安全卫士或其他终端防护软件拦截安装，请将
启动日志和发行包 SHA256 交给公司 IT，由 IT 核验并处理放行。
升级时在首次启动前导入旧版数据。卸载请双击 `uninstall.bat` 并核对删除范围。

安装和故障排查见包内 `docs/OFFLINE_INSTALL.md`。
