"""Smoke test: the Streamlit Cloud app renders and answers via a fake LLM."""
import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "streamlit not installed")
class StreamlitAppTests(unittest.TestCase):
    def test_app_renders_without_errors(self):
        from streamlit.testing.v1 import AppTest

        os.environ.pop("GROQ_API_KEY", None)
        at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.title[0].value, "📊 AI Analytics Copilot")

    def test_missing_key_shows_clear_error(self):
        from streamlit.testing.v1 import AppTest

        os.environ.pop("GROQ_API_KEY", None)
        at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60).run()
        at.text_area(key="question").input("How many customers do we have?").run()
        next(b for b in at.button if b.label == "Ask").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("GROQ_API_KEY" in e.value for e in at.error))


if __name__ == "__main__":
    unittest.main()
