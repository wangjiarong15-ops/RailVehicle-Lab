"""Traceable scenarios, observable assertions, and physical validation boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    inputs: str
    expected: str
    test: str
    validation: str


SCENARIOS = (
    Scenario("波形异常定位", "重复时间与符号变化的合成时程", "正确保留异常样点和事件原始行",
             "test_workbench_trust.test_exact_marker_rows_with_duplicate_times_sign_changes_and_gap", "合成数据"),
    Scenario("数据窗口归档", "两批次、不同筛选窗口", "重新连接后保持批次隔离与原始范围",
             "test_workbench_trust.test_history_survives_new_connection_with_exact_scope_and_batch_isolation", "软件回归"),
    Scenario("曲线整车复算", "17 m/s、610 m 曲线、31 自由度", "DOP853 和 RK45 按相同工况通过数值容差",
             "test_evolution_x.test_active_case_is_really_recomputed_with_independent_integrator", "独立数值积分"),
    Scenario("轮轨载荷对照", "公开固定间隙基准和分组留出", "物理量匹配，留出组不进入训练",
             "test_evolution_viii.test_geometry_uses_matching_physical_quantities_and_disjoint_groups", "公开数值资料"),
    Scenario("悬挂参数辨识", "独立合成记录与噪声", "匹配时频指标，量化独立验证误差",
             "test_evolution_viii.test_joint_spectral_fit_on_distinct_noisy_observation", "合成数据"),
    Scenario("试验优化与双视窗", "受约束的悬挂候选", "可恢复步骤，生成同时间网格的对照",
             "test_evolution_viii.test_real_research_recovers_saved_steps_and_builds_synchronized_comparison", "数值与软件"),
    Scenario("轮轨轨下响应", "小扰动轨下等效模型", "时间积分与矩阵指数独立结果匹配",
             "test_evolution_vi.test_flexible_track_matches_matrix_exponential_in_small_response", "数学对照"),
    Scenario("采集断线重连", "声明的本机虚构采集桥", "按原始索引重连，不重复或错拼",
             "test_evolution_vi.test_local_websocket_resumes_in_order_without_duplicating_packets", "软件回归"),
    Scenario("采集存储重启", "含重复与缺失序号的合成记录", "摘要可验，重启延续，旧游标拒绝覆盖",
             "test_evolution_x.test_acquisition_checkpoints_restart_in_order_and_reject_other_writers", "软件回归"),
    Scenario("工程智能体复算", "项目资料版本 v1 与随后上传的 v2", "原任务与复算均引用 v1，计算证据摘要一致",
             "test_evolution_x.test_knowledge_agent_replay_keeps_archived_input", "软件回归"),
    Scenario("条件不确定性", "随机种子 901、四组共扰动", "经验分位与候选可行率，最差样本 RK45 复算",
             "test_evolution_x.test_seeded_31_dof_distribution_has_independent_reference", "合成数值计算"),
    Scenario("工程报告转档", "具有明确单位与配套证据的求解结果", "PDF 可追溯，兼容字体可显示加速度单位",
             "test_evolution_ix.test_research_pdf_keeps_numeric_evidence_and_portable_font_units", "软件回归"),
)
