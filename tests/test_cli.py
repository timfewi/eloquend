from __future__ import annotations

import unittest

from eloquend.__main__ import _parser


class CommandLineTests(unittest.TestCase):
    def test_private_tuning_values_are_accepted_as_runtime_options(self) -> None:
        args = _parser().parse_args(
            [
                "--backend",
                "tone",
                "--first-chunk-chars",
                "18",
                "--min-chunk-chars",
                "32",
                "--max-chunk-chars",
                "96",
                "--max-wait-ms",
                "120",
                "--segment-queue-size",
                "3",
                "--audio-queue-size",
                "6",
            ]
        )

        self.assertEqual(args.first_chunk_chars, 18)
        self.assertEqual(args.min_chunk_chars, 32)
        self.assertEqual(args.max_chunk_chars, 96)
        self.assertEqual(args.max_wait_ms, 120)
        self.assertEqual(args.segment_queue_size, 3)
        self.assertEqual(args.audio_queue_size, 6)

    def test_public_defaults_match_engine_defaults(self) -> None:
        args = _parser().parse_args(["--backend", "tone"])

        self.assertEqual(args.first_chunk_chars, 24)
        self.assertEqual(args.min_chunk_chars, 40)
        self.assertEqual(args.max_chunk_chars, 140)
        self.assertEqual(args.max_wait_ms, 180)
        self.assertEqual(args.segment_queue_size, 4)
        self.assertEqual(args.audio_queue_size, 8)
