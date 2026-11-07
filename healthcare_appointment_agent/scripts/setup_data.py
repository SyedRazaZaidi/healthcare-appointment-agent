"""One-time setup: seed the simulated clinic database and ingest the knowledge base.

    python scripts/setup_data.py            # both
    python scripts/setup_data.py --kb-only
    python scripts/setup_data.py --db-only
"""
import _path  # noqa: F401
import argparse

from clinic_agent.clinic_db import seed
from clinic_agent.config import get_settings
from clinic_agent.rag import ChromaVectorStore, KnowledgeBase, build_embedder


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-only", action="store_true")
    ap.add_argument("--db-only", action="store_true")
    args = ap.parse_args()
    s = get_settings()

    if not args.kb_only:
        seed(s.clinic_db, s.clinic_now)
        print(f"[db]  seeded synthetic clinic database -> {s.clinic_db}  (clinic time {s.clinic_now})")

    if not args.db_only:
        embedder = build_embedder(s.embedding_backend)
        store = ChromaVectorStore(s.chroma_dir, s.collection)
        kb = KnowledgeBase(store, embedder, top_k=s.rag_top_k, min_score=s.rag_min_score)
        report = kb.ingest(s.kb_dir, reset=True)
        (s.chroma_dir / "embedder.txt").write_text(embedder.name)
        print(f"[rag] embedder : {report.embedder}")
        print(f"[rag] ingested : {report.documents} documents -> {report.chunks} chunks in ChromaDB ({s.chroma_dir})")
        for src in report.sources:
            print(f"        - {src}")


if __name__ == "__main__":
    main()
