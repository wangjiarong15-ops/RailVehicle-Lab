# PyInstaller directory build. Bundle only source and known configuration files.
from pathlib import Path
import subprocess
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas = [(str(root / "app.py"), "."), (str(root / "demo_app.py"), "."),
         (str(root / "packaging" / "portable_config.toml"), ".")]
tracked = subprocess.check_output(["git", "ls-files", "--", "rail_vehicle/*.py"], cwd=str(root), text=True, encoding="utf-8").splitlines()
if not tracked:
    raise RuntimeError("Build requires the Git-tracked application sources.")
hidden = []
for name in tracked:
    path = root / name
    if path.suffix != ".py" or not path.is_file():
        continue
    datas.append((str(path), str(path.parent.relative_to(root))))
    parts = list(Path(name).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    hidden.append(".".join(parts))
binaries = []
for package in ("streamlit", "plotly", "reportlab", "sklearn", "scipy", "numpy", "jsonschema", "jsonschema_specifications", "referencing", "openai", "pydeck"):
    package_data, package_binaries, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hidden += package_hidden
# Third-party test fixtures and CSV files are not application resources.
def include_data(item):
    path = Path(item[0])
    parts = {part.lower() for part in path.parts}
    return not ({"tests", "test", "__pycache__"} & parts) and path.suffix.lower() not in {".csv", ".db", ".sqlite", ".sqlite3", ".pyc"} and path.name.lower() not in {"secrets.toml", ".env"}
datas = [item for item in datas if include_data(item)]
hidden = [name for name in hidden if ".tests" not in name]
a = Analysis([str(root / "portable_launcher.py")], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hidden, excludes=["pytest", "IPython", "tkinter"])
# Hooks may add optional sklearn sample CSVs after our initial data filtering.
# Exclude them from the distributable as well; runtime analysis uses user input.
a.datas = [entry for entry in a.datas if Path(entry[0]).suffix.lower() not in {".csv", ".db", ".sqlite", ".sqlite3"} and Path(entry[0]).name.lower() not in {"secrets.toml", ".env"}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RailVehicleLab", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="RailVehicleLab")
