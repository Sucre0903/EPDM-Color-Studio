# 构建与发布

本次版本为 `v14.1.4`。发布包必须从同一提交生成。构建脚本只读取 Git 对象中的文件，
工作目录的未提交改动不会进入 ZIP。

## 构建

使用 Python 3.12 或以上，以及 `launcher/go.mod` 指定的 Go 版本。维护者运行：

```powershell
python -m pip install -r tools/build-requirements.txt
python tools/build_release.py --ref v14.1.4 --output dist
```

默认生成 Lite、Full Offline 与包含两个 ZIP 的 SHA256 文件。需要独立构建时可以加
`--edition lite` 或 `--edition full-offline` 并使用不同输出目录。使用已有标准 Windows
wheelhouse 和 Python 归档时可加 `--wheelhouse` 与 `--runtime-archive`。
构建器检查归档哈希、目标 ABI、版本约束、Windows 标记、依赖闭包和每个 wheel 的哈希。

锁文件由 `requirements.txt` 在 CPython 3.13 / Windows x64 目标上解析。升级时，先冻结
目标 Python 补丁版本与依赖，在干净 Windows 环境验证，再用所选 Windows wheel 的
SHA256 更新锁文件。核对 PyPI 文件的摘要、许可和撤回状态。Python 归档使用
`launcher/runtime.json` 指定的官方 NuGet 资产，PSF 和第三方许可位于归档中的
`tools/LICENSE.txt`。Inter 的 SIL OFL 许可位于 `assets/inter-OFL.txt`。

## GitHub 草稿

如果使用交付的 Git bundle，请在目标仓库克隆中核验并导入：

```powershell
git bundle verify C:\path\ZT-Color-Match-Studio-v14.1.4-Git.bundle
git fetch C:\path\ZT-Color-Match-Studio-v14.1.4-Git.bundle refs/tags/v14.1.4:refs/tags/v14.1.4
git switch main
git merge --ff-only v14.1.4
git push origin main
git push origin refs/tags/v14.1.4
```

如果目标主分支已有后续提交，检查差异并合入后重新打包，由实际发布提交创建标签。
仓库的 GitHub Actions 会构建两个 ZIP，执行 Windows x64 冒烟并创建 Release 草稿。
接受断网和公司设备验收后，在 GitHub 上核对草稿提交及 SHA256，再发布草稿。
草稿模板位于 `docs/RELEASE_NOTES.md`。

## Windows 验收

以下在 Windows 工作站或测试 VM 运行，命令会用带中文与空格的解压目录、独立的
且路径较短的 LocalAppData 测试目录，以及不可用的代理验证 Full Offline。Lite 测试覆盖跨发行模式的环境复用、无效镜像回退。还会验证旧数据迁移、保留依赖卸载和完全卸载。

```powershell
pwsh -File tools/test_windows_release.ps1 -Dist dist
```

正式验收需在干净的 Windows 10 / 11 x64 VM 上，用标准用户并在未安装 Python、
断开外部网络的情况下完整解压 Full Offline。双击 `start.bat`，确认浏览器主页、
批量配比、手动配方、预览和导出可用。记录首次、第二次和删除测试环境后的离线启动。
用公司批准的网络监测工具记录外部请求，同时核对运行时和 wheel 的 SHA256。
在两台实际渲染机记录 Windows 版本、账号权限、终端防护型号与运行结果，并覆盖 U 盘或
网络映射盘路径。遇到 DLL 拒绝访问，记录日志里完整的文件名、操作阶段和异常。
