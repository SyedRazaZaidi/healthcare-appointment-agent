"""LLM access behind one small interface.

The agent talks to ``LLMClient.chat()`` and never to a vendor SDK directly, so
the model can be swapped with a single environment variable:

* ``LLM_PROVIDER=anthropic`` - Claude via the Anthropic Messages API.
* ``LLM_PROVIDER=openai``    - any OpenAI-compatible Chat Completions endpoint.
  Set ``OPENAI_BASE_URL`` to use Gemini, Groq, OpenRouter, Ollama, etc.

Internally the conversation is stored in one provider-neutral format (the
Anthropic "content blocks" shape):

    {"role": "user" | "assistant", "content": str | list[block]}
    block = {"type": "text", "text": ...}
          | {"type": "tool_use", "id": ..., "name": ..., "input": {...}}
          | {"type": "tool_result", "tool_use_id": ..., "content": str, "is_error": bool}

``OpenAICompatLLM`` converts to and from that shape.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class ConfigError(RuntimeError):
    """Raised when required configuration (e.g. an API key) is missing."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    blocks: list[dict]  # assistant content in the neutral block format
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    name: str

    async def chat(
        self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------
class AnthropicLLM:
    def __init__(self, model: str, api_key: str, temperature: float | None = None):
        if not api_key:
            raise ConfigError("ANTHROPIC_API_KEY is not set (see .env.example).")
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.name = f"anthropic:{model}"

    async def chat(self, *, system, messages, tools, max_tokens) -> LLMResponse:
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools  # already {"name", "description", "input_schema"}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = await self.client.messages.create(**kwargs)

        blocks, calls, text_parts = [], [], []
        for b in resp.content:
            if b.type == "text" and b.text.strip():
                blocks.append({"type": "text", "text": b.text})
                text_parts.append(b.text)
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                calls.append(ToolCall(b.id, b.name, dict(b.input)))
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return LLMResponse("\n".join(text_parts).strip(), calls, blocks, resp.stop_reason or "", usage)


# --------------------------------------------------------------------------
# OpenAI-compatible
# --------------------------------------------------------------------------
def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "\n".join(b["text"] for b in content if b["type"] == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                    # Gemini 3 requires its opaque 'thought_signature' to be echoed back on tool calls.
                    **({"extra_content": b["extra_content"]} if b.get("extra_content") else {}),
                }
                for b in content
                if b["type"] == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user message made of tool_result blocks (and possibly text)
            for b in content:
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out



def clean_schema(node):
    """Make a JSON Schema acceptable to stricter OpenAI-compatible backends (e.g. Gemini).

    FastMCP/pydantic emit 'title', 'default', 'additionalProperties' and Optional types as
    anyOf:[X, null]. Some providers reject these, so we drop the noise and collapse Optional[X] to X.
    """
    if isinstance(node, list):
        return [clean_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    node = {k: v for k, v in node.items() if k not in ("title", "default", "additionalProperties", "$schema")}
    alts = node.get("anyOf")
    if alts:
        non_null = [a for a in alts if not (isinstance(a, dict) and a.get("type") == "null")]
        if len(non_null) == 1:
            merged = {k: v for k, v in node.items() if k != "anyOf"}
            merged.update(clean_schema(non_null[0]))
            return merged
    return {k: (clean_schema(v) if k != "properties" else {pk: clean_schema(pv) for pk, pv in v.items()})
            for k, v in node.items()}


class OpenAICompatLLM:
    def __init__(self, model: str, api_key: str, base_url: str | None = None, temperature: float | None = None):
        if not api_key:
            raise ConfigError("OPENAI_API_KEY is not set (see .env.example).")
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.name = f"openai-compatible:{model}"

    async def chat(self, *, system, messages, tools, max_tokens) -> LLMResponse:
        kwargs: dict[str, Any] = dict(
            model=self.model, messages=to_openai_messages(system, messages), max_tokens=max_tokens
        )
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t["name"], "description": t["description"], "parameters": clean_schema(t["input_schema"])},
                }
                for t in tools
            ]
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = await self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        blocks, calls = [], []
        text = (msg.content or "").strip()
        if text:
            blocks.append({"type": "text", "text": text})
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            block = {"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": args}
            extra = getattr(tc, "extra_content", None)
            if extra is None:  # pydantic keeps unknown response fields in model_extra
                extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
            if extra:
                block["extra_content"] = extra  # e.g. {"google": {"thought_signature": "..."}}
            blocks.append(block)
            calls.append(ToolCall(tc.id, tc.function.name, args))
        usage = {}
        if resp.usage:
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
        return LLMResponse(text, calls, blocks, resp.choices[0].finish_reason or "", usage)


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def build_llm(settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(settings.llm_model, settings.anthropic_api_key, settings.llm_temperature)
    if settings.llm_provider == "openai":
        return OpenAICompatLLM(
            settings.llm_model, settings.openai_api_key, settings.openai_base_url, settings.llm_temperature
        )
    raise ConfigError(f"Unknown LLM_PROVIDER '{settings.llm_provider}' (use 'anthropic' or 'openai').")


# --------------------------------------------------------------------------
# Test double - used ONLY by the unit tests, never by the demo or the CLI.
# --------------------------------------------------------------------------
def text_response(text: str) -> LLMResponse:
    return LLMResponse(text, [], [{"type": "text", "text": text}], "end_turn")


def tool_response(*calls: tuple[str, dict], text: str = "") -> LLMResponse:
    blocks: list[dict] = [{"type": "text", "text": text}] if text else []
    tool_calls = []
    for name, args in calls:
        call = ToolCall(f"toolu_{uuid.uuid4().hex[:12]}", name, args)
        tool_calls.append(call)
        blocks.append({"type": "tool_use", "id": call.id, "name": name, "input": args})
    return LLMResponse(text, tool_calls, blocks, "tool_use")


class ScriptedLLM:
    """Returns pre-programmed responses and records every request it receives.

    Lets the tests verify the *plumbing* (tool loop, confirmation gate, memory
    injection, replanning hints) deterministically, without a network call.
    Items in ``script`` may be an ``LLMResponse``, an ``Exception`` to raise, or
    a callable ``f(call_record) -> LLMResponse`` for responses that depend on
    what the agent sent.
    """

    name = "scripted-test-llm"

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    async def chat(self, *, system, messages, tools, max_tokens) -> LLMResponse:
        record = {"system": system, "messages": copy.deepcopy(messages), "tools": [t["name"] for t in tools]}
        self.calls.append(record)
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(record)
        return item
