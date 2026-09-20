from __future__ import annotations

import unittest

from eloquend.segmenter import IncrementalSegmenter, SegmenterConfig


class IncrementalSegmenterTests(unittest.TestCase):
    def test_sentence_punctuation_flushes_immediately(self) -> None:
        segmenter = IncrementalSegmenter()
        self.assertEqual(segmenter.append("Das ist sofort bereit."), [
            "Das ist sofort bereit."
        ])

    def test_token_fragments_are_preserved(self) -> None:
        segmenter = IncrementalSegmenter()
        self.assertEqual(segmenter.append("Das ist "), [])
        self.assertEqual(segmenter.append("ein Test! Weiter"), [
            "Das ist ein Test!"
        ])
        self.assertEqual(segmenter.flush(), "Weiter")

    def test_deadline_respects_first_chunk_minimum(self) -> None:
        segmenter = IncrementalSegmenter(
            SegmenterConfig(
                first_chunk_chars=10,
                min_chunk_chars=12,
                max_chunk_chars=30,
                max_wait_ms=10,
            )
        )
        segmenter.append("zu kurz")
        self.assertIsNone(segmenter.flush_due())
        segmenter.append(" aber jetzt")
        self.assertEqual(segmenter.flush_due(), "zu kurz aber jetzt")

    def test_maximum_chunk_size_forces_a_split(self) -> None:
        segmenter = IncrementalSegmenter(
            SegmenterConfig(
                first_chunk_chars=5,
                min_chunk_chars=5,
                max_chunk_chars=12,
            )
        )
        chunks = segmenter.append("eins zwei drei vier")
        self.assertEqual(chunks, ["eins zwei"])
        self.assertEqual(segmenter.flush(), "drei vier")

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SegmenterConfig(first_chunk_chars=20, max_chunk_chars=10)
