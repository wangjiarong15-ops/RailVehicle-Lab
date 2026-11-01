"""Launcher runs only the requested entry, without automatic installation."""
import subprocess
import unittest
from unittest.mock import Mock, patch
import windows_launcher as launcher


class WindowsLauncherTests(unittest.TestCase):
    def test_check_modes_do_not_start_process_or_open_browser(self):
        for args in (["--check"], ["--demo", "--check"]):
            with self.subTest(args=args), patch.object(launcher, "check_environment", return_value=None), \
                 patch.object(launcher.subprocess, "Popen") as popen, patch.object(launcher.webbrowser, "open") as browser:
                self.assertEqual(launcher.main(args), 0)
                popen.assert_not_called()
                browser.assert_not_called()

    def test_missing_dependencies_and_occupied_port_return_failure(self):
        with patch.object(launcher, "check_environment", return_value="Missing dependencies"):
            self.assertEqual(launcher.main(["--check"]), 1)
        with patch.object(launcher, "check_environment", return_value=None), patch.object(launcher, "port_available", return_value=False), \
             patch.object(launcher.subprocess, "Popen") as popen:
            self.assertEqual(launcher.main([]), 1)
            popen.assert_not_called()

    def test_entry_and_working_directory_and_exit_status(self):
        for args, entry in (([], "app.py"), (["--demo"], "demo_app.py")):
            process = Mock()
            process.poll.return_value = 7
            process.wait.return_value = 7
            with self.subTest(entry=entry), patch.object(launcher, "check_environment", return_value=None), \
                 patch.object(launcher, "port_available", return_value=True), patch.object(launcher.subprocess, "Popen", return_value=process) as popen:
                self.assertEqual(launcher.main(args), 7)
                self.assertEqual(popen.call_args.kwargs["cwd"], str(launcher.ROOT))
                self.assertIn(str(launcher.ROOT / entry), popen.call_args.args[0])
                self.assertIn("127.0.0.1", popen.call_args.args[0])

    def test_interrupt_terminates_owned_process(self):
        process = Mock()
        process.poll.side_effect = KeyboardInterrupt
        with patch.object(launcher, "check_environment", return_value=None), patch.object(launcher, "port_available", return_value=True), \
             patch.object(launcher.subprocess, "Popen", return_value=process):
            self.assertEqual(launcher.main([]), 130)
            process.terminate.assert_called_once()
            process.wait.assert_called_once()

    def test_missing_module_is_reported(self):
        original = launcher.importlib.import_module
        def fake(name, *args, **kwargs):
            if name == "scipy":
                raise ImportError("fixture")
            return original(name, *args, **kwargs)
        with patch.object(launcher.importlib, "import_module", side_effect=fake):
            self.assertIn("scipy", launcher.check_environment())
