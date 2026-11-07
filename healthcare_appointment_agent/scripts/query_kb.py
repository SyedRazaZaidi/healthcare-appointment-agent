"""Inspect retrieval WITHOUT the LLM (evidence for the RAG requirement).

    python scripts/query_kb.py "how much is the late cancellation fee"
"""
import _path  # noqa: F401
import sys

from clinic_agent.config import get_settings
from clinic_agent.runtime import build_knowledge_base


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('usage: python scripts/query_kb.py "your question"')
    s = get_settings()
    kb = build_knowledge_base(s)
    query = " ".join(sys.argv[1:])
    passages = kb.retrieve(query)
    print(f"query: {query!r}   embedder: {kb.embedder.name}   threshold: {kb.min_score}")
    if not passages:
        print("-> NO passage above the similarity threshold (the agent must say it has no evidence)")
    for p in passages:
        print(f"\n#{p.rank}  score={p.score}  {p.source}  [{p.section}]\n    {p.text[:300]}")


if __name__ == "__main__":
    main()
