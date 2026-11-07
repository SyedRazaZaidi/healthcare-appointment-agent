"""Structured trace of everything the agent decides and does.

Each event is (a) kept in memory, (b) appended to a JSONL file and (c)
optionally echoed to the console. The trace is the *evidence* for the marking
rubric: it shows plans, tool selection, tool results, confirmations, memory
writes, retrieval scores and guardrail triggers.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_COLORS = {
    "user": "\033[1;37m", "plan": "\033[1;35m", "replan": "\033[1;33m", "tool_call": "\033[36m",
    "tool_result": "\033[32m", "tool_error": "\033[31m", "rag": "\033[34m", "memory": "\033[95m",
    "confirm": "\033[1;33m", "guardrail": "\033[1;31m", "assistant": "\033[1;37m", "info": "\033[90m",
}
_RESET = "\033[0m"


def _short(value: Any, limit: int = 220) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Tracer:
    def __init__(self, session_id: str, log_dir: str | Path | None = None, echo: bool = True,
                 sink: Callable[[str], None] | None = None, hide: frozenset[str] | set[str] = frozenset(),
                 on_event: Callable[[dict], None] | None = None):
        self.session_id = session_id
        self.events: list[dict] = []
        self.echo = echo
        self.hide = set(hide)  # event kinds not printed to the console (still logged)
        self.on_event = on_event  # structured-event consumer (the web UI streams these)
        self.sink = sink  # optional extra consumer of the printed lines (e.g. transcript writer)
        self.path: Path | None = None
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            self.path = Path(log_dir) / f"trace_{session_id}.jsonl"

    def emit(self, kind: str, **data: Any) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "session": self.session_id, "kind": kind, **data}
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        if self.on_event:
            self.on_event(event)
        line = self._format(kind, data)
        if self.sink:
            self.sink(line)
        if self.echo and line and kind not in self.hide:
            color = _COLORS.get(kind, "") if sys.stdout.isatty() else ""
            print(f"{color}{line}{_RESET if color else ''}")

    @staticmethod
    def _format(kind: str, d: dict) -> str:
        if kind == "user":
            return f"PATIENT  > {d['text']}"
        if kind == "assistant":
            return f"ASSISTANT> {d['text']}"
        if kind in ("plan", "replan"):
            label = "REPLAN" if kind == "replan" else "PLAN"
            head = f"  [{label} v{d['version']}] {d['goal']}" + (f"  (reason: {d['reason']})" if d.get("reason") else "")
            steps = "\n".join(
                f"      {i}. {s.get('step', '')}  -> tool: {s.get('tool', '-')}  | why: {s.get('why', '')}"
                for i, s in enumerate(d["steps"], 1)
            )
            return f"{head}\n{steps}"
        if kind == "tool_call":
            return f"  [TOOL CALL] {d['name']} {_short(d['args'])}  (via {d['via']})"
        if kind == "tool_result":
            return f"  [TOOL RESULT] {d['name']}: {_short(d['result'])}"
        if kind == "tool_error":
            return f"  [TOOL ERROR] {d['name']}: {_short(d['error'])}"
        if kind == "rag":
            hits = ", ".join(f"{p['source']}#{p['section']} ({p['score']})" for p in d["passages"]) or "none above threshold"
            return f"  [RAG] query={d['query']!r} -> {hits}"
        if kind == "memory":
            return f"  [MEMORY] {d['op']}: {_short(d['detail'])}"
        if kind == "confirm":
            return f"  [HUMAN CONFIRMATION] {d['summary']}  => {'APPROVED' if d['approved'] else 'DECLINED'}"
        if kind == "guardrail":
            return f"  [GUARDRAIL] {d['rule']}: {d['detail']}"
        if kind == "info":
            return f"  [INFO] {d['text']}"
        return f"  [{kind}] {_short(d)}"
