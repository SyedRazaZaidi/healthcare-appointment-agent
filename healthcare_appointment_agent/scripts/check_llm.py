"""Quick LLM connectivity check - run this BEFORE the demo.

    python scripts/check_llm.py

Prints the effective configuration (API key masked), lists the models your key can use
(OpenAI-compatible providers) and sends one tiny test message.
"""
import _path  # noqa: F401
import asyncio

from clinic_agent.config import get_settings
from clinic_agent.llm import build_llm


async def main() -> None:
    s = get_settings()
    key = s.openai_api_key if s.llm_provider == "openai" else s.anthropic_api_key
    masked = f"{key[:4]}...{key[-3:]} ({len(key)} chars)" if key else "NOT SET"
    print(f"provider : {s.llm_provider}")
    print(f"model    : {s.llm_model}")
    print(f"base_url : {s.openai_base_url or '(provider default)'}")
    print(f"api key  : {masked}\n")
    llm = build_llm(s)

    if s.llm_provider == "openai":
        try:
            page = await llm.client.models.list()
            names = sorted(m.id for m in page.data)
            print(f"models available to this key ({len(names)}):")
            for n in names[:40]:
                print("   ", n)
            print()
        except Exception as exc:  # noqa: BLE001
            print(f"could not list models: {type(exc).__name__}: {exc}\n")

    try:
        reply = await llm.chat(system="Reply with the single word OK.",
                               messages=[{"role": "user", "content": "ping"}], tools=[], max_tokens=20)
        print(f"CHAT OK -> {reply.text!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"CHAT FAILED: {type(exc).__name__} (HTTP {getattr(exc, 'status_code', '?')}): {exc}")
        resp = getattr(exc, "response", None)
        if resp is not None:
            print("url :", getattr(getattr(exc, "request", None), "url", "?"))
            print("body:", str(getattr(resp, "text", ""))[:500])


if __name__ == "__main__":
    asyncio.run(main())
