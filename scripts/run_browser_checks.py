"""Run the browser regression against an isolated, fictional RailVehicle-Lab instance."""

from argparse import ArgumentParser
from contextlib import closing, contextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory, mkdtemp
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from rail_vehicle.db import init_db
from rail_vehicle.demo_data import DEMO_FILE_NAMES, DEMO_SAMPLE_COUNT, DEMO_VEHICLE_CODE, demo_csv_content
from rail_vehicle.run_data import import_run_data, validate_run_csv
from rail_vehicle.vehicle_data import add_vehicle, list_vehicles


ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "copilot": "check_copilot_browser.cjs",
    "copilot-stream": "check_copilot_stream_browser.cjs",
    "model": "check_bogie_browser.cjs",
    "evolution": "check_evolution_browser.cjs",
    "evolution-ii": "check_evolution_ii_browser.cjs",
    "evolution-iii": "check_evolution_iii_browser.cjs",
    "evolution-iv": "check_evolution_iv_browser.cjs",
    "evolution-v": "check_evolution_v_browser.cjs",
    "evolution-vi": "check_evolution_vi_browser.cjs",
    "evolution-vii": "check_evolution_vii_browser.cjs",
    "evolution-viii": "check_evolution_viii_browser.cjs",
    "evolution-ix": "check_evolution_ix_browser.cjs",
    "evolution-ix-cloud": "check_evolution_ix_browser.cjs",
    "evolution-x": "check_evolution_x_browser.cjs",
    "resume": "check_workspace_resume_browser.cjs",
    "frontend": "check_frontend_product_browser.cjs",
    "interface": "check_interface_browser.cjs",
    "sensors": "check_sensor_browser.cjs",
    "workbench-trust": "check_workbench_trust_browser.cjs",
}


def seed_database(path: Path) -> None:
    if path.exists():
        raise FileExistsError("浏览器测试只允许创建新数据库")
    init_db(path)
    add_vehicle({
        "vehicle_code": DEMO_VEHICLE_CODE, "vehicle_type": "虚构演示车型（非实车）",
        "vehicle_length_m": 20, "mass_kg": 40000, "axle_count": 4,
        "bogie_count": 2, "max_speed_kmh": 160, "notes": "仅供浏览器回归的虚构数据。",
    }, path)
    vehicles = list_vehicles(path)
    for index, name in enumerate(DEMO_FILE_NAMES):
        validation = validate_run_csv(demo_csv_content(index), vehicles)
        if not validation.is_valid or validation.source_row_count != DEMO_SAMPLE_COUNT:
            raise ValueError(f"虚构批次未通过校验：{name}")
        import_run_data(name, validation, path)


def isolated_env(home: Path) -> dict[str, str]:
    # Filter names before reading values; never load API credentials into the test processes.
    env = {key: os.environ[key] for key in os.environ
           if not key.upper().startswith(("AI_", "OPENAI_", "DEEPSEEK_", "STREAMLIT_"))}
    env.update(RAIL_PORTABLE_HOME=str(home), RAIL_PUBLIC_DEMO="false", AI_ENABLED="false",
               PYTHONIOENCODING="utf-8", RAIL_SOFTWARE_WEBGL="1")
    return env


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_until_healthy(process: subprocess.Popen, address: str, timeout: int = 45) -> None:
    opener = build_opener(ProxyHandler({}))
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError(f"Streamlit 提前退出（退出码 {process.returncode}）")
        try:
            with opener.open(f"{address}/_stcore/health", timeout=2) as response:
                if response.status == 200 and response.read(8) == b"ok":
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.2)
    raise TimeoutError("等待 Streamlit 本机健康检查超时")


def stop_server(server: subprocess.Popen) -> None:
    if server.poll() is not None:
        return
    if os.name == "nt":
        # The Windows venv executable launches another Python process that owns the port and cwd.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run([str(taskkill), "/PID", str(server.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        server.terminate()
    try:
        server.wait(timeout=8)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


