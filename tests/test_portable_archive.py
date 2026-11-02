"""Versioned test archives must preserve provenance and exclude local data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
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
        self.metadata = {"source_commit": "a" * 40, "source_clean": True,
                         "exe_sha256": hashlib.sha256(b"fictional executable").hexdigest()}
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


if __name__ == "__main__":
    unittest.main()
