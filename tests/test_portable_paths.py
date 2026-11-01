"""Portable storage is opt-in and never moves an existing database."""
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
import os
import sys
from rail_vehicle.db import PROJECT_ROOT, data_directory
from rail_vehicle.ai.config import _streamlit_secrets
from portable_launcher import instance_lock, user_home, PortableError, install_default_config, run, main


class PortablePathTests(unittest.TestCase):
    def test_regular_mode_keeps_existing_location(self):
        self.assertEqual(data_directory({}), PROJECT_ROOT / "data")

    def test_portable_mode_uses_user_directory_with_unicode_and_spaces(self):
        path = Path("用户 数据")
        self.assertEqual(data_directory({"RAIL_PORTABLE_HOME": str(path)}), path / "data")

    def test_user_home_has_windows_fallback_and_explicit_root(self):
        self.assertEqual(user_home({"LOCALAPPDATA": "虚构 路径"}), Path("虚构 路径") / "RailVehicle-Lab")
        self.assertEqual(user_home({}), Path.home() / "AppData" / "Local" / "RailVehicle-Lab")

    def test_existing_user_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home, bundle = Path(directory) / "用户 配置", Path(directory) / "bundle"
            bundle.mkdir()
            (bundle / "portable_config.toml").write_text("default", encoding="utf-8")
            install_default_config(home, bundle)
            path = home / ".streamlit" / "config.toml"
            path.write_text("user choice", encoding="utf-8")
            install_default_config(home, bundle)
            self.assertEqual(path.read_text(encoding="utf-8"), "user choice")

    def test_portable_secrets_do_not_use_global_streamlit_secrets(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"RAIL_PORTABLE_HOME": directory}):
            self.assertEqual(_streamlit_secrets(), {})
            path = Path(directory) / ".streamlit" / "secrets.toml"
            path.parent.mkdir()
            path.write_text('AI_ENABLED = true\nAI_MODEL = "fictional-test"\n', encoding="utf-8")
            self.assertEqual(_streamlit_secrets()["AI_MODEL"], "fictional-test")

    def test_safe_startup_errors_never_expose_exception_text(self):
        with patch("portable_launcher.run", side_effect=RuntimeError("private-example-value")), \
             patch("portable_launcher.show_failure") as message:
            self.assertEqual(main([]), 1)
        self.assertNotIn("private-example-value", str(message.call_args))
        with patch("portable_launcher.run", side_effect=PortableError("Port 8501 is occupied.")), \
             patch("portable_launcher.show_failure") as message:
            self.assertEqual(main([]), 1)
        self.assertIn("Port 8501 is occupied", str(message.call_args))

    def test_portable_config_parse_failure_does_not_read_global_secrets(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"RAIL_PORTABLE_HOME": directory}):
            path = Path(directory) / ".streamlit" / "secrets.toml"
            path.parent.mkdir()
            path.write_text("invalid = [", encoding="utf-8")
            with patch("streamlit.secrets", {"AI_MODEL": "must-not-fall-back"}):
                self.assertEqual(_streamlit_secrets(), {})

    @unittest.skipUnless(__import__("sys").platform == "win32", "Windows file locking")
    def test_single_instance_lock_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with instance_lock(home):
                with self.assertRaises(PortableError):
                    with instance_lock(home):
                        pass
            with instance_lock(home):
                pass

    @unittest.skipUnless(sys.platform == "win32", "Windows socket and file locking")
    def test_demo_bootstrap_is_local_and_does_not_log_or_inherit_credentials(self):
        from streamlit import config
        from streamlit.web import bootstrap
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bundle = base / "bundle"
            bundle.mkdir()
            (bundle / "portable_config.toml").write_text("[theme]\nbase='dark'\n", encoding="utf-8")
            (bundle / "demo_app.py").write_text("# fixture", encoding="utf-8")
            observed = {}
            def fake_run(*args, **kwargs):
                observed.update(demo=os.environ.get("RAIL_PUBLIC_DEMO"), key_present="DEEPSEEK_API_KEY" in os.environ)
            old_cwd = Path.cwd()
            try:
                with patch.dict(os.environ, {"LOCALAPPDATA": directory, "DEEPSEEK_API_KEY": "fictional-test-only"}), \
                     patch.object(sys, "_MEIPASS", str(bundle), create=True), \
                     patch.object(bootstrap, "load_config_options"), patch.object(bootstrap, "run", side_effect=fake_run) as boot, \
                     patch.object(config, "set_option") as setting, patch("portable_launcher.threading.Thread"):
                    run(demo=True, port=0xC7F1)
                self.assertEqual(observed, {"demo": "true", "key_present": False})
                self.assertEqual(boot.call_args.args[0], str(bundle / "demo_app.py"))
                flags = boot.call_args.args[3]
                self.assertEqual(flags["server.address"], "127.0.0.1")
                self.assertFalse(flags["global.developmentMode"])
                setting.assert_called_once_with("secrets.files", [str(base / "RailVehicle-Lab" / ".streamlit" / "secrets.toml")])
                log = (base / "RailVehicle-Lab" / "logs" / "launcher.log").read_text(encoding="utf-8")
                self.assertNotIn("fictional-test-only", log)
            finally:
                os.chdir(old_cwd)
