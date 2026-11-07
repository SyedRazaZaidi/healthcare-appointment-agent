"""Interactive chat:  python -m clinic_agent.cli --patient P1001

Commands inside the chat:
  /memory   show what the assistant has saved about you (long-term memory)
  /plan     show the agent's current plan
  /new      end this session (saving a summary) and start a NEW session
  /quit     end the session and exit
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from .config import get_settings
from .runtime import Runtime
from .tracing import Tracer


async def cli_confirm(summary: str) -> bool:
    """Human-in-the-loop gate: the patient must type 'y' for any booking change."""
    answer = await asyncio.to_thread(
        input, f"\n  >>> CONFIRMATION REQUIRED: {summary}\n  Approve this action? [y/N] "
    )
    return answer.strip().lower() in ("y", "yes")


async def amain(patient_id: str, show_trace: bool) -> None:
    settings = get_settings()
    async with Runtime(settings) as rt:
        patient = rt.login(patient_id)

        async def open_session():
            sid = f"s-{uuid.uuid4().hex[:6]}"
            tracer = Tracer(sid, settings.log_dir, echo=True, hide={"user", "assistant"} | (set() if show_trace else
                            {"info", "plan", "replan", "tool_call", "tool_result", "rag", "memory", "tool_error"}))
            return await rt.new_agent(patient, cli_confirm, tracer, sid)

        agent = await open_session()
        print(f"\n{settings.clinic_name} - virtual assistant  (signed in as {patient['name']}, {patient['patient_id']})")
        print(f"model: {rt.llm.name} | clinic time: {settings.clinic_now:%a %d %b %Y %H:%M} | type /quit to exit\n")
        while True:
            try:
                text = (await asyncio.to_thread(input, "You> ")).strip()
            except (EOFError, KeyboardInterrupt):
                text = "/quit"
            if not text:
                continue
            if text == "/quit":
                print("Saved:", agent.end_session())
                return
            if text == "/new":
                print("Saved:", agent.end_session())
                agent = await open_session()
                print("--- new session started (long-term memory reloaded) ---")
                continue
            if text == "/memory":
                print(rt.ltm.profile_block(patient["patient_id"]))
                continue
            if text == "/plan":
                print(agent.plan or "No plan yet.")
                continue
            reply = await agent.respond(text)
            print(f"\nAssistant> {reply.text}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="CareBridge appointment assistant (CLI)")
    parser.add_argument("--patient", default="P1001", help="patient id to sign in as (P1001, P1002, P1003)")
    parser.add_argument("--quiet", action="store_true", help="hide plans/tool calls/retrieval in the console")
    args = parser.parse_args()
    asyncio.run(amain(args.patient, show_trace=not args.quiet))


if __name__ == "__main__":
    main()
