"""Transport-independent core of the web UI.

One ``WebSession`` = one browser tab talking to one agent session. It owns:

* an outgoing queue of JSON-able messages (the web layer just pumps it to the socket),
* the bridge that turns the agent's *human-confirmation gate* into a UI dialog
  (a future that the browser resolves with approve / decline - fail-closed on timeout),
* turn execution in a background task, so the socket stays responsive while the agent
  thinks (the browser must be able to answer a confirmation *during* a turn).

It has no web-framework imports, so it is unit-tested directly (tests/test_web_session.py).
The Starlette wiring lives in ``web.py``.

Messages from the browser (``handle``):
    {"type": "user", "text": "..."}                      send a patient message
    {"type": "confirm", "id": "...", "approved": bool}   answer a confirmation dialog
    {"type": "new_session"}                              save a summary, start a fresh session
    {"type": "forget", "key": "..."}                     delete a stored preference ('*' = all)
    {"type": "refresh"}                                  re-send appointments / memory

Messages to the browser (``out`` queue):
    session, trace, turn_start, assistant, confirm_request, confirm_timeout,
    state, session_saved, turn_end, error
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any, Awaitable, Callable

from .tracing import Tracer

MAX_MESSAGE_CHARS = 2000
HIDDEN_TRACE_KINDS = {"user", "assistant"}  # the chat shows these itself


def jsonable(value: Any) -> Any:
    """Round-trip through JSON so the queue only ever holds plain data."""
    return json.loads(json.dumps(value, default=str))


class WebSession:
    def __init__(
        self,
        *,
        agent_factory: Callable[..., Awaitable[Any]],   # async (patient, confirm, tracer, session_id) -> ClinicAgent
        mcp: Any,
        ltm: Any,
        patient: dict,
        log_dir: Any = None,
        confirm_timeout: float = 300.0,
    ):
        self.agent_factory, self.mcp, self.ltm, self.patient = agent_factory, mcp, ltm, patient
        self.log_dir = log_dir
        self.confirm_timeout = confirm_timeout
        self.out: asyncio.Queue = asyncio.Queue()
        self.agent = None
        self.session_id = ""
        self._pending: dict[str, asyncio.Future] = {}
        self._turn: asyncio.Task | None = None

    # ------------------------------------------------------------------
    def emit(self, message: dict) -> None:
        self.out.put_nowait(jsonable(message))

    def _on_trace(self, event: dict) -> None:
        if event.get("kind") not in HIDDEN_TRACE_KINDS:
            self.emit({"type": "trace", "event": event})

    async def start(self) -> None:
        self.session_id = f"s-{uuid.uuid4().hex[:6]}"
        tracer = Tracer(self.session_id, self.log_dir, echo=False, on_event=self._on_trace)
        self.agent = await self.agent_factory(self.patient, self._confirm, tracer, self.session_id)
        pid = self.patient["patient_id"]
        self.emit({
            "type": "session",
            "session_id": self.session_id,
            "patient": self.patient,
            "memory_loaded": {"facts": len(self.ltm.facts(pid)), "summaries": len(self.ltm.recent_summaries(pid))},
        })
        await self._send_state()

    # ------------------------------------------------------------------
    # human-in-the-loop bridge
    # ------------------------------------------------------------------
    async def _confirm(self, summary: str) -> bool:
        cid = uuid.uuid4().hex[:8]
        future = asyncio.get_running_loop().create_future()
        self._pending[cid] = future
        self.emit({"type": "confirm_request", "id": cid, "summary": summary, "timeout": self.confirm_timeout})
        try:
            return bool(await asyncio.wait_for(future, self.confirm_timeout))
        except asyncio.TimeoutError:
            self.emit({"type": "confirm_timeout", "id": cid})
            return False  # fail closed: no answer means no action
        finally:
            self._pending.pop(cid, None)

    def resolve_confirm(self, cid: str, approved: bool) -> bool:
        future = self._pending.get(cid)
        if future is None or future.done():
            return False
        future.set_result(bool(approved))
        return True

    # ------------------------------------------------------------------
    # incoming messages
    # ------------------------------------------------------------------
    async def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "user":
            text = str(msg.get("text", "")).strip()[:MAX_MESSAGE_CHARS]
            if not text:
                return
            if self._turn and not self._turn.done():
                self.emit({"type": "error", "message": "The assistant is still working on your last message."})
                return
            self._turn = asyncio.create_task(self._run_turn(text))
        elif kind == "confirm":
            self.resolve_confirm(str(msg.get("id", "")), bool(msg.get("approved")))
        elif kind == "new_session":
            await self.new_session()
        elif kind == "forget":
            key = str(msg.get("key", "")).strip()
            if key:
                self.ltm.forget(self.patient["patient_id"], key)
                await self._send_state()
        elif kind == "refresh":
            await self._send_state()

    async def _run_turn(self, text: str) -> None:
        self.emit({"type": "turn_start"})
        try:
            reply = await self.agent.respond(text)
            self.emit({"type": "assistant", "text": reply.text, "guardrail": reply.guardrail,
                       "steps": reply.steps, "plan_version": reply.plan_version})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let a turn kill the socket
            self.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await self._send_state()
            self.emit({"type": "turn_end"})

    async def new_session(self) -> None:
        await self._cancel_turn()
        summary = self._save_summary()
        if summary:
            self.emit({"type": "session_saved", "summary": summary})
        await self.start()

    # ------------------------------------------------------------------
    async def snapshot(self) -> dict:
        pid = self.patient["patient_id"]
        res = await self.mcp.call_tool("list_my_appointments", {"patient_id": pid})
        appts = res.data.get("appointments", []) if isinstance(res.data, dict) else []
        audit = [
            {"ts": r["ts"], "tool": r["tool"], "human_confirmed": r["human_confirmed"], "ok": r["ok"],
             "summary": r["summary"]}
            for r in self.ltm.audit_rows(patient_id=pid)[-8:]
        ]
        return {
            "appointments": appts,
            "memory": {"facts": self.ltm.facts(pid), "summaries": self.ltm.recent_summaries(pid, 5)},
            "audit": audit,
            "plan": self.agent.plan if self.agent else None,
        }

    async def _send_state(self) -> None:
        try:
            self.emit({"type": "state", **(await self.snapshot())})
        except Exception as exc:  # noqa: BLE001
            self.emit({"type": "error", "message": f"Could not refresh state: {type(exc).__name__}: {exc}"})

    def _save_summary(self) -> str | None:
        if self.agent and self.agent.stm.turn_count:
            return self.agent.end_session()
        return None

    async def _cancel_turn(self) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(False)
        if self._turn and not self._turn.done():
            self._turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._turn

    async def close(self) -> None:
        """Browser tab closed: decline anything pending, stop the turn, save the session summary."""
        await self._cancel_turn()
        self._save_summary()
