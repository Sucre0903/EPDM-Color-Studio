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
`%LOCALAPPDATA%\ZTColorStudio\studio-v14`。每份解压副本的色库与历史数据库位于该副本的
`data` 下。

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

## 升级与环境复用

关闭旧版，在新目录完整解压新版。首次打开新版前，在新版目录的 PowerShell 中执行：

```powershell
.\start.bat --import-data 'D:\旧版或卸载备份目录'
```

指定包含 `data` 子目录的目录。启动器将色库、SQLite 历史及其他数据文件一起迁移，
检测到新版已经产生用户数据时会停止导入。导入后双击 `start.bat`。

启动器以 Python 运行时和依赖锁识别环境。两者一致时，同一发行模式可直接复用
已验证的环境。Lite 也可复用通过校验的 Full Offline 环境。Full Offline 可复用
由工具管理且没有继承系统依赖的 Lite 环境。日志会显示复用结果。

依赖锁或运行时改变时，新版需要建立对应的环境，并复用可用的下载缓存。
确认旧版本不再使用后，可在新版目录运行 `.\start.bat --maintenance prune`，
清理过期的应用环境和运行时。

## 卸载

关闭本机所有 ZT Color Match Studio 启动窗口。双击要卸载那份副本中的 `uninstall.bat`。

| 选项 | 清理范围 |
| --- | --- |
| **K** | 删除该应用目录。共用运行时和依赖留给其他副本或后续升级。 |
| **P** | 删除该应用目录以及 `studio-v14` 下的应用运行时、虚拟环境、依赖、下载缓存和日志。 |
| **Q** | 取消。 |

核对提示中的目录并输入 `Y`。两种卸载方式都会先备份 `data` 到
`%LOCALAPPDATA%\ZTColorStudio\backups`，备份失败时停止。卸载时请核对完整应用目录，
该目录中的其他文件也会一起移除。保留其他版本时选 **K**。完全卸载选 **P**。
此前安装的系统 Python 或 Anaconda 位于各自的安装目录，供其他项目使用。

v14.1.3 和更早的发行包可手动卸载。关闭应用后，先备份应用目录下的 `data`，
再删除解压出来的应用目录。若所有副本均停止使用，再删除
`%LOCALAPPDATA%\ZTColorStudio\studio-v14`。

如遇 DLL 创建、写入或运行失败，请把最新的 `logs\*.log` 交给公司 IT。
Windows 文件权限和企业安全策略仍适用。
