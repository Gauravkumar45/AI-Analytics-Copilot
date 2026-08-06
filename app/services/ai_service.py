import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class OllamaModelError(RuntimeError):
    """Raised when the configured Ollama model cannot be reached."""


class OllamaResponseError(RuntimeError):
    """Raised when Ollama does not return an executable read-only SQL query."""


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULTS = {
    "OLLAMA_HOST": "http://localhost:11434",
    "OLLAMA_MODEL": "qwen2.5:7b",
    "OLLAMA_TIMEOUT_SECONDS": "180",
}


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


env = {**DEFAULTS, **load_dotenv(ENV_PATH), **os.environ}


class AiService:
    def __init__(self) -> None:
        self.host = env["OLLAMA_HOST"]
        self.model = env["OLLAMA_MODEL"]
        try:
            self.timeout_seconds = int(env["OLLAMA_TIMEOUT_SECONDS"])
        except ValueError as exc:
            raise ValueError("OLLAMA_TIMEOUT_SECONDS must be a whole number of seconds") from exc
        if self.timeout_seconds <= 0:
            raise ValueError("OLLAMA_TIMEOUT_SECONDS must be greater than zero")

    def build_sql_prompt(
        self,
        question: str,
        table_name: str | None = None,
        time_range: str | None = None,
        schema: dict[str, list[str]] | None = None,
    ) -> str:
        context = [
            "You are an analytics assistant.",
            "Translate the user question into a valid SQL query for PostgreSQL.",
        ]
        if table_name:
            context.append(f"Target table: {table_name}.")
        if time_range:
            context.append(f"Time range: {time_range}.")
        if schema:
            schema_lines = [f"{table}({', '.join(columns)})" for table, columns in schema.items()]
            context.append(f"Available database schema: {'; '.join(schema_lines)}.")
        context.append(
            "Return exactly one PostgreSQL SELECT query. Do not include explanations, markdown, "
            "placeholders, or code fences. Use only the tables and columns in the available schema. "
            "Use only numeric columns for SUM, AVG, MIN, MAX, and arithmetic."
        )
        context.append(f"User question: {question}")
        return "\n".join(context)

    def query_model(self, prompt: str, timeout: int | None = None) -> dict[str, Any]:
        url = self.host.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 128,
            "stream": False,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            if "model" in error_body and "not found" in error_body.lower():
                raise OllamaModelError(
                    f"Ollama model '{self.model}' is not available. Pull it with: ollama pull {self.model}"
                ) from exc
            raise RuntimeError(f"Ollama HTTP error: {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama connection error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"Ollama request timed out after {timeout or self.timeout_seconds} seconds"
            ) from exc

    def generate_sql(self, prompt: str) -> str:
        response = self.query_model(prompt)
        choices = response.get("choices", [])
        if not choices:
            raise RuntimeError("Empty response from Ollama")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str):
            raise OllamaResponseError("Ollama returned a non-text SQL response")
        return self.extract_sql(content)

    @staticmethod
    def extract_sql(content: str) -> str:
        """Extract one read-only SQL statement from a model response."""
        fenced_query = re.search(r"```(?:sql)?\s*(.*?)```", content, flags=re.IGNORECASE | re.DOTALL)
        query = (fenced_query.group(1) if fenced_query else content).strip()

        if not re.match(r"^(SELECT|WITH)\b", query, flags=re.IGNORECASE):
            raise OllamaResponseError("Ollama did not return a SQL SELECT query")

        # Permit one optional final delimiter, but never execute multiple statements.
        query = query.rstrip()
        if query.endswith(";"):
            query = query[:-1].rstrip()
        if ";" in query:
            raise OllamaResponseError("Ollama returned multiple SQL statements")
        return query
