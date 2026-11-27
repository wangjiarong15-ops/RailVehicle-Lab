"""One cancellable, time-bounded read-only job per active Streamlit session."""

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Queue, Empty
from threading import BoundedSemaphore, Event, Thread
from time import monotonic
from uuid import uuid4

from rail_vehicle.ai.provider import AIProviderUnavailable
from rail_vehicle.copilot.streaming import stream_analysis
from rail_vehicle.copilot.planning import local_plan, online_plan, planning_client, resolve_event_reference
from rail_vehicle.copilot.tools import execute, validate_plan
from rail_vehicle.deployment import public_demo_enabled


_SLOTS = BoundedSemaphore(4)


@dataclass
class Turn:
    prompt: str
    mode: str
    id: str = field(default_factory=lambda: uuid4().hex)
    plan: list = field(default_factory=list)
    results: list = field(default_factory=list)
    status: str = "running"
    active: str = ""
    message: str = ""
    verified: dict | None = None
    streaming: list = field(default_factory=list)

    def apply(self, event):
        kind = event["status"]
        if kind == "plan":
            self.plan = event["calls"]
        elif kind == "running":
            self.active = event["tool"]
        elif kind == "done":
            self.results.append(event)
            self.active = ""
        elif kind == "verified":
            self.verified = event["result"]
        elif kind == "claim":
            self.streaming.append((event["text"], event["evidence"]))
        else:
            self.status, self.message, self.active = kind, event.get("message", ""), ""
            self.streaming.clear()


def prior_calls(turns):
    return [{"tool": r["tool"], "args": r["args"]} for t in turns[-6:] for r in t.results][-24:]


class Job:
    def __init__(self):
        self.cancel = Event()
        self.queue = Queue()

    def drain(self, turn):
        while True:
            try:
                turn.apply(self.queue.get_nowait())
            except Empty:
                return

    def start(self, *args, **kwargs):
        thread = Thread(target=self.run, args=args, kwargs=kwargs, daemon=True, name="rail-copilot")
        thread.start()

    def run(
        self,
        work,
        prompt,
        previous=(),
        *,
        config=None,
        authorized=False,
        interpret=False,
        share_notes=False,
        budget=45.0,
        client_factory=planning_client,
    ):
        if not _SLOTS.acquire(blocking=False):
            self.queue.put(dict(status="error", message="本机计算任务已满，请稍后重试。"))
            return
        deadline = monotonic() + budget

        def checkpoint():
            if self.cancel.is_set() or monotonic() >= deadline:
                raise TimeoutError

        try:
            checkpoint()
            if not 0 < len(prompt.strip()) <= 1200:
                raise ValueError("请输入 1 至 1200 字的问题。")
            task = resolve_event_reference(prompt, previous, work.data.preferred_event if work.data else None)
            if config is not None:
                if not authorized or public_demo_enabled():
                    raise ValueError("本轮没有在线调用授权。")
                with closing(client_factory(config)) as client:
                    plan = online_plan(
                        client,
                        config.model,
                        task,
                        previous,
                        checkpoint,
                        work.notes if share_notes else (),
                        provider=config.provider,
                        has_batch=work.data is not None,
                    )
            else:
                plan = local_plan(task, previous)
            validate_plan(plan)
            report_tools = {"report", "coupled_report"}
            computations = [step for step in plan if step["tool"] not in report_tools]
            reports = [step for step in plan if step["tool"] in report_tools]
            self.queue.put(dict(status="plan", calls=computations + reports))
            reviewed = set()

            def run_steps(steps):
                if steps:
                    for event in execute(work, steps, cancelled=self.cancel.is_set, deadline=deadline):
                        checkpoint()
                        self.queue.put(event)
                        if event["status"] == "done" and event["tool"] not in report_tools:
                            work.report_items.append(event)
                        if event["status"] == "done" and event["result"].scene:
                            work.previous_scene = event["result"].scene
                        if event["status"] == "done" and event["tool"] == "review":
                            reviewed.update(event["result"].refs)

            run_steps(computations)
            if (
                config is not None
                and interpret
                and any(c["tool"] in ("events", "review", "statistics", "vibration") for c in computations)
            ):
                checkpoint()
                self.queue.put(dict(status="running", tool="证据校验与 AI 解读"))
                axes = {c["args"]["axis"] for c in plan if "axis" in c["args"]}
                axis = next(iter(axes)) if len(axes) == 1 else None
                with closing(client_factory(config)) as client:
                    result = stream_analysis(
                        client,
                        config.model,
                        prompt,
                        work.anonymous_payload,
                        axis,
                        checkpoint,
                        lambda text, eid: self.queue.put(dict(status="claim", text=text, evidence=eid)),
                        work.notes if share_notes else (),
                        event_ids=reviewed,
                        provider=config.provider,
                    )
                checkpoint()
                result.update(
                    selection_fingerprint=work.data.identity,
                    ai_metadata=dict(
                        provider=config.provider,
                        model=config.model,
                        generated_at=datetime.now(timezone.utc).isoformat(),
                    ),
                )
                work.verified = result
                self.queue.put(dict(status="verified", result=result))
            run_steps(reports)
            checkpoint()
            self.queue.put(dict(status="complete"))
        except TimeoutError:
            self.queue.put(
                dict(
                    status="stopped",
                    message="本轮已停止。" if self.cancel.is_set() else "本轮已超过时间预算。",
                )
            )
        except AIProviderUnavailable as exc:
            self.queue.put(dict(status="error", message=exc.user_message))
        except ValueError as exc:
            # Only controlled planner / local numerical validation messages reach the interface.
            self.queue.put(dict(status="error", message=str(exc)[:240]))
        except Exception:
            self.queue.put(dict(status="error", message="工具执行失败。请检查数据来源或重试。"))
        finally:
            _SLOTS.release()


@dataclass
class Conversation:
    scope: tuple
    id: str = field(default_factory=lambda: uuid4().hex)
    turns: list = field(default_factory=list)

    def begin(self, prompt, mode):
        if len(self.turns) >= 12:
            raise ValueError("本会话已满 12 轮，请新建会话。")
        turn = Turn(prompt, mode)
        self.turns.append(turn)
        return turn

    def last_scene(self):
        return next(
            (
                r["result"].scene
                for t in reversed(self.turns)
                for r in reversed(t.results)
                if r["result"].scene
            ),
            None,
        )

    def last_verified(self):
        return next((t.verified for t in reversed(self.turns) if t.verified), None)
