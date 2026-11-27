from contextlib import nullcontext
import json
from time import time
from types import SimpleNamespace

import pytest

from rail_vehicle.copilot.context import Dataset
from rail_vehicle.copilot.tools import Work
from rail_vehicle.db import get_connection, init_db
from rail_vehicle.demo_data import spectral_demo_data
from rail_vehicle.workbench_planning import cloud_proposal, local_proposal
from rail_vehicle.workbench_queue import (create, dispatch, job, listing, note_cloud_call, record_round,
                                          recover, results, run_queue, update, validate)
from rail_vehicle.workbench_tools import definitions


def work():
    vehicle,batches,rows = spectral_demo_data()
    return Work(*(Dataset("demo",vehicle,b,rows[b["id"]],batches=tuple(batches)) for b in batches[:2]))


def plan_step(id, tool, depends=(), **args):
    return {"id":id,"tool":tool,"args":args,"depends":list(depends)}


def test_contract_topological_order_bounds_and_allowlist():
    assert all(item["permission"]=="read_only" and item["units"] for item in definitions().values())
    assert [s["id"] for s in validate([plan_step("b","evidence_report",("a",)),
                                      plan_step("a","quality")])]==["a","b"]
    with pytest.raises(ValueError,match="循环"):
        validate([plan_step("a","quality",("b",)),plan_step("b","quality",("a",))])
    with pytest.raises(ValueError,match="无效"):
        validate([plan_step("shell","shell")])
    with pytest.raises(ValueError,match="无效"):
        validate([plan_step("x","contact_fit",tangent_weight=-1)])


def test_multiple_jobs_spawn_and_restore_priority_then_specialized_routes(tmp_path):
    pytest.importorskip("scipy")
    db = tmp_path/"agent.db"
    assert not listing(db)
    init_db(db)
    tasks = [("比较两批运行质量", "compare"),("联系横向频谱与待验证故障假设", "vibration"),
             ("轮轨接触校准与误差分析", "contact_fit"),("横向异常与悬挂优化", "pareto31"),
             ("不同线路工况对照求解", "solve31")]
    ids = {}
    for goal,focus in tasks:
        identifier = create(goal,work(),priority=int(focus=="contact_fit"),db_path=db)
        record_round(identifier,local_proposal(goal),"local",db)
        ids[focus] = identifier
    assert dispatch(db)>0
    from time import monotonic, sleep
    until = monotonic()+130
    while monotonic()<until and any(j["status"] in ("queued","running") for j in listing(db)):
        sleep(.15)
    for focus,identifier in ids.items():
        record = job(identifier,db)
        assert record["status"]=="review",record["error"]
        output = results(identifier,db)
        specific = [output[s["id"]] for s in record["steps"] if s["tool"]==focus]
        assert specific and all(item["status"]=="done" for item in specific),specific
        assert all(item.get("proof") for item in specific)
    identifier = ids["pareto31"]
    previous = results(identifier,db)
    follow = local_proposal("请用独立方法复算",job(identifier,db)["steps"],previous)
    record_round(identifier,follow,"local",db)
    run_queue(db)
    completed = results(identifier,db)
    assert completed[follow[0]["id"]]["output"]["response"]["solver"]["method"]=="RK45"
    assert all(completed[key]==value for key,value in previous.items())


def test_failure_recovery_and_expired_worker_preserve_approved_steps(tmp_path):
    db = tmp_path/"history.db"; init_db(db)
    selection=work()
    selection.other=selection.data
    identifier = create("对比同一批次的拒绝验证",selection,db_path=db)
    failure = plan_step("bad","compare")
    alternative = {**plan_step("explain","knowledge",("bad",),query="悬挂振动"),"recover":"bad"}
    record_round(identifier,[failure,alternative],"local",db)
    with get_connection(db) as connection:
        connection.execute("UPDATE workbench_jobs SET status='running', heartbeat=? WHERE id=?",(time()-60,identifier))
    recover(db)
    assert job(identifier,db)["status"]=="queued"
    update(identifier,"pause",db)
    assert job(identifier,db)["status"]=="paused"
    update(identifier,"resume",db)
    run_queue(db)
    output=results(identifier,db)
    assert output["bad"]["status"]=="failed" and output["explain"]["status"]=="done"
    update(identifier,"finish",db)
    assert job(identifier,db)["status"]=="complete"


def test_cloud_stream_can_change_tool_graph_and_receives_only_compact_status(tmp_path):
    db = tmp_path/"cloud.db"; init_db(db)
    identifier = create("待研究",work(),db_path=db)
    record_round(identifier, [plan_step("e1","events",axis="lateral")],"local",db)
    run_queue(db)
    record = job(identifier,db)
    observed = results(identifier,db)
    payload = json.dumps({"steps":[plan_step("scope","vibration",("e1",),axis="lateral") ]})
    class API:
        def create(self, **kwargs):
            user = json.loads(kwargs["messages"][1]["content"])
            assert set(user)=={"data_available","second_batch_available","steps","remaining_cost","requested_goal","tools"}
            assert user["steps"][0]["events_exist"] and "FICTIONAL-SPECTRUM" not in str(user)
            assert kwargs["max_tokens"]==1200 and kwargs["stream"]
            choices = [SimpleNamespace(finish_reason=None,delta=SimpleNamespace(content=payload[:22])),
                       SimpleNamespace(finish_reason="stop",delta=SimpleNamespace(content=payload[22:]))]
            return nullcontext([SimpleNamespace(choices=[c]) for c in choices])
    client = SimpleNamespace(chat=SimpleNamespace(completions=API()))
    note_cloud_call(identifier,db)
    follow = cloud_proposal(client,"deepseek","local-fixture","检查频谱",record,observed)
    assert follow[0]["tool"]=="vibration"
    record_round(identifier,follow,"cloud",db)
    run_queue(db)
    figure=results(identifier,db)["scope"]["output"]["figure"]
    assert figure["xlabel"]=="频率（Hz）"
    power=next(iter(figure["columns"].values()))
    assert figure["x"][power.index(max(power))]==pytest.approx(3.,abs=.12)
