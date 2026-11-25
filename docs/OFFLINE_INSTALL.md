# Windows 10 / 11 x64 部署

| 下载包 | 第一次启动 |
| --- | --- |
| Windows Lite | 先复用本机已初始化环境和兼容的 CPython 3.13 x64。缺少运行时或依赖时联网获取。 |
| Windows Full Offline | 随包带 CPython 3.13.17 x64 和完整 wheelhouse。用本地文件初始化。 |

1. 用发布页的 `SHA256SUMS.txt` 核对 ZIP，可在 PowerShell 执行 `Get-FileHash -Algorithm SHA256 '<ZIP 路径>'`。
2. 完整解压到有读写权限的目录，双击 `start.bat`。退出启动窗口即可停止服务。
3. 第一次初始化会创建本机虚拟环境，后续启动会验证并复用它。
   出现错误时，启动窗口和日志会显示具体文件、阶段及异常。

应用可放在本机、U 盘或公司共享盘。运行时、虚拟环境、缓存和日志位于当前用户的
`%LOCALAPPDATA%\ZTColorStudio\studio-v14`。色库与历史数据库仍在应用目录中的
`data` 下。从旧版迁移时，用现有色库和历史库迁到新版的对应位置，建议先备份。
同时保留两套解压目录时，两套数据存储各自读写。

Full Offline 的 `offline/python-runtime` 中包含官方原始 Python NuGet 归档，启动器
校验归档摘要并将其 `tools/` 运行时写入本机。依赖安装只读取随包的 wheelhouse。
可以执行 `start.bat --verify-offline` 检查归档与每个 wheel 的 SHA256。
Full Offline 缓存损坏后仍从已验证的发行包恢复。完整环境可能占用数百 MiB。

Lite 的默认依赖源为 PyPI，缺少兼容 Python 时向 NuGet 获取固定归档。如需在
当前 PowerShell 会话指定已获批准的镜像或解释器，可执行：

```powershell
$env:ZT_PYPI_INDEX_URL = 'https://你的公司镜像/simple'
$env:ZT_PYTHON_EXE = 'C:\已批准的Python\python.exe'
.\start.bat
```

镜像失败后会切换到官方 PyPI。应用不会改动已安装 Python 的包目录。
Lite 使用已有解释器时，应用虚拟环境可以读取其中符合精确锁定版本的依赖。
启动窗口显示 pip 进度，下载源和日志路径。

Windows 默认监听本机回环地址。已有获准的局域网使用方式可以在启动前设置
`$env:ZT_SERVER_ADDRESS = '0.0.0.0'`。同一个 Streamlit 实例中的用户共用数据。

如遇 DLL 创建、写入或运行失败，请把最新的 `logs\*.log` 交给公司 IT。
Windows 文件权限和企业安全策略仍适用。关闭使用中的实例后，可移除对应发行模式的
`environments` 子目录来重建环境。应用的 CSV 和 SQLite 位于应用目录，不在缓存中。
