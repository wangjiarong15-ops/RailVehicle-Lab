"""Integration across project context, knowledge, acquisition, and physical studies."""

import json
from io import BytesIO
import sqlite3

import pytest

from rail_vehicle.acquisition_store import append_packets, load_recording, open_recording
from rail_vehicle.copilot.tools import Work
from rail_vehicle.db import get_connection, init_db
from rail_vehicle.engineering_project import capture, create_project, read_revision, save_revision
from rail_vehicle.engineering_backup import copy_verified_database
from rail_vehicle.knowledge_library import import_document, original, passages
from rail_vehicle.measurement_stream import Acquisition, Device, Packet
from rail_vehicle.nonlinear_vehicle import WheelRailCase
from rail_vehicle.vehicle_uncertainty import paired_study
from rail_vehicle.wheel_track import template
from rail_vehicle.workbench_planning import local_proposal
from rail_vehicle.workbench_queue import (archived_round, create, duplicate_for_replay, job, record_round,
                                          results, run_queue)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "platform.sqlite"
    init_db(path)
    return path


def test_project_versions_and_knowledge_are_scoped(database):
    first, second = (create_project(name, database) for name in ("项目甲", "项目乙"))
    case = WheelRailCase(speed_m_s=16., duration_s=1.)
    context = capture("动力学仿真", case=case, project=first)
    assert save_revision(first, context, database) == 1
    assert save_revision(first, {**context, "page": "RailVehicle Copilot"}, database) == 2
    assert read_revision(first, 1, database) == context
    with pytest.raises(ValueError):
        read_revision(second, 1, database)
    with get_connection(database) as connection:
        connection.execute("UPDATE engineering_project_revisions SET sha256='different' WHERE project_id=? AND revision=2",
                           (first,))
    with pytest.raises(ValueError, match="摘要"):
        read_revision(first, 2, database)

    raw = "二系悬挂公式 F=kx\n本段是工程示例。".encode()
    doc, version = import_document(first, "悬挂规范.txt", "合成示例・本地授权", raw, database)
    assert version == 1 and original(first, doc, version, database)[1] == raw
    assert import_document(first, "悬挂规范.txt", "合成示例・本地授权", raw, database) == (doc, 1)
    assert import_document(first, "悬挂规范.txt", "合成示例・本地授权", raw + b"\nF = k*x", database) == (doc, 2)
    assert not passages(second, "悬挂公式", database)
    hit, = passages(first, "悬挂公式", database)
    assert hit.id.endswith(".v2.1") and "行 1–3" in hit.source
    assert hit.text.startswith("二系悬挂公式")


def test_knowledge_agent_replay_keeps_archived_input(database):
    project = create_project("工程资料", database)
    body = "二系刚度试验，弹簧形变 F=kx。".encode()
    document, _ = import_document(project, "sandbox.md", "研发示例", body, database)
    notes = tuple(passages(project, "二系刚度", database))
    scope = capture("RailVehicle Copilot", project=project)
    identifier = create("检索二系刚度资料", Work(None, notes=notes, engineering=scope), db_path=database)
    plan = local_proposal("检索二系刚度资料")
    record_round(identifier, plan, "local", database)
    run_queue(database)
    earlier = results(identifier, database)
    citations = earlier["r0_reference"]["output"]["citations"]
    assert any(cite["id"] == f"library.{document}.v1.1" for cite in citations)
    import_document(project, "sandbox.md", "研发示例", "更新后的同名资料".encode(), database)
    clone = duplicate_for_replay(identifier, database)
    assert job(clone, database)["status"] == "draft"
    proposal = archived_round(job(clone, database), database)
    record_round(clone, proposal, "replay", database)
    run_queue(database)
    after = results(clone, database)
    assert earlier["r0_reference"]["proof"] == after["r0_reference"]["proof"]
    assert earlier["r0_reference"]["execution"] == after["r0_reference"]["execution"]


def test_pdf_library_citation_keeps_original_page(database):
    pytest.importorskip("pypdf")
    from reportlab.pdfgen.canvas import Canvas

    document = BytesIO()
    writer = Canvas(document)
    writer.drawString(30, 740, "Suspension preload evidence")
    writer.showPage()
    writer.drawString(30, 740, "Damping coefficient equation")
    writer.save()
    project = create_project("合成 PDF 证据", database)
    identifier, version = import_document(project, "damping.pdf", "本机生成的试验资料", document.getvalue(), database)
    match, = passages(project, "coefficient equation", database)
    assert match.source.endswith("第 2 页")
    assert original(project, identifier, version, database)[1] == document.getvalue()


