"""The primary agent: an LLM-driven tool-using loop with memory, RAG, MCP,
planning/replanning and human oversight.

One call to :meth:`ClinicAgent.respond` handles one patient message:

    emergency pre-screen (code)
        -> short-term memory gets the message
        -> loop (max N steps):
              LLM sees: system prompt (+ long-term memory) + conversation + tool list
              LLM answers with text  -> done
              LLM requests tools     -> we execute them, feed results back, loop

Tool routing (the LLM decides *which*; the code enforces *how*):

    search_clinic_knowledge   local   RAG over the vector database
    remember / forget         local   long-term memory (SQLite)
    update_plan               local   explicit plan / replan record
    list_doctors, find_available_slots, get_slot, list_my_appointments,
    book_appointment, cancel_appointment, reschedule_appointment
                              MCP     discovered from the scheduling MCP server

Code-level guarantees (independent of the prompt):
    * patient_id is injected by the agent; the model cannot act for someone else
    * book/cancel/reschedule run only after a human approves the exact details
      (fail-closed: no confirm callback means no consequential action)
    * tool failures never crash the loop; the model gets a structured error
      plus a hint to replan
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Protocol

from .llm import LLMClient, ToolCall
from .memory import ALLOWED_CATEGORIES, LongTermMemory, ShortTermMemory
from .prompts import build_system_prompt
from .rag import KnowledgeBase
from .safety import CONSEQUENTIAL_TOOLS, EMERGENCY_REPLY, detect_emergency, fence_untrusted, flag_injection
from .tracing import Tracer

ConfirmFn = Callable[[str], Awaitable[bool]]

REPLAN_HINT = (
    "The action did not succeed. Do NOT repeat the same call and do NOT tell the patient it worked. "
    "Call update_plan with is_replan=true and a short reason, then continue with a revised plan "
    "(use the alternatives provided, or search again with different filters that respect the patient's "
    "preferences) and offer at most three options."
)
MAX_TOOL_OUTPUT_CHARS = 8000


class MCPLike(Protocol):
    async def list_tools(self) -> list[dict]: ...
    async def call_tool(self, name: str, arguments: dict): ...


def fmt_when(day: str, start: str) -> str:
    return f"{date.fromisoformat(day):%a %d %b %Y}, {start}"


@dataclass
class AgentReply:
    text: str
    steps: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    plan_version: int = 0
    guardrail: str | None = None


class ClinicAgent:
    def __init__(
        self,
        *,
        llm: LLMClient,
        mcp: MCPLike,
        kb: KnowledgeBase,
        ltm: LongTermMemory,
        patient: dict,
        clinic_name: str,
        clinic_now: datetime,
        confirm: ConfirmFn | None = None,
        tracer: Tracer | None = None,
        session_id: str | None = None,
        max_steps: int = 8,
        stm_turns: int = 12,
        max_tokens: int = 1500,
        llm_retries: int = 2,
        retry_delay: float = 1.0,
        min_call_interval: float = 0.0,
    ):
        self.llm, self.mcp, self.kb, self.ltm = llm, mcp, kb, ltm
        self.patient_id = patient["patient_id"]
        self.patient_name = patient["name"]
        self.clinic_name, self.clinic_now = clinic_name, clinic_now
        self.confirm = confirm
        self.session_id = session_id or f"s-{uuid.uuid4().hex[:8]}"
        self.tracer = tracer or Tracer(self.session_id, echo=False)
        self.max_steps, self.max_tokens = max_steps, max_tokens
        self.llm_retries, self.retry_delay = llm_retries, retry_delay
        self.min_call_interval = min_call_interval
        self._last_llm_call = 0.0
        self.stm = ShortTermMemory(max_turns=stm_turns)

        self.plan: dict | None = None
        self.plan_history: list[dict] = []
        self.replans = 0
        self._saved_facts: dict[str, str] = {}
        self._llm_tools: list[dict] = []
        self._mcp_tool_names: set[str] = set()
        self._bound_tools: set[str] = set()

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Discover MCP tools and build the tool list shown to the LLM."""
        mcp_tools = await self.mcp.list_tools()
        self._mcp_tool_names = {t["name"] for t in mcp_tools}
        exposed = []
        for t in mcp_tools:
            schema = copy.deepcopy(t["input_schema"])
            props = schema.get("properties", {})
            if "patient_id" in props:
                # The model never sees (and so never chooses) the patient id.
                self._bound_tools.add(t["name"])
                props.pop("patient_id")
                schema["required"] = [r for r in schema.get("required", []) if r != "patient_id"]
            exposed.append({"name": t["name"], "description": t["description"], "input_schema": schema})
        self._llm_tools = exposed + self._local_tool_specs()
        self.tracer.emit(
            "info",
            text=f"session {self.session_id} started for patient {self.patient_id}; "
                 f"{len(mcp_tools)} MCP tools discovered: {sorted(self._mcp_tool_names)}",
        )

    @staticmethod
    def _local_tool_specs() -> list[dict]:
        return [
            {
                "name": "search_clinic_knowledge",
                "description": "Search the clinic's official documents (policies, fees, hours, registration, "
                               "telehealth, preparation, privacy, referrals). Returns relevant passages with "
                               "source file and section and a similarity score. Use for ANY policy or procedure question.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "A self-contained search query in plain English."},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "remember",
                "description": "Save a durable NON-CLINICAL preference about the patient to long-term memory "
                               "(preferred time of day, preferred doctor, days to avoid, accessibility need, reminder "
                               "channel). Never save diagnoses, symptoms, medicines or reasons for a medical visit.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(ALLOWED_CATEGORIES)},
                        "key": {"type": "string", "description": "short snake_case name, e.g. preferred_time_of_day"},
                        "value": {"type": "string", "description": "the preference, e.g. 'mornings'"},
                    },
                    "required": ["category", "key", "value"],
                },
            },
            {
                "name": "forget",
                "description": "Delete one saved preference by key, or everything with key='*'. Use only when the "
                               "patient asks to forget something.",
                "input_schema": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
            {
                "name": "update_plan",
                "description": "Record your plan for the current request (call first when 2+ tool calls are needed) "
                               "or revise it after a failure (is_replan=true). Each step names the tool and why.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step": {"type": "string", "description": "short description of the step (never empty)"},
                                    "tool": {"type": "string"},
                                    "why": {"type": "string"},
                                },
                                "required": ["step", "tool", "why"],
                            },
                        },
                        "is_replan": {"type": "boolean"},
                        "reason": {"type": "string", "description": "Why the plan changed (replans only)."},
                    },
                    "required": ["goal", "steps", "is_replan"],
                },
            },
        ]

    def _system_prompt(self) -> str:
        return build_system_prompt(
            clinic_name=self.clinic_name,
            today=f"{self.clinic_now:%a %d %b %Y, %H:%M}",
            patient_name=self.patient_name,
            memory_block=self.ltm.profile_block(self.patient_id),
        )

    # ------------------------------------------------------------------
    # main entry point
    # ------------------------------------------------------------------
    async def respond(self, user_text: str) -> AgentReply:
        self.tracer.emit("user", text=user_text)
        self.stm.begin_turn({"role": "user", "content": user_text})
        tool_log: list[dict] = []

        # Guardrail 1: possible emergency never reaches the planner.
        if detect_emergency(user_text):
            self.tracer.emit("guardrail", rule="emergency_prescreen",
                             detail="possible emergency detected; LLM bypassed, safety message returned")
            return self._finish(EMERGENCY_REPLY, 0, tool_log, guardrail="emergency")

        for step in range(1, self.max_steps + 1):
            try:
                response = await self._call_llm()
            except Exception as exc:  # noqa: BLE001
                self.tracer.emit("tool_error", name="llm", error=f"{type(exc).__name__}: {exc}")
                msg = ("I'm sorry, I'm having technical trouble right now and can't complete that. "
                       "Please try again in a moment, or call the front desk on 555-0142.")
                return self._finish(msg, step, tool_log, guardrail="llm_unavailable")

            if not response.tool_calls:
                text = response.text.strip() or (
                    "I'm sorry, I couldn't put together an answer. Could you rephrase your request?")
                return self._finish(text, step, tool_log)

            if response.text:
                self.tracer.emit("info", text=f"assistant (before tools): {response.text}")
            self.stm.add({"role": "assistant", "content": response.blocks})
            results = []
            for call in response.tool_calls:
                content, is_error = await self._execute(call, tool_log)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": content, "is_error": is_error})
            self.stm.add({"role": "user", "content": results})

        self.tracer.emit("guardrail", rule="max_steps", detail=f"stopped after {self.max_steps} reasoning steps")
        msg = ("I wasn't able to finish that safely. Please call the front desk on 555-0142 "
               "and they will help you directly.")
        return self._finish(msg, self.max_steps, tool_log, guardrail="max_steps")

    def _finish(self, text: str, steps: int, tool_log: list[dict], guardrail: str | None = None) -> AgentReply:
        self.stm.add({"role": "assistant", "content": text})
        self.tracer.emit("assistant", text=text)
        return AgentReply(text, steps, tool_log, self.plan["version"] if self.plan else 0, guardrail)

    async def _throttle(self) -> None:
        """Keep a minimum gap between LLM calls (helps stay under free-tier requests-per-minute)."""
        if self.min_call_interval > 0:
            wait = self.min_call_interval - (time.monotonic() - self._last_llm_call)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_llm_call = time.monotonic()

    @staticmethod
    def _server_retry_hint(exc: Exception) -> float | None:
        """Seconds the provider asked us to wait (Retry-After header or 'retry in 32s' in the body)."""
        resp = getattr(exc, "response", None)
        headers = getattr(resp, "headers", None) or {}
        try:
            if headers.get("retry-after"):
                return float(headers["retry-after"])
        except (ValueError, TypeError):
            pass
        text = (getattr(resp, "text", "") or "") + " " + str(exc)
        m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', text) or re.search(
            r"retry in (\d+(?:\.\d+)?)s", text, re.I)
        return float(m.group(1)) if m else None

    async def _call_llm(self):
        last: Exception | None = None
        for attempt in range(self.llm_retries + 1):
            try:
                await self._throttle()
                return await self.llm.chat(
                    system=self._system_prompt(),
                    messages=self.stm.messages,
                    tools=self._llm_tools,
                    max_tokens=self.max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - network/rate limit/etc.
                last = exc
                status = getattr(exc, "status_code", None)
                detail = f"{type(exc).__name__} (HTTP {status}): {exc}" if status else f"{type(exc).__name__}: {exc}"
                url = getattr(getattr(exc, "request", None), "url", None)
                body = getattr(getattr(exc, "response", None), "text", "")
                if url:
                    detail += f" | url={url}"
                if body:
                    detail += f" | body={str(body)[:700]}"
                self.tracer.emit("info", text=f"LLM call failed (attempt {attempt + 1}): {detail}")
                if status in (400, 401, 403, 404):
                    break  # configuration/auth/model errors: retrying cannot help
                if attempt < self.llm_retries:
                    wait = self.retry_delay * (2 ** attempt)
                    if status == 429 and self.retry_delay > 0:  # rate limit: wait as long as the provider asks
                        wait = min(90.0, max(wait, self._server_retry_hint(exc) or 20.0))
                        self.tracer.emit("info", text=f"rate limited; waiting {wait:.0f}s before retrying")
                    await asyncio.sleep(wait)
        raise last  # type: ignore[misc]

    # ------------------------------------------------------------------
    # tool execution
    # ------------------------------------------------------------------
    async def _execute(self, call: ToolCall, tool_log: list[dict]) -> tuple[str, bool]:
        name, args = call.name, dict(call.input or {})
        is_error = False
        try:
            if name == "update_plan":
                data = self._tool_update_plan(args)
            elif name == "search_clinic_knowledge":
                data = self._tool_search(args)
            elif name == "remember":
                data = self._tool_remember(args)
            elif name == "forget":
                data = self._tool_forget(args)
            elif name in self._mcp_tool_names:
                data = await self._tool_mcp(name, args)
            else:
                is_error = True
                data = {"ok": False, "error_code": "UNKNOWN_TOOL", "message": f"No tool named '{name}'."}
                self.tracer.emit("tool_error", name=name, error="unknown tool requested by the model")
        except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the session
            is_error = True
            data = {"ok": False, "error_code": "AGENT_TOOL_ERROR", "message": f"{type(exc).__name__}: {exc}"}
            self.tracer.emit("tool_error", name=name, error=data["message"])

        content = json.dumps(data, ensure_ascii=False, default=str)
        if len(content) > MAX_TOOL_OUTPUT_CHARS:
            content = content[:MAX_TOOL_OUTPUT_CHARS] + "...[truncated]"
        if flag_injection(content):
            self.tracer.emit("guardrail", rule="injection_flag", detail=f"instruction-like text in output of {name}")
            content = fence_untrusted(content)
        ok = not (isinstance(data, dict) and data.get("ok") is False)
        tool_log.append({"name": name, "args": args, "ok": ok})
        return content, is_error

    # ---- local tools ---------------------------------------------------
    def _tool_update_plan(self, args: dict) -> dict:
        steps = []
        for s in args.get("steps") or []:
            s = dict(s) if isinstance(s, dict) else {"step": str(s), "tool": "-", "why": ""}
            if not str(s.get("step", "")).strip():  # some models leave the description empty
                s["step"] = f"call {s.get('tool', '-')}"
            steps.append(s)
        is_replan = bool(args.get("is_replan"))
        version = (self.plan["version"] + 1) if self.plan else 1
        self.plan = {"version": version, "goal": args.get("goal", ""), "steps": steps,
                     "is_replan": is_replan, "reason": args.get("reason", "")}
        self.plan_history.append(copy.deepcopy(self.plan))
        if is_replan:
            self.replans += 1
        self.tracer.emit("replan" if is_replan else "plan", version=version, goal=self.plan["goal"],
                         steps=steps, reason=self.plan["reason"])
        return {"ok": True, "plan_version": version, "message": "Plan recorded. Continue executing it."}

    def _tool_search(self, args: dict) -> dict:
        query = str(args.get("query", ""))
        passages = self.kb.retrieve(query)
        self.tracer.emit("rag", query=query, passages=[
            {"source": p.source, "section": p.section, "score": p.score} for p in passages])
        return KnowledgeBase.as_tool_result(passages)

    def _tool_remember(self, args: dict) -> dict:
        result = self.ltm.remember(self.patient_id, str(args.get("category", "")), str(args.get("key", "")),
                                   str(args.get("value", "")), session_id=self.session_id)
        self.tracer.emit("memory", op="remember" if result["ok"] else "remember_rejected", detail=result)
        if result["ok"]:
            saved = result["saved"]
            self._saved_facts[saved["key"]] = saved["value"]
        return result

    def _tool_forget(self, args: dict) -> dict:
        key = str(args.get("key", ""))
        result = self.ltm.forget(self.patient_id, key)
        self.tracer.emit("memory", op="forget", detail={"key": key, **result})
        self._saved_facts.pop(key, None)
        return result

    # ---- MCP tools -----------------------------------------------------
    async def _tool_mcp(self, name: str, args: dict) -> Any:
        # Guardrail 3: identity binding.
        if name in self._bound_tools:
            supplied = args.get("patient_id")
            if supplied and supplied != self.patient_id:
                self.tracer.emit("guardrail", rule="identity_binding",
                                 detail=f"model supplied patient_id={supplied!r}; replaced with signed-in patient")
            args["patient_id"] = self.patient_id
        else:
            args.pop("patient_id", None)

        gated = name in CONSEQUENTIAL_TOOLS
        summary = ""
        if gated:  # Guardrail 2: human oversight, fail-closed.
            summary = await self._describe_action(name, args)
            approved = bool(self.confirm and await self.confirm(summary))
            self.tracer.emit("confirm", summary=summary, approved=approved)
            if not approved:
                self.ltm.log_audit(session_id=self.session_id, patient_id=self.patient_id, tool=name,
                                   args=args, human_confirmed=False, ok=False, summary=f"DECLINED: {summary}")
                return {"ok": False, "error_code": "USER_DECLINED",
                        "message": "The patient did not confirm this action, so nothing was changed. "
                                   "Do not retry; ask what they would like instead."}

        self.tracer.emit("tool_call", name=name, args=args, via="MCP")
        result = await self.mcp.call_tool(name, args)
        data = result.data
        if isinstance(data, dict):
            data = dict(data)
            if data.get("ok") is False:
                data["agent_hint"] = REPLAN_HINT
        self.tracer.emit("tool_result", name=name, result=data)

        if gated:
            ok = isinstance(data, dict) and data.get("ok") is True
            self.ltm.log_audit(session_id=self.session_id, patient_id=self.patient_id, tool=name, args=args,
                               human_confirmed=True, ok=ok, summary=self._outcome_summary(name, data) if ok else
                               f"FAILED: {summary}")
        return data

    async def _describe_action(self, name: str, args: dict) -> str:
        """Human-readable description of what is about to happen, built from
        fresh tool data (not from the model's words)."""
        try:
            if name == "book_appointment":
                return "BOOK " + await self._slot_text(args["slot_id"]) + (f" - reason: {args['reason']}" if args.get("reason") else "")
            appts = (await self.mcp.call_tool("list_my_appointments", {"patient_id": self.patient_id})).data
            by_id = {a["appointment_id"]: a for a in appts.get("appointments", [])}
            old = by_id.get(args.get("appointment_id", ""))
            old_txt = (f"{old['doctor_name']} ({old['department']}), {fmt_when(old['date'], old['start_time'])} "
                       f"[{old['appointment_id']}]") if old else f"appointment {args.get('appointment_id')}"
            if name == "cancel_appointment":
                return f"CANCEL {old_txt}"
            if name == "reschedule_appointment":
                return f"RESCHEDULE {old_txt} -> {await self._slot_text(args['new_slot_id'])}"
        except Exception:  # noqa: BLE001
            pass
        return f"{name.upper()} {json.dumps(args, sort_keys=True)}"

    async def _slot_text(self, slot_id: str) -> str:
        data = (await self.mcp.call_tool("get_slot", {"slot_id": slot_id})).data
        if isinstance(data, dict) and data.get("ok"):
            s = data["slot"]
            return f"{s['doctor_name']} ({s['department']}), {fmt_when(s['date'], s['start_time'])} [slot {slot_id}]"
        return f"slot {slot_id}"

    @staticmethod
    def _outcome_summary(name: str, data: dict) -> str:
        a = data.get("appointment") or {}
        where = f"{a.get('doctor_name')} ({a.get('department')}) on {a.get('weekday')} {a.get('date')} {a.get('start_time')}"
        if name == "book_appointment":
            return f"Booked {a.get('appointment_id')}: {where}"
        if name == "reschedule_appointment":
            return f"Rescheduled {data.get('old_appointment_id')} -> {a.get('appointment_id')}: {where}"
        return f"Cancelled {data.get('appointment_id')}" + (" (late cancellation)" if data.get("late_cancellation") else "")

    # ------------------------------------------------------------------
    # end of session -> long-term memory
    # ------------------------------------------------------------------
    def end_session(self) -> str:
        """Write a deterministic summary of what was *done* (taken from the audit
        log, not from the model's recollection) into long-term memory."""
        actions = [r["summary"] for r in self.ltm.audit_rows(session_id=self.session_id)
                   if r["ok"] and r["human_confirmed"]]
        prefs = [f"saved preference {k}={v}" for k, v in self._saved_facts.items()]
        summary = "; ".join(actions + prefs) or "General enquiry (no bookings or preference changes)."
        self.ltm.add_session_summary(self.session_id, self.patient_id, summary)
        self.tracer.emit("memory", op="session_summary_saved", detail=summary)
        return summary
