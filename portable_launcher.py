"""Directory-mode Windows launcher; user data is never stored in the bundle."""
import argparse
import ctypes
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import threading
import traceback
import webbrowser


class PortableError(RuntimeError):
    """A fixed, non-sensitive startup message suitable for the user."""


DEFAULT_PORTS = range(8501, 8511)
HOST = "127.0.0.1"


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
            raise PortableError("公测版已在运行，请返回原窗口或先退出原程序。") from exc
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


def select_port(requested=None):
    if requested is not None and not 1 <= requested <= 65535:
        raise PortableError("端口无效，请输入 1 到 65535 之间的整数。")
    for port in DEFAULT_PORTS if requested is None else (requested,):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((HOST, port))
            except OSError:
                continue
            return port
    if requested is not None:
        raise PortableError(f"端口 {requested} 无法使用。请关闭占用程序，或直接双击以自动选择端口。")
    raise PortableError("本机端口 8501–8510 均不可用。请关闭占用程序，或用 --port 指定其他端口。")


def open_when_ready(port, address, stopping):
    for _ in range(300):
        if stopping.is_set():
            return
        connection = HTTPConnection(HOST, port, timeout=.4)
        try:
            connection.request("GET", "/_stcore/health")
            response = connection.getresponse()
            ready = response.status == 200 and response.read(16).strip() == b"ok"
        except (OSError, HTTPException):
            ready = False
        finally:
            connection.close()
        if ready and not stopping.is_set():
            try:
                if webbrowser.open(address):
                    return
            except (OSError, webbrowser.Error):
                pass
            print(f"未能自动打开浏览器，请手动访问 {address}。", flush=True)
            return
        stopping.wait(.2)
    print(f"自动打开浏览器超时；服务就绪后请手动访问 {address}。", flush=True)


def run(demo=False, port=None, *, open_browser=True):
    home = user_home()
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    with instance_lock(home):
        port = select_port(port)
        install_default_config(home, bundle)
        logs = home / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("rail_portable")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(logs / "launcher.log", maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        stopping = threading.Event()
        browser_thread = None
        try:
            os.environ["RAIL_PORTABLE_HOME"] = str(home)
            os.environ["RAIL_PUBLIC_DEMO"] = "true" if demo else "false"
            for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "AI_ENABLED", "AI_PROVIDER", "AI_MODEL", "AI_TIMEOUT", "DEEPSEEK_BASE_URL"):
                os.environ.pop(key, None)
            os.chdir(home)
            script = bundle / ("demo_app.py" if demo else "app.py")
            if not script.is_file():
                raise PortableError("程序文件不完整。请重新解压整个 ZIP，勿单独移动 EXE。")
            print("正在加载本地运行环境…", flush=True)
            from streamlit import config
            from streamlit.web import bootstrap
            flags = {"global.developmentMode": False,
                     "server.address": HOST, "server.port": port,
                     "server.headless": True, "server.fileWatcherType": "none",
                     "browser.gatherUsageStats": False}
            bootstrap.load_config_options(flags)
            config.set_option("secrets.files", [str(home / ".streamlit" / "secrets.toml")])
            address = f"http://{HOST}:{port}"
            print(f"本机页面：{address}\n数据目录：{home}\n使用期间请保留本窗口；退出请在本窗口按 Ctrl+C。", flush=True)
            logger.info("启动便携服务，端口 %s", port)
            if open_browser:
                browser_thread = threading.Thread(target=open_when_ready, args=(port, address, stopping), daemon=True)
                browser_thread.start()
            bootstrap.run(str(script), False, [], flags)
        finally:
            stopping.set()
            if browser_thread is not None:
                browser_thread.join(timeout=1.)
            logger.info("便携启动器关闭")
            logger.removeHandler(handler)
            handler.close()


def show_failure(message, *, dialog=True):
    print(message, file=sys.stderr, flush=True)
    if dialog and getattr(sys, "frozen", False) and sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "RailVehicle-Lab", 0x10)


def check_bundle():
    """Check the packaged imports and critical resources without starting a service."""
    from rail_vehicle.pages import copilot, research_agent, twin, validation_lab, workbench_research
    from rail_vehicle.contact_calibration import load_reference
    from rail_vehicle.ui import TWIN

    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    required = ("app.py", "rail_vehicle/assets/bogie_viewer/bogie.glb",
                "rail_vehicle/assets/bogie_viewer/viewer.js", "rail_vehicle/assets/bogie_viewer/vehicle-details.js")
    if not all((bundle / path).is_file() for path in required):
        raise PortableError("缺少运行入口或三维资源。请重新完整解压。")
    if not TWIN or not all(callable(page.render) for page in (copilot,research_agent,twin,validation_lab,workbench_research)):
        raise PortableError("当前页面模块版本不匹配。请重新完整解压。")
    reference = load_reference()
    if len(reference["manchester"])!=42 or len(reference["module3"])!=2:
        raise PortableError("公开轮轨基准数据不完整。请重新完整解压。")
    print("程序检查通过：页面模块、GLB、JavaScript 和轮轨参考数据。实际界面需启动后在浏览器确认。", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="RailVehicle-Lab Windows 便携公测版")
    parser.add_argument("--demo", action="store_true", help="启动仅含虚构数据的演示版")
    parser.add_argument("--port", type=int, help="固定本机端口；默认自动选择 8501–8510")
    parser.add_argument("--no-browser", action="store_true", help="只启动本机服务，不打开浏览器或弹窗")
    parser.add_argument("--check", action="store_true", help="检查模块与静态资源，不启动服务")
    args = parser.parse_args(argv)
    try:
        if args.check:
            check_bundle()
        else:
            run(args.demo, args.port, open_browser=not args.no_browser)
        return 0
    except KeyboardInterrupt:
        return 130
    except PortableError as exc:
        show_failure(str(exc), dialog=not (args.no_browser or args.check))
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
            print("启动诊断：" + " > ".join(safe_frames), file=sys.stderr, flush=True)
        show_failure(f"启动失败（{kind}" + (f": {safe_module}" if safe_module else "") +
                     "）。请检查程序是否完整解压，以及本机数据目录是否可写。",
                     dialog=not (args.no_browser or args.check))
        return 1


if __name__ == "__main__":
    # Frozen builds can ignore PYTHONUTF8. Keep Chinese startup text usable
    # when Windows redirects the console to a file or automation pipe.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace")
    multiprocessing.freeze_support()
    sys.exit(main())
