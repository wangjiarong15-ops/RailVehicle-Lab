# Phase 9 发布候选与 RC1 审计

2026-11-25 在 `D:\RailVehicle-Lab` 实际核对。远程 fetch 后，`codex/phase8-engineering-validation` 与 GitHub 均在 `b0fb5f3b`，比 `origin/main` 多 110 个提交，比 `origin/codex/project-evolution` 多四个。后两条分支均无 Phase 8 未包含的提交。其余远程分支中，独有旧路线分支 `origin/codex/roadmap-motion-replay-20261102` 含文档提交 `5a7dc93b`。已有未跟踪目录、Qoder 描述符和构建物留在工作区。开发从 `b0fb5f3b` 开始，使用 `codex/phase9-engineering-depth`。

## 比赛与两端边界

| 审计项 | 处理 |
| --- | --- |
| 可恢复基线 | 本地 `rc1` 对应 `95ccd2a02693550e8a9ad75b4ebe0f730410d675`。版本清单按 Git 对象对比冻结网页及全部比赛文档，避免工作树换行转换改变摘要。 |
| 两端功能 | 共享 CSV、异常阈值、时间与装配契约。在线端为教学展示和运行回放。SQLite、工程标定、数值仿真、报告和 Copilot 属于 Python 完整版。 |
| 材料测试数字 | 比赛 README、贡献说明、创新点、清单与 RC1 审计中提到的 58/58 端到端步骤属于历史快照。当前完整端重新得到 700 项、324 个子测试通过，浏览器以各版本本轮日志为准。评委问答引用公网 `/tests/index.html`，使用前需实际访问并核对。 |
| 参赛说法 | 作品帖、解说词明确标注教学数据和运动示意。老架构表中的“实车运动重建 · 研究性”仅描述研究方向，演示材料应使用“实测 CSV 回放”。机构、身份与提交仍由项目所有者确认。 |
| 部署版本 | 文件哈希清单代表提交中的源资源。只有托管方清单、部署记录和线上逐项校验才能把公开地址绑定到同一构建。官方入口此次返回通用赛页内容，可在报名账户内核实当前规则。 |

[公开 RC1](https://railvehicle-bogie-d1j7uxpvtu6.qoder.zone/) · [天池赛题](https://s.tianchi.aliyun.com/competition/entrance/532536/introduction)

## 发布准备流程

1. 在独立分支记录候选 Git 提交，运行 `python -m scripts.phase9_release_manifest --revision HEAD --output tmp/phase9-source-manifest.json`。输出候选标识、完整端与冻结端的 commit、文件大小及 SHA-256。输出文件需使用新路径。
2. 运行全量 Python、Node 双端契约、GLB 装配、Edge、依赖公告和敏感路径检查。比赛源码和材料用 `git diff --exit-code rc1..HEAD -- qoder-sites-demo docs/qoder-competition` 核对。
3. 用现有 PyInstaller 脚本在隔离目录构建，并保留其清单和提交号。独立 Windows 设备需验证启动、退出、文件路径与数据保存。真实车辆验证使用独立仪器与有授权的试验记录。
4. 待这些条件满足后，可用 Phase 9 分支发起指向 `main` 的评审 PR，随附构建清单、测试记录和风险。比赛部署、冻结 ZIP、比赛提交作为独立交付。