def run_suite(node: str, suite: str, address: str, output: Path, env: dict[str, str]) -> bool:
    log_path = output / f"{suite}.log"
    try:
        with log_path.open("wb") as log:
            result = subprocess.run([node, str(ROOT / "scripts" / SUITES[suite]), address, str(output / suite)],
                                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    except subprocess.TimeoutExpired:
        print(f"{suite} 浏览器检查超时：{log_path}", file=sys.stderr)
        return False
    print(log_path.read_text(encoding="utf-8", errors="replace"), end="", flush=True)
    return result.returncode == 0


@contextmanager
def local_app(home, env, log_path):
    port = free_port()
    address = f"http://127.0.0.1:{port}"
    with log_path.open("wb") as log, subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
        "--server.address", "127.0.0.1", "--server.port", str(port),
        "--server.headless", "true", "--server.fileWatcherType", "none",
        "--browser.gatherUsageStats", "false",
    ], cwd=home, env=env, stdout=log, stderr=subprocess.STDOUT) as server:
        try:
            wait_until_healthy(server, address)
            yield address
        finally:
            stop_server(server)


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "browser-qa")
    parser.add_argument("--suite", choices=("all", *SUITES), default="all")
    args = parser.parse_args()
    if not (node := shutil.which("node")):
        parser.error("Node.js 不在 PATH 中")
    args.output.mkdir(parents=True, exist_ok=True)
    output = Path(mkdtemp(prefix="run-", dir=args.output.resolve()))
    print(f"浏览器验收输出：{output}", flush=True)

    try:
        with TemporaryDirectory(prefix="rail-browser-") as folder:
            home = Path(folder)
            seed_database(home / "data" / "rail_vehicle.db")
            config = home / ".streamlit" / "config.toml"
            config.parent.mkdir()
            # Preserve the application theme without loading user or repository secrets.
            theme = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
            config.write_text(theme + '\n[secrets]\nfiles = [".streamlit/secrets.toml"]\n', encoding="utf-8")
            env = isolated_env(home)
            names = SUITES if args.suite == "all" else (args.suite,)
            results = []
            if "workbench-trust" in names:
                # Check recovery before other workflows import or analyze additional batches.
                with local_app(home, env, output / "streamlit-workbench.log") as address:
                    results.append(run_suite(node, "workbench-trust", address, output, env))
                if results[-1]:
                    restart_output = output / "restart"
                    restart_output.mkdir()
                    with local_app(home, env, restart_output / "streamlit.log") as address:
                        results.append(run_suite(node, "workbench-trust", address, restart_output,
                                                 dict(env, RAIL_WORKBENCH_RESTART="1")))
            offline = [name for name in names if name not in ("copilot-stream", "workbench-trust", "evolution-ix-cloud")]
            if offline:
                with local_app(home,env,output/"streamlit.log") as address:
                    results.extend(run_suite(node,name,address,output,env) for name in offline)
            if "copilot-stream" in names or "evolution-ix-cloud" in names:
                if __package__:
                    from .copilot_mock_service import simulated_provider
                else:
                    from copilot_mock_service import simulated_provider
                for suite, expected in (("copilot-stream",["plan","plan","interpretation","plan","interpretation"]),
                                        ("evolution-ix-cloud",["initial","followup"])):
                    if suite not in names:
                        continue
                    research=suite=="evolution-ix-cloud"
                    with simulated_provider(research=research) as (url,requests):
                        mock_env = dict(env,AI_ENABLED="true",AI_PROVIDER="deepseek",AI_MODEL="local-browser-fixture",
                                        DEEPSEEK_BASE_URL=url,DEEPSEEK_API_KEY="local-test-only",NO_PROXY="127.0.0.1")
                        with local_app(home,mock_env,output/f"streamlit-{suite}-mock.log") as address:
                            results.append(run_suite(node,suite,address,output,dict(env,RAIL_IX_MOCK="1" if research else "0")))
                        results.append(requests==expected)
            return 0 if all(results) else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"无法运行浏览器回归：{exc}（日志目录：{output}）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
