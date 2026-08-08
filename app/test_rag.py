import json
import unittest
from unittest.mock import patch

import rag


class RagTest(unittest.TestCase):
    def setUp(self):
        self.cases = [{
            "id": "bug_001", "error_message": "'NoneType' object has no attribute 'strip'",
            "context": "CSV name cleanup", "root_cause": "Missing None validation", "environment": "Python 3.11",
            "occurrence_count": 1, "last_seen_at": "2026-08-01T00:00:00+00:00",
        }, {
            "id": "bug_002", "error_message": "'user_id'", "context": "webhook payload",
            "root_cause": "Missing key", "environment": "Python 3.11", "occurrence_count": 1,
        }]
        rag._cases = None
        rag._embeddings = None

    def tearDown(self):
        rag._cases = None
        rag._embeddings = None

    def test_builds_query_and_document(self):
        self.assertIn("strip", rag.build_query({"error_text": "AttributeError", "code_snippet": "name.strip()"}))
        self.assertIn("Missing None validation", rag.build_document(self.cases[0]))

    @patch.object(rag, "_rerank")
    @patch.object(rag, "_embed_texts")
    def test_search_maps_relevance_score_to_contract(self, embed, rerank):
        rag._cases = self.cases
        rag._embeddings = [[1.0, 0.0], [0.0, 1.0]]
        embed.return_value = [[1.0, 0.0]]
        rerank.return_value = [{"index": 0, "relevance_score": 0.91}, {"index": 1, "relevance_score": 0.12}]
        results = rag.search_bug_corpus("None strip", top_k=2)
        self.assertEqual(results[0]["id"], "bug_001")
        self.assertEqual(results[0]["reranker_score"], 0.91)

    @patch.object(rag, "_rebuild_index")
    @patch.object(rag, "_write_cases")
    @patch.object(rag, "_load_cases")
    def test_confirmed_upsert_updates_existing_case(self, load_cases, write_cases, _rebuild):
        load_cases.return_value = self.cases
        rag.upsert_bug_case({}, "confirmed", "bug_001")
        self.assertEqual(len(self.cases), 2)
        self.assertEqual(self.cases[0]["occurrence_count"], 2)
        write_cases.assert_called_once_with(self.cases)

    @patch.object(rag, "_rebuild_index")
    @patch.object(rag, "_write_cases")
    @patch.object(rag, "_load_cases")
    def test_possible_upsert_creates_new_case(self, load_cases, write_cases, _rebuild):
        load_cases.return_value = self.cases
        rag.upsert_bug_case({"error_message": "new failure", "occurrence_count": 1}, "possible", "bug_001")
        self.assertEqual(len(self.cases), 3)
        self.assertEqual(self.cases[-1]["id"], "bug_003")
        write_cases.assert_called_once_with(self.cases)


if __name__ == "__main__":
    unittest.main()
