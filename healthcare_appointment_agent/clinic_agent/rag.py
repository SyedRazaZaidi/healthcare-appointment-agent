"""Retrieval-Augmented Generation pipeline.

    markdown docs --> section-aware chunks --> embeddings --> vector DB (Chroma)
                                                                    |
    question --> embedding --> top-k similarity search --> score filter --> passages

Design decisions worth knowing for the viva:

* We compute embeddings ourselves and hand them to the vector database, so the
  embedding model is a swappable component and ChromaDB is used purely as what
  it is - a persistent vector store with approximate nearest-neighbour search.
* Chunks are split on markdown ``##`` sections first (a section is a natural
  unit of meaning for policy documents) and only then by size, so a passage
  never mixes two unrelated policies. The document title and section heading
  are embedded together with the text, which makes short chunks findable.
* Similarity is re-computed as cosine on the returned vectors, so the "is this
  evidence good enough?" threshold does not depend on the database's distance
  metric. Below the threshold the retriever returns *nothing* and the agent
  must say it has no evidence (grounded refusal instead of hallucination).
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Protocol

import numpy as np

# --------------------------------------------------------------------------
# Documents and chunking
# --------------------------------------------------------------------------


@dataclass
class Document:
    source: str   # file name, used as the citation label
    title: str
    text: str


@dataclass
class Chunk:
    chunk_id: str
    source: str
    title: str
    section: str
    index: int
    text: str

    def embedding_text(self) -> str:
        # Context header: lets a chunk like "Fee: $15" be found by "cancellation fee".
        return f"{self.title} - {self.section}: {self.text}"


def load_documents(kb_dir: str | Path) -> list[Document]:
    docs = []
    for path in sorted(Path(kb_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        m = re.match(r"#\s+(.+)", text)
        title = m.group(1).strip() if m else path.stem.replace("_", " ").title()
        docs.append(Document(source=path.name, title=title, text=text))
    return docs


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (section_heading, body) pairs on '## ' headings."""
    sections, heading, buf = [], "Overview", []
    for line in text.splitlines():
        if line.startswith("# "):
            continue  # document title, handled separately
        if line.startswith("## "):
            if "".join(buf).strip():
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append((heading, "\n".join(buf).strip()))
    return sections


def _pack(paragraphs: list[str], max_chars: int) -> list[str]:
    """Greedily pack paragraphs into pieces of at most ``max_chars``."""
    pieces, cur = [], ""
    for para in paragraphs:
        # A single over-long paragraph is split on sentence boundaries.
        parts = [para] if len(para) <= max_chars else re.split(r"(?<=[.!?])\s+", para)
        for part in parts:
            if cur and len(cur) + len(part) + 2 > max_chars:
                pieces.append(cur)
                cur = part
            else:
                cur = f"{cur}\n\n{part}" if cur else part
    if cur:
        pieces.append(cur)
    return pieces


def chunk_document(doc: Document, max_chars: int = 700, overlap_chars: int = 120) -> list[Chunk]:
    chunks: list[Chunk] = []
    stem = Path(doc.source).stem
    for heading, body in _split_sections(doc.text):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        pieces = _pack(paragraphs, max_chars)
        for i, piece in enumerate(pieces):
            if i > 0 and overlap_chars:
                tail = pieces[i - 1][-overlap_chars:]
                tail = tail[tail.find(" ") + 1:] if " " in tail else tail
                piece = f"...{tail} {piece}"
            chunks.append(
                Chunk(
                    chunk_id=f"{stem}::{len(chunks):03d}",
                    source=doc.source,
                    title=doc.title,
                    section=heading,
                    index=len(chunks),
                    text=piece,
                )
            )
    return chunks


