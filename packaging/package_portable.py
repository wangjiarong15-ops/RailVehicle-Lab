"""Create the Windows x64 beta ZIP from a recorded portable build."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile


VERSION = "V0.5 Windows x64 Beta"
BUNDLE_NAME = "RailVehicle-Lab-Beta"
EXE_NAME = "RailVehicle-Lab.exe"
ZIP_NAME = "RailVehicle-Lab-V0.5-Windows-x64-Beta.zip"
RC_VERSION = "V1.0 RC1 Windows x64"
RC_ZIP_NAME = "RailVehicle-Lab-V1.0-RC1-Windows-x64.zip"
BLOCKED_SUFFIXES = {".csv", ".db", ".sqlite", ".sqlite3", ".log", ".key", ".pfx", ".p12"}
BLOCKED_NAMES = {".venv", "__pycache__", ".git", ".ssh", ".aws", ".git-credentials", ".netrc", "_netrc", "secrets.toml", ".env"}


def add_release_documents(bundle: Path, commit: str, version: str = VERSION) -> None:
    templates = Path(__file__).resolve().parent
    for source, target in (("PORTABLE_README.txt", "使用说明.txt"), ("PORTABLE_FEEDBACK.txt", "公测反馈模板.txt")):
        body = (templates / source).read_text(encoding="utf-8")
        if version == RC_VERSION:
            body = body.replace("RailVehicle-Lab V0.5", "RailVehicle-Lab V1.0 RC1", 1)
            if source == "PORTABLE_README.txt":
                body += ("\n工程项目提供资料版本与引用定位。高级轮轨动力学可打开工程研究并独立复算。"
                         "数字孪生工作台可恢复已保存的本机采集记录。\n")
        with (bundle / target).open("x", encoding="utf-8-sig", newline="\r\n") as stream:
            stream.write(body)
    with (bundle / "VERSION.txt").open("x", encoding="utf-8-sig", newline="\r\n") as stream:
        stream.write(f"RailVehicle-Lab {version}\n源码提交：{commit}\n"
                     "类型：免安装目录式用户公测；未签名，非正式发布。\n"
                     "无 Python 的干净 Windows：尚未验证。\n"
                     "BUILD-METADATA.json 记录程序及资源文件哈希；哈希不是数字签名。\n")


def is_link_or_reparse(path: Path) -> bool:
    """Reject Windows junctions as well as ordinary symbolic links."""
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bundle_files(bundle: Path) -> list[Path]:
    """Refuse sensitive-looking paths and links rather than silently omit them."""
    if not bundle.is_dir() or is_link_or_reparse(bundle):
        raise ValueError("构建目录不存在或是链接。")
    files = []
    def fail_on_walk_error(error):
        raise error

    for root, dirs, names in os.walk(bundle, followlinks=False, onerror=fail_on_walk_error):
        for name in dirs + names:
            path = Path(root) / name
            lower = name.casefold()
            if (is_link_or_reparse(path) or lower in BLOCKED_NAMES
                    or lower.startswith(".env.") or path.suffix.casefold() in BLOCKED_SUFFIXES):
                raise ValueError("构建目录包含不允许打包的文件、目录或链接。")
        files.extend(Path(root) / name for name in names)
    return sorted(files)


def write_build_metadata(bundle: Path, commit: str, clean: bool, version: str = VERSION) -> None:
    """Record every build file before distribution; this is not a signature."""
    bundle = Path(bundle)
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("Git 提交信息无效。")
    files = bundle_files(bundle)
    manifest = bundle / "BUILD-METADATA.json"
    if manifest.exists():
        raise FileExistsError("构建版本信息已存在，不会覆盖。")
    inventory = {path.relative_to(bundle).as_posix(): sha256_file(path) for path in files}
    if EXE_NAME not in inventory:
        raise ValueError("构建缺少启动程序。")
    if version not in (VERSION, RC_VERSION):
        raise ValueError("构建渠道无效。")
    metadata = {"version": version, "source_commit": commit, "source_clean": clean,
                "exe_sha256": inventory[EXE_NAME], "files_sha256": inventory}
    with manifest.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(metadata, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def publish_without_overwrite(temporary: Path, destination: Path) -> None:
    """Publish a completed file atomically without replacing an existing target."""
    if os.name == "nt":
        # Windows rename fails if the destination already exists.
        os.rename(temporary, destination)
    else:
        # POSIX rename can replace an existing file; hard-link creation cannot.
        os.link(temporary, destination)
        temporary.unlink()


def create_archive(bundle: Path, output: Path) -> tuple[Path, str]:
    bundle = Path(bundle)
    output = Path(output)
    if output.resolve().is_relative_to(bundle.resolve()):
        raise ValueError("输出目录不能位于构建目录内。")
    files = bundle_files(bundle)
    try:
        manifest_bytes = (bundle / "BUILD-METADATA.json").read_bytes()
        metadata = json.loads(manifest_bytes.decode("utf-8-sig"))
        commit = metadata["source_commit"]
        expected_hash = metadata["exe_sha256"]
        inventory = metadata["files_sha256"]
        if (metadata.get("version") not in (VERSION, RC_VERSION) or metadata.get("source_clean") is not True or not isinstance(commit, str)
                or not re.fullmatch(r"[0-9a-f]{40,64}", commit)
                or not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                or not isinstance(inventory, dict)):
            raise ValueError
        actual = {path.relative_to(bundle).as_posix(): sha256_file(path)
                  for path in files if path.name != "BUILD-METADATA.json" or path.parent != bundle}
        if actual != inventory or inventory.get(EXE_NAME) != expected_hash:
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("构建版本信息无效或文件已改变，请重新运行构建脚本。") from exc
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (RC_ZIP_NAME if metadata["version"] == RC_VERSION else ZIP_NAME)
    checksum = archive.with_name(archive.name + ".sha256")
    if archive.exists() or checksum.exists():
        raise FileExistsError("输出文件已存在；请选择其他输出目录。")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{archive.name}.", suffix=".partial", dir=output)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for path in files:
                zipped.write(path, BUNDLE_NAME + "/" + path.relative_to(bundle).as_posix())
        with zipfile.ZipFile(temporary) as zipped:
            if zipped.read(BUNDLE_NAME + "/BUILD-METADATA.json") != manifest_bytes:
                raise ValueError("构建信息在打包时发生变化。")
            for name, expected in inventory.items():
                digest = hashlib.sha256()
                with zipped.open(BUNDLE_NAME + "/" + name) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != expected:
                    raise ValueError("构建文件在打包时发生变化。")
        digest = sha256_file(temporary)
        publish_without_overwrite(temporary, archive)
    finally:
        # Only the uniquely created temporary file is removed on failure.
        temporary.unlink(missing_ok=True)
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {archive.name}\n")
    return archive, digest


def main() -> int:
    parser = argparse.ArgumentParser(description="从已记录版本的目录式构建生成实验性测试包")
    parser.add_argument("bundle", type=Path, help="build_portable.ps1 输出的 RailVehicle-Lab-Beta 目录")
    parser.add_argument("output", type=Path, nargs="?", help="压缩包输出目录")
    parser.add_argument("--record-build", metavar="COMMIT", help=argparse.SUPPRESS)
    parser.add_argument("--source-clean", choices=("true", "false"), help=argparse.SUPPRESS)
    parser.add_argument("--rc", action="store_true", help="记录 V1.0 RC1 构建渠道")
    args = parser.parse_args()
    try:
        if args.record_build:
            if args.output is not None or args.source_clean is None:
                parser.error("记录构建时需要 --source-clean，不能指定输出目录。")
            version = RC_VERSION if args.rc else VERSION
            add_release_documents(args.bundle, args.record_build, version)
            write_build_metadata(args.bundle, args.record_build, args.source_clean == "true", version)
            return 0
        if args.output is None or args.source_clean is not None or args.rc:
            parser.error("打包时需要指定输出目录。")
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
