"""Checks for the Windows release launcher. This script uses only stdlib."""
import importlib
import importlib.metadata
from pathlib import Path
import re
import sqlite3  # Checks the database extension as part of the base probe.
import ssl  # Checks the SSL extension as part of the base probe.
import sys
import sysconfig

LOCK_LINE = re.compile(r"([a-z0-9-]+)==(\S+) --hash=sha256:[a-f0-9]{64}")


def interpreter(expected, base):
    if (
        sys.implementation.name != "cpython"
        or sysconfig.get_platform() != "win-amd64"
        or sys.version_info[:2] != (3, 13)
        or sysconfig.get_config_var("Py_GIL_DISABLED")
        or sys.version_info.releaselevel != "final"
        or (expected and sys.version.split()[0] != expected)
    ):
        raise RuntimeError("需要标准版 CPython 3.13 x64")
    if base:
        if sys.prefix != sys.base_prefix:
            raise RuntimeError("需要基础 Python，不能用已有虚拟环境创建应用环境")
        import ensurepip
        import venv

        ensurepip.version()


def environment(lock_path):
    entries = []
    for line in Path(lock_path).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            match = LOCK_LINE.fullmatch(line)
            if not match:
                raise ValueError(f"无效锁文件条目 {line}")
            entries.append(match.groups())
    if not entries:
        raise ValueError("依赖锁文件为空")
    mismatched = []
    for name, expected in entries:
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = "缺失"
        if installed != expected:
            mismatched.append(f"{name} {installed} → {expected}")
    if mismatched:
        raise RuntimeError("依赖版本校验失败\n" + "\n".join(mismatched))
    for module in (
        "numpy", "pandas", "PIL.Image", "scipy.optimize", "scipy.spatial",
        "pyarrow", "streamlit", "openpyxl",
    ):
        importlib.import_module(module)


if __name__ == "__main__":
    operation, *args = sys.argv[1:]
    if operation == "base":
        interpreter(args[0], base=True)
    elif operation == "env":
        interpreter(args[1], base=False)
        environment(args[0])
    else:
        raise ValueError(operation)
