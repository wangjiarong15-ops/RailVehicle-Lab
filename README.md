# 轨道车辆运行与动力学智能分析平台

本仓库是“轨道车辆运行与动力学智能分析平台”的 MVP。当前已支持车辆基础参数新增、查看和编辑，运行数据 CSV 校验、预览、SQLite 导入和批次历史查询，基础动力学统计分析，以及可解释的阈值异常识别。

## 环境要求

- Python 3.10 或更高版本
- Windows、macOS 或 Linux

## 本地启动

在项目根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m streamlit run app.py
```

浏览器打开 Streamlit 输出的本地地址（通常是 `http://localhost:8501`）。如果 PowerShell 阻止激活虚拟环境，可不激活，直接执行 `.venv\Scripts\python.exe -m pip install -e .` 和 `.venv\Scripts\python.exe -m streamlit run app.py`。

如果 PowerShell 提示找不到 `python`，但本机已安装 Python，请在创建虚拟环境时使用解释器完整路径。例如 Python 3.13 的常见用户安装位置为：

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv
```

如果安装位置或版本不同，请将路径替换为实际的 `python.exe` 位置。

项目配置将 Streamlit 绑定到 `127.0.0.1`，默认只接受本机连接；不要通过移除此配置或改用 `0.0.0.0` 把未经身份验证的开发版直接暴露到局域网/公网。

macOS/Linux 使用以下命令：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m streamlit run app.py
```

## 数据目录

首次启动时会自动创建 `data/rail_vehicle.db` 并初始化 SQLite 表。数据库文件是本机运行数据，不需要手动创建。

车辆记录包含车辆编号、车型、车辆长度（米）、车辆质量（千克）、轴数、转向架数量、最高运行速度（千米/小时）和备注。车辆编号必须唯一；车辆类型和编号不能为空；长度、质量、轴数、转向架数量和最高速度必须为正值。既有骨架版本的 SQLite 数据库会在启动时自动补充新字段，原有车辆记录会保留。

## 运行数据 CSV

在“运行分析”页面上传 UTF-8 CSV（支持 BOM）。表头必须包含以下字段，字段顺序不限；允许附带额外列：

```csv
timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel
2026-10-08T09:30:00,RV-001,80,0.12,-0.04
```

`timestamp` 使用 ISO 8601 日期时间格式；`vehicle_id` 必须与“车辆参数”页已有车辆编号一致。速度须在 0–600 km/h，加速度须在 -100–100 m/s²，数值不能缺失且必须为有限数字。页面会显示格式检查结果、前 20 行预览和行数/车辆数/时间范围统计。只要文件中有一行校验失败，整份文件都不会入库。通过校验后，运行记录和对应的导入批次在同一 SQLite 事务中保存；历史导入记录显示文件名、行数和导入时间。

## 基础动力学统计分析

在“运行分析”页面切换到“基础动力学统计分析”，选择车辆和该车辆的历史导入批次，并可按 UTC 开始/结束时间筛选（默认覆盖整个批次）。速度统计、加速度统计、异常检测和三张曲线都使用筛选范围内的数据。页面显示速度最大值、最小值和平均值；横向/垂向加速度最大值、最小值、平均值及 RMS。加速度绝对峰值同时显示对应的有符号读数和时间。

加速度阈值可在页面调整，默认值为 1.0 m/s²。数据点按 `|加速度| > 阈值` 统计；页面分别展示横向、垂向超过点数量/占比，以及任一方向超过阈值的去重样本行数量/占比。平均值按算术平均计算，RMS 按 `sqrt(Σx² / n)` 计算；极值和 RMS 使用当前所选批次内的全部样本。单位在指标和图表中标明。以上内容仅为描述性的基础动力学统计分析，不表示法规合规或安全结论。

同一可配置阈值也用于“数据异常检测”：横向或垂向加速度绝对值严格大于阈值时，分别标记为对应方向的数据异常点；同一方向相邻的异常样本合并为一个统计异常区段，遇到正常样本后重新开始。事件列表包含异常类型、起止时间、两者之间的持续时间、区段内最大绝对值（同时显示有符号值）和异常样本数。横向/垂向曲线用红色菱形标出异常点，并显示阈值线。单点区段持续时间为 0 秒。该规则只描述数据/统计异常，不判断车辆故障或安全事故。

单批次分析支持生成 PDF 分析报告，报告按当前车辆、批次、时间范围和阈值整理统计指标、异常事件与三张筛选后曲线。PDF 优先使用本机已安装的中文字体（Windows 默认字体目录中的微软雅黑、黑体或宋体），字体会嵌入文件；缺少系统字体时使用 ReportLab 内置 CJK CID 字体方案。运行报告功能需要安装项目依赖中的 ReportLab。

