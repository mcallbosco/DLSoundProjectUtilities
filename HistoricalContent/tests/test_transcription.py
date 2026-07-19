from __future__ import annotations

import unittest

from HistoricalContent.historical_content.transcription import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
)


class TranscriptionConfigurationTests(unittest.TestCase):
    def test_supported_models_include_gpt_4o_mini_transcribe(self):
        self.assertEqual(DEFAULT_MODEL, "gpt-4o-transcribe")
        self.assertIn("gpt-4o-mini-transcribe", SUPPORTED_MODELS)


if __name__ == "__main__":
    unittest.main()
