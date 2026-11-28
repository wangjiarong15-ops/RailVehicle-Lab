# RailVehicle-Lab V1.0 RC1 Windows x64

此候选版面向本机离线工程研究和独立设备公测，采用目录式 ZIP。完整解压后启动 `RailVehicle-Lab.exe`。在命令行运行 `RailVehicle-Lab.exe --check` 可检查入口、资源和公开轮轨基准。Windows 数据与日志位置见 [公测清单](windows-beta-checklist.md)。

## 五分钟演示

1. 打开内置虚构批次，查看速度及加速度曲线和逐轴异常时间。
2. 从事件详情跳转三维回放，再在高级轮轨动力学选择曲线工况。
3. 打开工程项目，建立测试项目。可上传自己的授权 TXT 或使用无资料的本地研究。
4. 从动力学中打开工程研究，输入「当前工况独立复算」，审批后查看 DOP853、RK45 与数值容差。
5. 导出 PDF 或 JSON 研究证据。在无云端账号时使用「本地工程分析」。

## 构建与校验

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build_portable.ps1 -Rc -OutputRoot tmp/rc1-build
.venv/Scripts/python.exe -X utf8 packaging/package_portable.py tmp/rc1-build/RailVehicle-Lab-Beta dist/Evolution-X
Get-FileHash -Algorithm SHA256 dist/Evolution-X/RailVehicle-Lab-V1.0-RC1-Windows-x64.zip
```

检查实际压缩包与同名 `.sha256` 文件。每个构建记录源码提交及运行文件哈希，校验码用于文件完整性。试用前保存 IX 的压缩包和用户目录副本，可把旧数据库复制到独立位置核对兼容性。

## 数据备份与恢复核对

源码环境可在应用运行时创建一致的 SQLite 备份。恢复时先写入另一条未使用的路径，检查项目、原始数据和报告引用。

```powershell
.venv/Scripts/python.exe scripts/backup_engineering_database.py C:/path/to/rail_vehicle.db C:/backup/project-copy.sqlite
.venv/Scripts/python.exe scripts/backup_engineering_database.py C:/backup/project-copy.sqlite C:/restore/verified-copy.sqlite
```

免安装环境请先退出应用，再复制整个 RailVehicle-Lab 用户目录。若需测试历史版本，从副本启动测试。外部试用结果应登记系统、GPU、浏览器、ZIP 校验值、重现步骤和虚构数据截图。

## 工程和部署范围

- 本机模拟采集和已登记文件可离线测试。真实设备及独立无 Python Windows 仍需实机验收。
- 31 自由度模型采用教学悬挂和接触假设。公开数值、合成案例、独立积分器和软件回归分别记录。完整 CONTACT 载荷范围、真实台架与实车仍待验证。
- 云端能力取决于已配置的服务和用户授权。无密钥工程流程在本机执行。
- 核心运行组件包括 Python、Streamlit、NumPy、SciPy、Plotly、Three.js、OpenAI Python SDK、ReportLab、pypdf、websockets 和 PyInstaller。随包构建清单记录实际文件，全量许可需按运行分发件逐项核对。
