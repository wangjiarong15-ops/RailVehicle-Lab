"""Windows launcher: check first, then run Streamlit on loopback only."""
import argparse
import importlib
import inspect
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser

ROOT = Path(__file__).resolve().parent
DEPENDENCIES = ("streamlit", "plotly", "reportlab", "jsonschema", "openai", "numpy", "scipy", "sklearn")


def check_environment(root=ROOT):
    if sys.version_info < (3, 10):
        return "Python 3.10 or later is required."
    missing_files = [name for name in ("app.py", "demo_app.py") if not (root / name).is_file()]
    if missing_files:
        return "Application entry missing: " + ", ".join(missing_files)
    missing = []
    for name in DEPENDENCIES:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    if missing:
        return "Missing or broken dependencies: " + ", ".join(missing) + ". Run setup_windows.bat."
    if "unsafe_allow_javascript" not in inspect.signature(importlib.import_module("streamlit").html).parameters:
        return "Streamlit is too old for the theme interface. Upgrade Streamlit in .venv."
    return None


def port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sys.platform == "win32":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        print("Invalid port; choose 1-65535.", file=sys.stderr)
        return 2
    error = check_environment()
    if error:
        print(error, file=sys.stderr)
        return 1
    entry = "demo_app.py" if args.demo else "app.py"
    print(f"Environment OK. Mode: {'demo' if args.demo else 'full'}; entry: {entry}", flush=True)
    if args.check:
        return 0
    if not port_available(args.port):
        print(f"Port {args.port} is occupied. Stop the other service or use --port 8502.", file=sys.stderr)
        return 1
    address = f"http://127.0.0.1:{args.port}"
    print(f"Open {address} in your browser. Press Ctrl+C to stop.", flush=True)
    command = [sys.executable, "-m", "streamlit", "run", str(ROOT / entry), "--server.address", "127.0.0.1", "--server.port", str(args.port), "--server.headless", "true"]
    try:
        # Affect only the child process, not the calling process's environment.
        child_env = os.environ.copy()
        child_env["RAIL_PUBLIC_DEMO"] = "true" if args.demo else "false"
        process = subprocess.Popen(command, cwd=str(ROOT), env=child_env)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", args.port), timeout=0.3):
                    try:
                        webbrowser.open(address)
                    except webbrowser.Error:
                        print("Browser could not be opened; use the address above.")
                    break
            except OSError:
                time.sleep(0.2)
        return process.wait()
    except KeyboardInterrupt:
        if 'process' in locals():
            process.terminate()
            process.wait()
        return 130
    except OSError:
        print("Could not start Streamlit. Check the environment and permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
