"""Create an experimental Windows test ZIP from a recorded portable build."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile


INSTRUCTIONS = """RailVehicle-Lab Windows 测试版（实验性）

完整解压后双击 RailVehicleLab/RailVehicleLab.exe 启动完整版，或双击 Start-Demo.bat 启动虚构数据演示。
访问 http://127.0.0.1:8501；保持启动窗口打开，按 Ctrl+C 退出。端口占用时可在命令行指定 --port 8502。
在车辆参数登记 DEMO-001 后，可在 CSV 导入页下载并导入虚构示例。基础分析不需要 AI 密钥。
数据库、配置和日志保存在 %LOCALAPPDATA%\\RailVehicle-Lab\\。AI 默认未配置。
本包未在无 Python 的干净 Windows 系统完成验收；真实 AI 调用需单独授权验收。本包不能视为正式发布。
使用 PowerShell Get-FileHash -Algorithm SHA256 <ZIP路径> 与旁边的 .sha256 文件比较；哈希不是数字签名。
"""
BLOCKED_SUFFIXES = {".csv", ".db", ".sqlite", ".sqlite3", ".log"}
BLOCKED_DIRS = {".venv", "__pycache__", ".git"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bundle_files(bundle: Path) -> list[Path]:
    """Refuse sensitive-looking paths and links rather than silently omit them."""
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("构建目录不存在或是链接。")
    files = []
    for root, dirs, names in os.walk(bundle, followlinks=False):
        for name in dirs + names:
            path = Path(root) / name
            lower = name.casefold()
            if (path.is_symlink() or lower in BLOCKED_DIRS or lower in {"secrets.toml", ".env"}
                    or lower.startswith(".env.") or path.suffix.casefold() in BLOCKED_SUFFIXES):
                raise ValueError("构建目录包含不允许打包的文件、目录或链接。")
        files.extend(Path(root) / name for name in names)
    return sorted(files)


def create_archive(bundle: Path, output: Path) -> tuple[Path, str]:
    bundle = Path(bundle)
    output = Path(output)
    if output.resolve().is_relative_to(bundle.resolve()):
        raise ValueError("输出目录不能位于构建目录内。")
    files = bundle_files(bundle)
    try:
        metadata = json.loads((bundle / "BUILD-METADATA.json").read_text(encoding="utf-8-sig"))
        commit = metadata["source_commit"]
        expected_hash = metadata["exe_sha256"]
        if (metadata.get("source_clean") is not True or not isinstance(commit, str)
                or not re.fullmatch(r"[0-9a-f]{40,64}", commit)
                or not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)):
            raise ValueError
        if sha256_file(bundle / "RailVehicleLab.exe") != expected_hash:
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("构建版本信息无效或 EXE 已改变，请重新运行构建脚本。") from exc
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"RailVehicle-Lab-v0.4-beta-test-{commit[:7]}-win64.zip"
    checksum = archive.with_name(archive.name + ".sha256")
    if archive.exists() or checksum.exists():
        raise FileExistsError("输出文件已存在；请选择其他输出目录。")
    version = (f"RailVehicle-Lab V0.4 beta test (experimental)\nSource commit: {commit}\n"
               f"Executable SHA-256: {expected_hash}\nClean Windows without Python: NOT VERIFIED\n"
               "Build metadata records source revision and executable hash; it is not a digital signature.\n")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in files:
            zipped.write(path, "RailVehicleLab/" + path.relative_to(bundle).as_posix())
        zipped.writestr("VERSION.txt", version)
        zipped.writestr("使用说明.txt", INSTRUCTIONS)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("压缩包完整性检查失败。")
    digest = sha256_file(archive)
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {archive.name}\n")
    return archive, digest


def main() -> int:
    parser = argparse.ArgumentParser(description="从已记录版本的目录式构建生成实验性测试包")
    parser.add_argument("bundle", type=Path, help="build_portable.ps1 输出的 RailVehicleLab 目录")
    parser.add_argument("output", type=Path, help="压缩包输出目录")
    args = parser.parse_args()
    try:
        archive, digest = create_archive(args.bundle, args.output)
    except ValueError as exc:
        print(f"打包失败：{exc}")
        return 1
    except (OSError, zipfile.BadZipFile):
        print("打包失败：请检查构建文件和输出目录权限及可用空间；已有文件不会覆盖。")
        return 1
    print(f"测试包：{archive}\nSHA-256：{digest}\n未完成干净 Windows 验收；请勿作为正式版发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
