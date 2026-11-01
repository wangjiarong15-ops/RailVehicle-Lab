"""Directory-mode Windows launcher; user data is never stored in the bundle."""
import argparse
import ctypes
from contextlib import contextmanager
import logging
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import webbrowser


class PortableError(RuntimeError):
    """A fixed, non-sensitive startup message suitable for the user."""


def user_home(environment=None):
    environment = os.environ if environment is None else environment
    base = Path(environment.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "RailVehicle-Lab"


@contextmanager
def instance_lock(home):
    import msvcrt
    home.mkdir(parents=True, exist_ok=True)
    handle = (home / "launcher.lock").open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise PortableError("Another portable instance is already running.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def install_default_config(home, bundle):
    config = home / ".streamlit" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    try:
        if config.exists():
            return
        content = (bundle / "portable_config.toml").read_bytes()
        with config.open("xb") as output:
            output.write(content)
    except FileExistsError:
        pass


def run(demo=False, port=8501):
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port.")
    home = user_home()
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    with instance_lock(home):
        install_default_config(home, bundle)
        logs = home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("rail_portable")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = logging.FileHandler(str(logs / "launcher.log"), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        os.environ["RAIL_PORTABLE_HOME"] = str(home)
        os.environ["RAIL_PUBLIC_DEMO"] = "true" if demo else "false"
        for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "AI_ENABLED", "AI_PROVIDER", "AI_MODEL", "AI_TIMEOUT", "DEEPSEEK_BASE_URL"):
            os.environ.pop(key, None)
        os.chdir(home)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise PortableError(f"Port {port} is occupied; close the other service or use --port 8502.") from exc
        print("Loading bundled Streamlit runtime...", flush=True)
        from streamlit import config
        from streamlit.web import bootstrap
        flags = {"global.developmentMode": False,
                 "server.address": "127.0.0.1", "server.port": port,
                 "server.headless": True, "server.fileWatcherType": "none",
                 "browser.gatherUsageStats": False}
        print("Applying local configuration...", flush=True)
        bootstrap.load_config_options(flags)
        config.set_option("secrets.files", [str(home / ".streamlit" / "secrets.toml")])
        script = bundle / ("demo_app.py" if demo else "app.py")
        if not script.is_file():
            raise PortableError("Application files are missing from the package.")
        address = f"http://127.0.0.1:{port}"
        print(f"Local address: {address}\nUser data: {home}\nPress Ctrl+C to stop.", flush=True)
        logger.info("Starting local portable service")
        def open_when_ready():
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        try:
                            webbrowser.open(address)
                        except Exception:
                            pass
                        return
                except OSError:
                    time.sleep(.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
        bootstrap.run(str(script), False, [], flags)
        logger.info("Portable service stopped")
        logger.removeHandler(handler)
        handler.close()


def show_failure(message):
    print(message, file=sys.stderr, flush=True)
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "RailVehicle-Lab", 0x10)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args(argv)
    try:
        run(args.demo, args.port)
        return 0
    except KeyboardInterrupt:
        return 130
    except PortableError as exc:
        show_failure(str(exc))
        return 1
    except Exception:
        # No raw exception, secret, request or user data in the log or message.
        exc = sys.exc_info()[1]
        kind = type(exc).__name__ if type(exc).__name__ in {"ImportError", "ModuleNotFoundError", "AttributeError", "OSError", "TypeError", "ValueError", "RuntimeError"} else "UnexpectedError"
        module = getattr(exc, "name", None) if kind == "ModuleNotFoundError" else None
        safe_module = module if isinstance(module, str) and module.replace(".", "").replace("_", "").isalnum() else None
        frames = traceback.extract_tb(exc.__traceback__)[-5:]
        safe_frames = [f"{f.name}:{f.lineno}" for f in frames if f.name.replace("_", "").isalnum()]
        if safe_frames:
            print("Startup diagnostic: " + " > ".join(safe_frames), file=sys.stderr, flush=True)
        show_failure(f"Startup failed ({kind}" + (f": {safe_module}" if safe_module else "") + "). Check the local port, package files and user directory permissions.")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
