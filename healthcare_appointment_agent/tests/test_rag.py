"""RAG pipeline tests: ingestion, chunking, retrieval relevance, grounded refusal."""
import tempfile
import unittest
from pathlib import Path

from helpers import KB_DIR, World  # noqa: F401  (also puts project root on sys.path)

from clinic_agent.rag import (HashEmbedder, InMemoryVectorStore, KnowledgeBase, chunk_document, load_documents)

try:
    import chromadb  # noqa: F401
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

# (query, source file that must appear in the top-2 results)
RELEVANCE_CASES = [
    ("how much is the late cancellation fee", "02_cancellation_and_rescheduling_policy.md"),
    ("can a new patient book a dermatologist", "03_new_patient_registration.md"),
    ("which insurance companies do you accept", "04_fees_insurance_and_payments.md"),
    ("can I have a telehealth video appointment", "07_telehealth_visit_guide.md"),
    ("what time does the clinic open on Saturday", "09_clinic_hours_contact_and_holidays.md"),
    ("what should I do if I have chest pain", "08_emergency_and_urgent_care_guidelines.md"),
    ("does the assistant store my diagnosis in memory", "10_privacy_and_data_policy.md"),
    ("how many appointments can I hold at once", "01_appointment_booking_policy.md"),
    ("which doctors work in cardiology and is a referral needed", "05_department_and_doctor_directory.md"),
    ("should I fast before my visit", "06_preparing_for_your_visit.md"),
]


class IngestionTests(unittest.TestCase):
    def test_at_least_five_documents_are_loaded(self):
        docs = load_documents(KB_DIR)
        self.assertGreaterEqual(len(docs), 5)
        self.assertEqual(len({d.source for d in docs}), len(docs))

    def test_chunking_is_section_aware_and_bounded(self):
        for doc in load_documents(KB_DIR):
            chunks = chunk_document(doc)
            self.assertGreaterEqual(len(chunks), 3, doc.source)
            for c in chunks:
                self.assertTrue(c.section)
                self.assertLessEqual(len(c.text), 700 + 200, f"{c.chunk_id} too large")

    def test_long_paragraph_is_split_with_overlap(self):
        from clinic_agent.rag import Document
        sentence = "This is a fairly long sentence about clinic policy and procedure. "
        doc = Document("x.md", "X", "# X\n\n## Section\n" + sentence * 30)
        chunks = chunk_document(doc, max_chars=300, overlap_chars=60)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(chunks[1].text.startswith("..."))

    def test_ingest_is_idempotent(self):
        kb = KnowledgeBase(InMemoryVectorStore(), HashEmbedder())
        first = kb.ingest(KB_DIR)
        count = kb.store.count()
        kb.ingest(KB_DIR)
        self.assertEqual(kb.store.count(), count)
        self.assertGreaterEqual(first.documents, 5)


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = KnowledgeBase(InMemoryVectorStore(), HashEmbedder(), top_k=4, min_score=0.12)
        cls.kb.ingest(KB_DIR)

    def test_relevant_source_is_in_top_two(self):
        misses = []
        for query, expected in RELEVANCE_CASES:
            sources = [p.source for p in self.kb.retrieve(query, k=2)]
            if expected not in sources:
                misses.append((query, expected, sources))
        self.assertEqual(misses, [], f"retrieval misses: {misses}")

    def test_top_hit_carries_citation_fields_and_score(self):
        top = self.kb.retrieve("late cancellation fee")[0]
        self.assertTrue(top.source.endswith(".md"))
        self.assertTrue(top.section)
        self.assertGreater(top.score, 0.12)

    def test_off_topic_query_returns_no_evidence(self):
        for query in ["best recipe for chocolate cake", "who won the football world cup", "bitcoin price today"]:
            self.assertEqual(self.kb.retrieve(query), [], query)
        result = KnowledgeBase.as_tool_result([])
        self.assertFalse(result["found"])
        self.assertIn("front desk", result["note"])

    def test_empty_query_is_safe(self):
        self.assertEqual(self.kb.retrieve("   "), [])


def _neural_kb():
    """Real sentence embedder + real ChromaDB; None if unavailable (no chromadb / model not downloadable)."""
    if not HAS_CHROMA:
        return None, None
    try:
        from clinic_agent.rag import ChromaDefaultEmbedder, ChromaVectorStore
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows keeps Chroma files locked
        kb = KnowledgeBase(ChromaVectorStore(Path(tmp.name), "neural_kb"), ChromaDefaultEmbedder(),
                           min_score=0.25)
        kb.ingest(KB_DIR, reset=True)
        return kb, tmp
    except Exception:  # model download blocked, offline, etc.
        return None, None


class NeuralRetrievalTests(unittest.TestCase):
    """Semantic retrieval with the production embedder (all-MiniLM-L6-v2) on ChromaDB.
    Skipped automatically when ChromaDB or the model file is unavailable."""

    @classmethod
    def setUpClass(cls):
        cls.kb, cls._tmp = _neural_kb()
        if cls.kb is None:
            raise unittest.SkipTest("chromadb / neural embedding model not available")

    @classmethod
    def tearDownClass(cls):
        if cls._tmp:
            cls._tmp.cleanup()

    def test_paraphrased_queries_hit_the_right_document_in_top_three(self):
        cases = RELEVANCE_CASES + [
            ("do you offer video visits", "07_telehealth_visit_guide.md"),
            ("what happens if I skip my appointment without telling you", "02_cancellation_and_rescheduling_policy.md"),
        ]
        misses = [(q, e) for q, e in cases if e not in [p.source for p in self.kb.retrieve(q, k=3)]]
        self.assertEqual(misses, [])

    def test_unrelated_query_is_rejected_by_threshold(self):
        self.assertEqual(self.kb.retrieve("best recipe for chocolate cake"), [])


@unittest.skipUnless(HAS_CHROMA, "chromadb not installed")
class ChromaIntegrationTests(unittest.TestCase):
    """Same pipeline against a real persistent ChromaDB collection."""

    def test_chroma_roundtrip_and_persistence(self):
        from clinic_agent.rag import ChromaVectorStore
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows keeps Chroma files locked
            kb = KnowledgeBase(ChromaVectorStore(Path(tmp), "test_kb"), HashEmbedder(), min_score=0.12)
            report = kb.ingest(KB_DIR, reset=True)
            self.assertGreaterEqual(report.documents, 5)
            self.assertEqual(kb.store.count(), report.chunks)
            top = kb.retrieve("how much is the late cancellation fee", k=2)
            self.assertIn("02_cancellation_and_rescheduling_policy.md", [p.source for p in top])
            # A second client on the same folder sees the persisted vectors (no re-ingest).
            kb2 = KnowledgeBase(ChromaVectorStore(Path(tmp), "test_kb"), HashEmbedder(), min_score=0.12)
            self.assertEqual(kb2.store.count(), report.chunks)
            self.assertEqual(kb2.retrieve("best recipe for chocolate cake"), [])


if __name__ == "__main__":
    unittest.main()
