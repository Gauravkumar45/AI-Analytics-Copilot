import io
import unittest
import urllib.error
from unittest.mock import patch

from app.services.ai_service import AiService, OllamaResponseError


class AiServiceTests(unittest.TestCase):
    def test_query_model_surfaces_missing_model_guidance(self) -> None:
        service = AiService()
        payload = b'{"error":{"message":"model \'qwen2.5:7b\' not found"}}'
        http_error = urllib.error.HTTPError(
            url="http://ollama:11434/v1/chat/completions",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(payload),
        )

        with patch("app.services.ai_service.urllib.request.urlopen", side_effect=http_error):
            with self.assertRaisesRegex(RuntimeError, "pull"):
                service.query_model("hello")

    def test_extract_sql_removes_markdown_fence(self) -> None:
        content = "Here is the query:\n```sql\nSELECT * FROM events;\n```\n"

        self.assertEqual(AiService.extract_sql(content), "SELECT * FROM events")

    def test_extract_sql_rejects_explanation_without_a_query(self) -> None:
        with self.assertRaises(OllamaResponseError):
            AiService.extract_sql("I need more information about the table.")

    def test_build_sql_prompt_includes_available_schema(self) -> None:
        prompt = AiService().build_sql_prompt(
            "Show total sales", schema={"orders": ["id", "amount"]}
        )

        self.assertIn("orders(id, amount)", prompt)


if __name__ == "__main__":
    unittest.main()
