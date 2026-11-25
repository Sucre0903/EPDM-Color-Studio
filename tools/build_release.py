"""Build both Windows editions from an immutable Git snapshot."""
import argparse
from collections import deque
from email.parser import BytesParser
from hashlib import file_digest
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

LOCK = "requirements-lock-win-amd64-py313.txt"
PACKAGE_ROOT = "ZT-Color-Match-Studio"
INDEX = "https://pypi.org/simple"
COMMON_FILES = (
    "VERSION", "app.py", "branding.py", "color_utils.py", "database.py",
    "excel_export.py", "ratio_search.py", "sample_preview.py", "windows_runtime.py",
    "requirements.txt", LOCK, "start.bat", "uninstall.bat", "start.sh", "README.md",
    ".streamlit/config.toml", "assets/logo.png", "assets/inter.css",
    "assets/inter-OFL.txt", "data/fk_colors.csv", "docs/OFFLINE_INSTALL.md",
)


def sha256(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def locked_entries(path):
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([a-z0-9-]+)==(\S+) --hash=sha256:([a-f0-9]{64})", line)
        if not match or match[1] in pins:
            raise ValueError(f"无效或重复锁定条目 {line}")
        pins[match[1]] = (match[2], match[3])
    if not pins:
        raise ValueError("锁文件为空")
    return pins


def verify_wheelhouse(directory, snapshot, runtime):
    pins = locked_entries(snapshot / LOCK)
    target = set(cpython_tags((3, 13), abis=[runtime["abi"]], platforms=[runtime["architecture"]]))
    target.update(compatible_tags((3, 13), interpreter=runtime["abi"], platforms=[runtime["architecture"]]))
    marker_env = {
        **default_environment(),
        "implementation_name": "cpython", "implementation_version": runtime["version"],
        "os_name": "nt", "platform_machine": "AMD64", "platform_python_implementation": "CPython",
        "platform_release": "11", "platform_system": "Windows", "platform_version": "10.0.26100",
        "python_full_version": runtime["version"], "python_version": "3.13", "sys_platform": "win32",
    }
    wheel_assets, requirements = {}, {}
    for wheel in sorted(directory.iterdir()):
        name, version, _, tags = parse_wheel_filename(wheel.name)
        checksum = sha256(wheel)
        if name in wheel_assets or pins.get(name) != (str(version), checksum) or not tags.intersection(target):
            raise ValueError(f"wheel 未与 Windows 依赖锁匹配 {wheel}")
        with ZipFile(wheel) as archive:
            metadata_files = [entry for entry in archive.namelist() if entry.endswith(".dist-info/METADATA")]
            if len(metadata_files) != 1:
                raise ValueError(f"wheel 元数据无效 {wheel}")
            meta = BytesParser().parsebytes(archive.read(metadata_files[0]))
        if (canonicalize_name(meta["Name"]) != name or meta["Version"] != str(version) or
                runtime["version"] not in SpecifierSet(meta.get("Requires-Python", ""))):
            raise ValueError(f"wheel 名称、版本或 Python 要求无效 {wheel}")
        requirements[name] = [Requirement(req) for req in meta.get_all("Requires-Dist", [])]
        wheel_assets[name] = {
            "path": f"wheelhouse/{wheel.name}", "name": name, "version": str(version),
            "size": wheel.stat().st_size, "sha256": checksum,
        }
    if set(pins) != set(wheel_assets):
        raise ValueError(f"wheelhouse 缺少 {sorted(set(pins) - set(wheel_assets))}")

    direct = (snapshot / "requirements.txt").read_text(encoding="utf-8").splitlines()
    remaining = deque((Requirement(line), "") for line in direct if line.strip() and not line.startswith("#"))
    visited = set()
    while remaining:
        req, extra = remaining.popleft()
        if req.marker and not req.marker.evaluate({**marker_env, "extra": extra}):
            continue
        name = canonicalize_name(req.name)
        if req.url or name not in pins or pins[name][0] not in req.specifier:
            raise ValueError(f"锁文件无法满足 Windows 目标依赖 {req}")
        key = (name, tuple(sorted(req.extras)))
        if key in visited:
            continue
        visited.add(key)
        remaining.extend((dep, active) for dep in requirements[name] for active in ("", *req.extras))
    if {name for name, _ in visited} != set(pins):
        raise ValueError("锁文件含有 Windows 依赖树未使用的项目")
    return list(wheel_assets.values())


def pip_download(snapshot, target):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_")}
    env["PIP_CONFIG_FILE"] = os.devnull
    subprocess.run([
        sys.executable, "-m", "pip", "--disable-pip-version-check", "download",
        "--index-url", INDEX, "--only-binary=:all:", "--no-deps", "--require-hashes",
        "--platform", "win_amd64", "--implementation", "cp", "--python-version", "3.13",
        "--abi", "cp313", "--retries", "2", "--timeout", "20", "--dest", str(target),
        "-r", str(snapshot / LOCK),
    ], env=env, check=True)


def verify_runtime(archive_path, runtime):
    if sha256(archive_path) != runtime["sha256"]:
        raise ValueError(f"Python 官方运行时 SHA256 不匹配 {archive_path}")
    with ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        required = {
            "tools/python.exe", "tools/python313.dll", "tools/python3.dll",
            "tools/vcruntime140.dll", "tools/vcruntime140_1.dll", "tools/LICENSE.txt",
            "tools/Lib/venv/__init__.py", "tools/Lib/ensurepip/__init__.py",
            "tools/DLLs/_ssl.pyd", "tools/DLLs/_sqlite3.pyd",
        }
        # CPython's NuGet layout uses tools directly for extension modules.
        required = {p if p in names else p.replace("/DLLs/", "/") for p in required}
        if not required <= names or not any(p.startswith("tools/Lib/ensurepip/_bundled/pip-") for p in names):
            raise ValueError("Python 运行时缺少 venv、ensurepip、标准库或 DLL")
        broken = archive.testzip()
        if broken:
            raise ValueError(f"Python 运行时 ZIP CRC 校验失败 {broken}")


def snapshot_tree(repo, commit, target):
    source = subprocess.check_output(["git", "archive", "--format=zip", commit], cwd=repo)
    with ZipFile(io.BytesIO(source)) as archive:
        archive.extractall(target)


def build_launcher(snapshot, target, tag, commit, go):
    env = os.environ | {"GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0", "GOTOOLCHAIN": "local"}
    subprocess.run([
        go, "build", "-trimpath", "-buildvcs=false",
        "-ldflags", f"-s -w -buildid= -X main.buildVersion={tag} -X main.buildCommit={commit}",
        "-o", str(target), ".",
    ], cwd=snapshot / "launcher", env=env, check=True)


def zip_entry(zip_file, path, name):
    entry = ZipInfo(f"{PACKAGE_ROOT}/{name}", (1980, 1, 1, 0, 0, 0))
    entry.compress_type = ZIP_STORED if path.suffix in {".whl", ".nupkg"} else ZIP_DEFLATED
    with path.open("rb") as source, zip_file.open(entry, "w") as target:
        shutil.copyfileobj(source, target)


def write_distribution(output, snapshot, launcher, info, assets):
    local_info = snapshot / "distribution.json"
    local_info.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    with ZipFile(output, "w") as archive:
        for relative in COMMON_FILES:
            zip_entry(archive, snapshot / relative, relative)
        zip_entry(archive, launcher, "ZTColorStudio.exe")
        zip_entry(archive, local_info, "distribution.json")
        for relative, path in assets.items():
            zip_entry(archive, path, relative)


def offline_assets(snapshot, workspace, runtime, args, tag, commit):
    wheelhouse = args.wheelhouse.resolve() if args.wheelhouse else workspace / "wheelhouse"
    if args.wheelhouse is None:
        wheelhouse.mkdir()
        pip_download(snapshot, wheelhouse)
    wheels = verify_wheelhouse(wheelhouse, snapshot, runtime)
    runtime_archive = args.runtime_archive.resolve() if args.runtime_archive else workspace / runtime["archive"]
    if args.runtime_archive is None:
        with urlopen(runtime["url"], timeout=60) as source, runtime_archive.open("wb") as target:
            shutil.copyfileobj(source, target)
    verify_runtime(runtime_archive, runtime)
    runtime_asset = {
        "path": f"python-runtime/{runtime['archive']}", "size": runtime_archive.stat().st_size,
        "sha256": runtime["sha256"],
    }
    manifest = {
        "schema": 1, "version": tag, "commit": commit, "lock_sha256": sha256(snapshot / LOCK),
        "runtime": runtime, "runtime_asset": runtime_asset, "wheels": wheels,
    }
    manifest_path = workspace / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums_path = workspace / "SHA256SUMS-offline.txt"
    sums_path.write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in [runtime_asset, *wheels]), encoding="utf-8")
    return {
        "offline/manifest.json": manifest_path,
        "offline/SHA256SUMS.txt": sums_path,
        f"offline/{runtime_asset['path']}": runtime_archive,
        **{f"offline/{w['path']}": wheelhouse / Path(w["path"]).name for w in wheels},
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--ref", default="HEAD", help="Git commit or tag")
    cli.add_argument("--edition", choices=["both", "lite", "full-offline"], default="both")
    cli.add_argument("--output", type=Path, default=Path("dist"))
    cli.add_argument("--wheelhouse", type=Path, help="Reuse an existing Windows wheelhouse")
    cli.add_argument("--runtime-archive", type=Path, help="Reuse the pinned official NuGet archive")
    cli.add_argument("--go", default="go", help="Go executable (requires version in launcher/go.mod)")
    args = cli.parse_args()

    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(["git", "rev-parse", "--verify", f"{args.ref}^{{commit}}"],
                                     text=True, cwd=root).strip()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zt-build-") as temp:
        workspace = Path(temp)
        snapshot = workspace / "source"
        snapshot_tree(root, commit, snapshot)
        tag = "v" + (snapshot / "VERSION").read_text(encoding="ascii").strip()
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
            raise ValueError(f"无效发行版本 {tag}")
        if (re.fullmatch(r"v[0-9].*", args.ref) and args.ref != tag):
            raise ValueError(f"Git 标签 {args.ref} 与 VERSION {tag} 不符")
        launcher = workspace / "ZTColorStudio.exe"
        build_launcher(snapshot, launcher, tag, commit, args.go)
        runtime = json.loads((snapshot / "launcher/runtime.json").read_text(encoding="utf-8"))
        editions = {"lite": "Lite", "full-offline": "Full-Offline"}
        pairs = []
        for mode, suffix in editions.items():
            if args.edition not in {"both", mode}:
                continue
            assets = {} if mode == "lite" else offline_assets(snapshot, workspace, runtime, args, tag, commit)
            filename = f"{PACKAGE_ROOT}-{tag}-Windows-{suffix}.zip"
            staged = workspace / filename
            info = {
                "mode": mode, "version": tag, "commit": commit,
                "initial_library_sha256": sha256(snapshot / "data" / "fk_colors.csv"),
            }
            write_distribution(staged, snapshot, launcher, info, assets)
            pairs.append((staged, sha256(staged)))
        sums = []
        for staged, digest in pairs:
            shutil.copyfile(staged, output / staged.name)
            sums.append(f"{digest}  {staged.name}\n")
            print(f"{staged.name}  {staged.stat().st_size:,} bytes  {digest}")
        (output / "SHA256SUMS.txt").write_text("".join(sums), encoding="ascii", newline="\n")
        print(f"Built {tag} from {commit}")


if __name__ == "__main__":
    main()
