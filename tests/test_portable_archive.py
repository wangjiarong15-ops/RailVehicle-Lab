"""Versioned test archives must preserve provenance and exclude local data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile


spec = importlib.util.spec_from_file_location(
    "portable_archive", Path(__file__).resolve().parents[1] / "packaging" / "package_portable.py"
)
archive_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_tool)


class PortableArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "RailVehicleLab"
        self.bundle.mkdir()
        (self.bundle / "RailVehicleLab.exe").write_bytes(b"fictional executable")
        (self.bundle / "app.py").write_bytes(b"fictional app")
        self.metadata = {"source_commit": "a" * 40, "source_clean": True,
                         "exe_sha256": hashlib.sha256(b"fictional executable").hexdigest(),
                         "files_sha256": {"RailVehicleLab.exe": hashlib.sha256(b"fictional executable").hexdigest(),
                                          "app.py": hashlib.sha256(b"fictional app").hexdigest()}}
        self.write_metadata()
        self.output = self.root / "output"

    def write_metadata(self):
        (self.bundle / "BUILD-METADATA.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def test_archive_contains_recorded_revision_and_valid_checksum(self):
        archive, digest = archive_tool.create_archive(self.bundle, self.output)
        self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertIn(digest, archive.with_name(archive.name + ".sha256").read_text())
        with zipfile.ZipFile(archive) as zipped:
            self.assertIsNone(zipped.testzip())
            self.assertIn("a" * 40, zipped.read("VERSION.txt").decode())
            self.assertIn("使用说明.txt", zipped.namelist())

    def test_rejects_unrecorded_dirty_or_changed_build(self):
        for change in ("missing", "dirty", "tampered", "invalid_commit"):
            with self.subTest(change=change):
                self.write_metadata()
                (self.bundle / "RailVehicleLab.exe").write_bytes(b"fictional executable")
                if change == "missing":
                    (self.bundle / "BUILD-METADATA.json").unlink()
                elif change == "dirty":
                    (self.bundle / "BUILD-METADATA.json").write_text(json.dumps({**self.metadata, "source_clean": False}))
                elif change == "tampered":
                    (self.bundle / "RailVehicleLab.exe").write_bytes(b"changed")
                else:
                    (self.bundle / "BUILD-METADATA.json").write_text(json.dumps({**self.metadata, "source_commit": "unknown"}))
                with self.assertRaises(ValueError):
                    archive_tool.create_archive(self.bundle, self.output)

    def test_rejects_changed_missing_or_added_non_executable_files(self):
        app = self.bundle / "app.py"
        for change in ("changed", "missing", "added"):
            with self.subTest(change=change):
                app.write_bytes(b"fictional app")
                extra = self.bundle / "extra.txt"
                if change == "changed":
                    app.write_bytes(b"changed")
                elif change == "missing":
                    app.unlink()
                else:
                    extra.write_bytes(b"extra")
                with self.assertRaises(ValueError):
                    archive_tool.create_archive(self.bundle, self.output)
                if extra.exists():
                    extra.unlink()

    def test_record_metadata_hashes_all_files_and_wont_overwrite(self):
        (self.bundle / "BUILD-METADATA.json").unlink()
        archive_tool.write_build_metadata(self.bundle, "a" * 40, True)
        actual = json.loads((self.bundle / "BUILD-METADATA.json").read_text())
        self.assertEqual(actual, self.metadata)
        with self.assertRaises(FileExistsError):
            archive_tool.write_build_metadata(self.bundle, "a" * 40, True)

    def test_change_during_archive_is_detected_before_checksum(self):
        original_write = zipfile.ZipFile.write
        def changing_write(zipped, filename, *args, **kwargs):
            if Path(filename).name == "app.py":
                Path(filename).write_bytes(b"changed during archive")
            return original_write(zipped, filename, *args, **kwargs)
        with patch.object(zipfile.ZipFile, "write", new=changing_write):
            with self.assertRaises(ValueError):
                archive_tool.create_archive(self.bundle, self.output)
        self.assertEqual(list(self.output.glob("*.sha256")), [])
        self.assertEqual(list(self.output.glob("*.zip")), [])
        self.assertEqual(list(self.output.glob("*.partial")), [])

    def test_publish_never_replaces_existing_file(self):
        source = self.root / "temporary.partial"
        destination = self.root / "existing.zip"
        source.write_bytes(b"new")
        destination.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            archive_tool.publish_without_overwrite(source, destination)
        self.assertEqual(destination.read_bytes(), b"existing")
        self.assertEqual(source.read_bytes(), b"new")

    def test_write_failure_leaves_no_final_zip_or_checksum(self):
        with patch.object(zipfile.ZipFile, "write", side_effect=OSError("private fixture")):
            with self.assertRaises(OSError):
                archive_tool.create_archive(self.bundle, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_rejects_local_data_and_secrets_without_exposing_content(self):
        for name in ("secrets.toml", ".env.local", "samples.csv", "state.sqlite3", "launcher.log"):
            with self.subTest(name=name):
                path = self.bundle / name
                path.write_text("private fixture")
                try:
                    with self.assertRaises(ValueError) as error:
                        archive_tool.create_archive(self.bundle, self.output)
                    self.assertNotIn("private fixture", str(error.exception))
                finally:
                    path.unlink()

    def test_never_overwrites_existing_package_or_writes_inside_bundle(self):
        archive, _ = archive_tool.create_archive(self.bundle, self.output)
        original = archive.read_bytes()
        with self.assertRaises(FileExistsError):
            archive_tool.create_archive(self.bundle, self.output)
        self.assertEqual(archive.read_bytes(), original)
        with self.assertRaises(ValueError):
            archive_tool.create_archive(self.bundle, self.bundle / "output")

    def test_windows_reparse_point_is_rejected(self):
        path = Mock()
        path.is_symlink.return_value = False
        path.lstat.return_value.st_file_attributes = 0x400
        self.assertTrue(archive_tool.is_link_or_reparse(path))
        nested = self.bundle / "junction"
        nested.mkdir()
        original = archive_tool.is_link_or_reparse
        with patch.object(archive_tool, "is_link_or_reparse", side_effect=lambda item: item == nested or original(item)):
            with self.assertRaises(ValueError):
                archive_tool.create_archive(self.bundle, self.output)

    def test_inaccessible_directory_aborts_instead_of_creating_partial_zip(self):
        def failing_walk(_bundle, *, followlinks, onerror):
            onerror(PermissionError("private fixture"))
            yield
        with patch.object(archive_tool.os, "walk", side_effect=failing_walk):
            with self.assertRaises(PermissionError):
                archive_tool.create_archive(self.bundle, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