“批次对比”页可选择同一车辆的两个或多个历史批次，对比各批次的数据点数量、速度统计、加速度 RMS 和绝对峰值；三张曲线使用相对运行时间作为横轴，各批次从首条样本的 0 秒开始。

## V0.3 AI Provider 配置（可选）

当前分析页和批次对比页提供用户主动触发的 AI 辅助解读。支持 OpenAI Responses API 和 DeepSeek Responses API；AI 默认关闭，不配置密钥也可照常使用车辆管理、导入、统计、异常检测、批次对比和 PDF 功能。启用后，Provider 只接收 AI 输入组装器生成的统计上下文，不读取或发送原始 CSV、PDF、车辆编号、文件名和备注。模型结果先通过本地输出 Schema、evidence ID、有限数值和安全措辞校验。

通用环境变量为 `AI_PROVIDER`、`AI_MODEL`、`AI_ENABLED`（默认 `false`）和 `AI_TIMEOUT`（秒，默认 30，允许 1–300）。OpenAI Provider 使用 `OPENAI_API_KEY`；DeepSeek Provider 使用 `DEEPSEEK_API_KEY`，API 地址默认为 `https://api.deepseek.com`，可选用 `DEEPSEEK_BASE_URL` 覆盖。选择 DeepSeek 且没有设置 `AI_MODEL` 时默认使用 `deepseek-flash`；OpenAI 仍需明确设置模型名称。例如使用 DeepSeek 的 PowerShell 配置：

```powershell
$env:AI_PROVIDER = "deepseek"
$env:AI_MODEL = "deepseek-flash"
$env:AI_ENABLED = "true"
$env:AI_TIMEOUT = "30"
$env:DEEPSEEK_API_KEY = "本机 DeepSeek API Key"
# 可选：默认值为 https://api.deepseek.com
# $env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
```

也可将密钥放在本机 `.streamlit/secrets.toml`：OpenAI 使用顶层 `OPENAI_API_KEY` 或 `[ai]` 下的 `api_key`；DeepSeek 使用顶层 `DEEPSEEK_API_KEY` 或 `[deepseek]` 下的 `api_key`。DeepSeek 地址也可在 `[deepseek]` 下设置 `base_url`。该文件已加入 `.gitignore`；不要把密钥写进源码、README、日志或提交到 Git。代码不会记录密钥；调用超时、网络、认证、限流及无效输出会返回通用的 AI 不可用状态，不会影响本地分析流程。两种 Provider 都使用有限次 SDK 重试和响应输出 token 上限。DeepSeek Responses API 不接受 `store` 参数，但返回的响应始终标记为不存储；Provider 因此不发送该参数。

## 运行测试

使用 Python 标准库的 `unittest` 运行车辆管理、数据库迁移、CSV 校验/导入、动力学指标和异常检测测试：

```powershell
python -m unittest discover -s tests -v
```

也可以使用虚拟环境解释器运行：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 目录结构

```text
app.py                         Streamlit 应用入口与页面导航
rail_vehicle/db.py             SQLite 路径、连接和表结构初始化
rail_vehicle/pages/vehicles.py 车辆参数新增、编辑和列表页面
rail_vehicle/vehicle_data.py    车辆数据校验和 SQLite 操作
rail_vehicle/pages/run_analysis.py 运行 CSV 上传、预览和导入历史页面
rail_vehicle/run_data.py       CSV 校验、SQLite 导入和批次历史操作
rail_vehicle/dynamics.py       基础动力学指标和阈值统计计算
rail_vehicle/anomaly.py        加速度阈值异常点和区段识别
rail_vehicle/time_filter.py    按 UTC 时间范围筛选运行样本
rail_vehicle/batch_comparison.py 多批次指标和相对时间曲线数据计算
rail_vehicle/analysis_report.py 分析报告数据组装和 PDF 生成
rail_vehicle/ai/                 AI 输入输出契约、上下文组装和 Provider
tests/test_vehicle_data.py     车辆校验和数据库测试
tests/test_run_data.py         运行 CSV 校验和导入测试
tests/test_dynamics.py         基础动力学统计指标测试
tests/test_anomaly.py          异常点和异常区段检测测试
tests/test_time_filter.py      运行数据时间范围筛选测试
tests/test_batch_comparison.py 多批次比较测试
tests/test_analysis_report.py  分析报告数据和 PDF 生成测试
pyproject.toml                 项目元数据和依赖
```