# --------------------------------------------------------------------------
# Embedders
# --------------------------------------------------------------------------


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _l2(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


_STOP = set(
    "a an the and or but if of to in on at by for with from as is are was were be been being do does did "
    "i you he she it we they my your our their me us them this that these those can could will would should "
    "what when where which who how why not no yes there here about into than then so very just".split()
)


class HashEmbedder:
    """Deterministic offline bag-of-words embedder (feature hashing).

    NOT a semantic model: it only captures word overlap. It exists so the test
    suite is reproducible on any machine without downloads. The real pipeline
    uses a neural sentence embedder (see ``build_embedder``).
    """

    name = "hash (offline, lexical)"

    def __init__(self, dim: int = 2048):  # 2048 keeps hash-collision noise below the 0.12 threshold
        self.dim = dim

    @staticmethod
    def _tokens(text: str) -> list[str]:
        out = []
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            if w in _STOP:
                continue
            for suf in ("ing", "ed", "es", "s"):
                if len(w) > 4 and w.endswith(suf):
                    w = w[: -len(suf)]
                    break
            out.append(w)
        return out

    def embed(self, texts: list[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            toks = self._tokens(text)
            feats = Counter(toks) + Counter(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
            for feat, count in feats.items():
                h = int(hashlib.md5(feat.encode()).hexdigest(), 16)
                sign = 1.0 if (h >> 64) & 1 else -1.0
                mat[row, h % self.dim] += sign * (1.0 + math.log(count))
        return _l2(mat)


class ChromaDefaultEmbedder:
    """all-MiniLM-L6-v2 through ONNX runtime (ships with ChromaDB; the model
    file is downloaded once on first use)."""

    name = "all-MiniLM-L6-v2 (ONNX via chromadb)"

    def __init__(self):
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        self._ef = DefaultEmbeddingFunction()

    def embed(self, texts: list[str]) -> np.ndarray:
        return _l2(np.asarray(self._ef(texts), dtype=np.float32))


class SentenceTransformerEmbedder:
    name = "all-MiniLM-L6-v2 (sentence-transformers)"

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model)

    def embed(self, texts: list[str]) -> np.ndarray:
        return _l2(np.asarray(self._model.encode(texts, normalize_embeddings=True), dtype=np.float32))


def build_embedder(backend: str) -> Embedder:
    if backend == "hash":
        return HashEmbedder()
    if backend == "sentence-transformers":
        return SentenceTransformerEmbedder()
    if backend == "chroma-default":
        return ChromaDefaultEmbedder()
    raise ValueError(f"Unknown EMBEDDING_BACKEND '{backend}'")


# --------------------------------------------------------------------------
# Vector stores
# --------------------------------------------------------------------------


class VectorStore(Protocol):
    def upsert(self, chunks: list[Chunk], embeddings: np.ndarray) -> None: ...
    def query(self, embedding: np.ndarray, k: int) -> list[tuple[Chunk, float]]: ...
    def count(self) -> int: ...
    def reset(self) -> None: ...


class ChromaVectorStore:
    """Persistent ChromaDB collection."""

    def __init__(self, path: str | Path, collection: str):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        Path(path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._name = collection
        self._col = self._open()

    def _open(self):
        # No embedding_function: we always pass vectors explicitly. Scores are recomputed as cosine
        # in query(), so the collection's own distance setting is only a ranking hint.
        try:
            return self._client.get_or_create_collection(name=self._name, metadata={"hnsw:space": "cosine"})
        except Exception:  # newer/older Chroma versions may reject this metadata form
            return self._client.get_or_create_collection(name=self._name)

    def upsert(self, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        if not chunks:
            return
        self._col.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[
                {"source": c.source, "title": c.title, "section": c.section, "index": c.index} for c in chunks
            ],
            embeddings=embeddings.tolist(),
        )

    def query(self, embedding: np.ndarray, k: int) -> list[tuple[Chunk, float]]:
        n = self._col.count()
        if n == 0:
            return []
        res = self._col.query(
            query_embeddings=[embedding.tolist()],
            n_results=min(k, n),
            include=["documents", "metadatas", "embeddings", "distances"],
        )
        out = []
        vectors = res.get("embeddings")
        for i, cid in enumerate(res["ids"][0]):
            meta = res["metadatas"][0][i]
            if vectors is not None and len(vectors) and vectors[0] is not None:
                score = float(np.dot(embedding, _l2(np.asarray(vectors[0][i], dtype=np.float32)[None, :])[0]))
            else:  # fall back to the cosine distance reported by the DB
                score = 1.0 - float(res["distances"][0][i])
            out.append(
                (
                    Chunk(
                        chunk_id=cid,
                        source=meta["source"],
                        title=meta["title"],
                        section=meta["section"],
                        index=int(meta["index"]),
                        text=res["documents"][0][i],
                    ),
                    score,
                )
            )
        return sorted(out, key=lambda t: -t[1])

    def count(self) -> int:
        return self._col.count()

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._name)
        except Exception:  # collection may not exist yet
            pass
        self._col = self._open()


# --------------------------------------------------------------------------
# Knowledge base facade
# --------------------------------------------------------------------------


@dataclass
class IngestReport:
    documents: int
    chunks: int
    embedder: str
    sources: list[str]


@dataclass
class Passage:
    rank: int
    score: float
    source: str
    title: str
    section: str
    text: str


class KnowledgeBase:
    def __init__(self, store: VectorStore, embedder: Embedder, top_k: int = 4, min_score: float = 0.25):
        self.store = store
        self.embedder = embedder
        self.top_k = top_k
        self.min_score = min_score

    def ingest(self, kb_dir: str | Path, reset: bool = False) -> IngestReport:
        docs = load_documents(kb_dir)
        if not docs:
            raise FileNotFoundError(f"No .md documents found in {kb_dir}")
        if reset:
            self.store.reset()
        chunks = [c for d in docs for c in chunk_document(d)]
        self.store.upsert(chunks, self.embedder.embed([c.embedding_text() for c in chunks]))
        return IngestReport(len(docs), len(chunks), self.embedder.name, [d.source for d in docs])

    def ensure_ingested(self, kb_dir: str | Path) -> None:
        if self.store.count() == 0:
            self.ingest(kb_dir)

    def retrieve(self, query: str, k: int | None = None, min_score: float | None = None) -> list[Passage]:
        query = (query or "").strip()
        if not query:
            return []
        qvec = self.embedder.embed([query])[0]
        hits = self.store.query(qvec, k or self.top_k)
        threshold = self.min_score if min_score is None else min_score
        passages = []
        for rank, (chunk, score) in enumerate((h for h in hits if h[1] >= threshold), start=1):
            passages.append(
                Passage(rank, round(score, 3), chunk.source, chunk.title, chunk.section, chunk.text)
            )
        return passages

    def scored(self, query: str, k: int = 6) -> list[dict]:
        """Raw top-k with similarity scores, each flagged by whether it clears the evidence
        threshold. Used by the UI's knowledge-base visualiser."""
        query = (query or "").strip()
        if not query:
            return []
        hits = self.store.query(self.embedder.embed([query])[0], k)
        return [
            {"source": c.source, "title": c.title, "section": c.section, "text": c.text,
             "score": round(score, 3), "above": score >= self.min_score}
            for c, score in hits
        ]

    @staticmethod
    def as_tool_result(passages: list[Passage]) -> dict:
        if not passages:
            return {
                "found": False,
                "passages": [],
                "note": "No sufficiently relevant passages in the clinic knowledge base. "
                        "Do not answer from general knowledge; tell the patient you could not find "
                        "this in the clinic's documents and suggest the front desk.",
            }
        return {"found": True, "passages": [asdict(p) for p in passages]}


class InMemoryVectorStore:
    """Brute-force cosine store used by unit tests when ChromaDB is not installed."""

    def __init__(self):
        self._chunks: dict[str, Chunk] = {}
        self._vecs: dict[str, np.ndarray] = {}

    def upsert(self, chunks, embeddings):
        for c, v in zip(chunks, embeddings):
            self._chunks[c.chunk_id] = c
            self._vecs[c.chunk_id] = v

    def query(self, embedding, k):
        scored = [(self._chunks[i], float(np.dot(embedding, v))) for i, v in self._vecs.items()]
        return sorted(scored, key=lambda t: -t[1])[:k]

    def count(self):
        return len(self._chunks)

    def reset(self):
        self._chunks.clear()
        self._vecs.clear()
