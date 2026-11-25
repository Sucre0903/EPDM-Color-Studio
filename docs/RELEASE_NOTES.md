# ZT Color Match Studio v14.1.0

V14 配色工作台现在提供两种 Windows 10 / 11 x64 发行包。两种发行包均包含
批量配色、推荐方案 / 方案探索、手动配色、材料预览及 Excel / PNG / SVG 导出。

| 发行包 | 适用场景 |
| --- | --- |
| **Windows Lite** | 下载体积优先。会复用兼容的 Python 与锁定版本依赖，缺少组件时首次联网。 |
| **Windows Full Offline** | 公司渲染机及受限网络。已带 CPython 3.13.17 x64 与完整 wheelhouse，首次启动可在断网条件下初始化。 |

完整解压后双击 `start.bat`。用 `SHA256SUMS.txt` 核对下载的 ZIP。
运行环境保存在本机用户目录。公司设备的 DLL 与应用控制规则仍适用。
升级旧版时保留 `data` 下自己的色库和历史记录。

安装和故障排查见包内 `docs/OFFLINE_INSTALL.md`。