def test_task_context_detects_tampering(database):
    identifier = create("上下文核对", Work(None, engineering=capture("动力学仿真")), db_path=database)
    changed = job(identifier, database)["context"]
    changed["engineering"]["page"] = "其他入口"
    with get_connection(database) as connection:
        connection.execute("UPDATE workbench_jobs SET context=? WHERE id=?", (json.dumps(changed), identifier))
    with pytest.raises(ValueError, match="摘要"):
        job(identifier, database)


def test_backup_and_restore_retain_project_revisions_without_replacing_source(database, tmp_path):
    project = create_project("备份复核", database)
    expected = capture("动力学仿真", project=project)
    save_revision(project, expected, database)
    archived = tmp_path / "verified-copy.sqlite"
    restored = tmp_path / "restore-separate.sqlite"
    assert len(copy_verified_database(database, archived)) == 64
    copy_verified_database(archived, restored)
    assert read_revision(project, 1, restored) == expected
    with pytest.raises(ValueError, match="不会被覆盖"):
        copy_verified_database(archived, database)


def test_active_case_is_really_recomputed_with_independent_integrator(database):
    case = WheelRailCase(route=template("curve", 610.), speed_m_s=17., duration_s=1.)
    context = capture("动力学仿真", case=case)
    identifier = create("当前工况独立复算", Work(None, engineering=context), db_path=database)
    record_round(identifier, local_proposal("当前工况独立复算", engineering=context), "local", database)
    run_queue(database)
    output = results(identifier, database)
    assert job(identifier, database)["status"] == "review"
    assert output["r0_solve"]["output"]["response"]["case"]["speed_m_s"] == 17.
    assert output["r0_solve"]["output"]["response"]["solver"]["method"] == "DOP853"
    assert output["r0_independent"]["output"]["response"]["solver"]["method"] == "RK45"
    assert output["r0_verify"]["output"]["metrics"]["passed"]


def test_seeded_31_dof_distribution_has_independent_reference():
    case = WheelRailCase(route=template("curve", 580.), speed_m_s=18., duration_s=1.)
    evidence = paired_study(case, draws=4, seed=901, spread=.06, unloading_ratio=.4, travel_mm=35.)
    assert len(evidence["rows"]) == 4
    assert len({row["speed_multiplier"] for row in evidence["rows"]}) == 4
    assert 0 <= evidence["sample_candidate_feasible_fraction"] <= 1
    assert evidence["independent"]["absolute_difference_m_s2"] < .0001
    assert evidence["empirical_reduction_m_s2"] == sorted(evidence["empirical_reduction_m_s2"])
    with pytest.raises(ValueError):
        paired_study(case, draws=32, seed=901, spread=.06, unloading_ratio=.4, travel_mm=35.)


def test_acquisition_checkpoints_restart_in_order_and_reject_other_writers(database):
    device = Device("sim", "acceleration", "m/s2", 1, 1., 0., 0, "declared", "common_trigger")
    data = Acquisition((device,), 1_000_000, "simulated")
    identifier = open_recording("模拟接续", data, database)
    for index in range(50):
        data.push(Packet("sim", index, 1_000_000 + 20_000 * index, index / 10))
    assert append_packets(identifier, 0, data, database) == 50
    restored = load_recording(identifier, database)
    assert restored.evidence_id == data.evidence_id
    restored.push(restored.packets[-1])
    restored.push(Packet("sim", 54, 2_080_000, 1.))
    assert append_packets(identifier, 50, restored, database) == 52
    assert load_recording(identifier, database).counters["missing_sequence"] == 4
    assert load_recording(identifier, database).counters["repeated"] == 1
    with pytest.raises(ValueError, match="已移动"):
        append_packets(identifier, 50, restored, database)
    with get_connection(database) as connection:
        connection.execute("UPDATE acquisition_packets SET packet=replace(packet,'4.9','4.1') "
                           "WHERE session_id=? AND offset=49", (identifier,))
    with pytest.raises(ValueError, match="摘要"):
        load_recording(identifier, database)
