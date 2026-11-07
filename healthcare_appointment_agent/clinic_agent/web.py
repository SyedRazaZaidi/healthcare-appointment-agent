"""Web UI server.

    python -m clinic_agent.web            # then open http://127.0.0.1:8000

Thin Starlette layer over the same agent the CLI uses:

    GET  /                 the single-page UI (web/index.html)
    GET  /api/info         model, MCP tools, RAG stats, patients, clinic clock
    GET  /api/kb/search    similarity search with scores and the cut-off (RAG visualiser)
    GET  /api/kb/docs      the knowledge-base documents
    POST /api/simulate     inject a change event: race | doctor_leave | reset
    WS   /ws?patient=P1001 one agent session per browser tab (see web_session.py)

Run without --reload: MCP launches a subprocess, which needs asyncio's default
(Proactor) event loop on Windows.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import webbrowser

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .clinic_db import apply_doctor_leave_for_patient, get_patient, list_patients, seed, set_fault
from .config import ROOT, Settings, get_settings
from .rag import chunk_document, load_documents
from .runtime import Runtime
from .web_session import WebSession

WEB_DIR = ROOT / "web"


def create_app(settings: Settings | None = None, llm=None) -> Starlette:
    settings = settings or get_settings()
    holder: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with Runtime(settings, llm=llm) as rt:
            holder["rt"] = rt
            holder["tools"] = sorted(t["name"] for t in await rt.mcp.list_tools())
            yield

    async def index(_request: Request):
        return FileResponse(WEB_DIR / "index.html")

    async def info(_request: Request):
        rt: Runtime = holder["rt"]
        return JSONResponse({
            "clinic_name": settings.clinic_name,
            "clinic_now": settings.clinic_now.isoformat(),
            "llm": rt.llm.name,
            "mcp": {"server": rt.mcp.server_name, "tools": holder["tools"]},
            "rag": {"chunks": rt.kb.store.count(), "embedder": rt.kb.embedder.name,
                    "threshold": rt.kb.min_score, "top_k": rt.kb.top_k,
                    "documents": len(load_documents(settings.kb_dir))},
            "patients": list_patients(settings.clinic_db),
        })

    async def kb_search_route(request: Request):
        rt: Runtime = holder["rt"]
        q = request.query_params.get("q", "")
        return JSONResponse({"query": q, "threshold": rt.kb.min_score, "results": rt.kb.scored(q)})

    async def kb_docs(_request: Request):
        docs = load_documents(settings.kb_dir)
        return JSONResponse([{"source": d.source, "title": d.title, "chunks": len(chunk_document(d)),
                              "words": len(d.text.split())} for d in docs])

    async def simulate(request: Request):
        rt: Runtime = holder["rt"]
        body = await request.json()
        event, pid = body.get("event"), body.get("patient")
        try:
            if event == "race":
                set_fault(settings.clinic_db, "race_on_first_booking", 1)
                return JSONResponse({"ok": True, "message": "Armed: the next booking attempt will lose its slot to someone else."})
            if event == "doctor_leave":
                result = apply_doctor_leave_for_patient(settings.clinic_db, pid)
                if not result:
                    return JSONResponse({"ok": False, "message": "This patient has no confirmed appointment to disrupt."})
                return JSONResponse({"ok": True, "message": f"{result['doctor']} is on leave on {result['date']}. "
                                                            f"The clinic cancelled {result['appointment_id']}.", "detail": result})
            if event == "reset":
                seed(settings.clinic_db, settings.clinic_now)
                for p in list_patients(settings.clinic_db):
                    rt.ltm.forget(p["patient_id"], "*")
                return JSONResponse({"ok": True, "message": "Demo world reset: schedule re-seeded and memories cleared."})
        except Exception as exc:  # noqa: BLE001 - e.g. Windows file lock while re-seeding
            return JSONResponse({"ok": False, "message": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse({"ok": False, "message": "Unknown event."}, status_code=400)

    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        rt: Runtime = holder["rt"]
        patient = get_patient(settings.clinic_db, ws.query_params.get("patient", "P1001"))
        if patient is None:
            await ws.send_json({"type": "error", "message": "Unknown patient."})
            await ws.close()
            return
        session = WebSession(agent_factory=rt.new_agent, mcp=rt.mcp, ltm=rt.ltm, patient=patient,
                             log_dir=settings.log_dir)

        async def pump():
            while True:
                await ws.send_json(await session.out.get())

        pump_task = asyncio.create_task(pump())
        try:
            await session.start()
            while True:
                await session.handle(await ws.receive_json())
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - malformed frame etc.: drop the connection, keep the server
            pass
        finally:
            await session.close()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump_task

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/info", info),
            Route("/api/kb/search", kb_search_route),
            Route("/api/kb/docs", kb_docs),
            Route("/api/simulate", simulate, methods=["POST"]),
            WebSocketRoute("/ws", ws_endpoint),
            Mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static"),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="CareBridge web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}"
    print(f"\n  CareBridge is starting -> {url}\n")
    if not args.no_browser:
        import threading
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
