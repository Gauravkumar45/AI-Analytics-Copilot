#!/usr/bin/env python3
"""Simple main entrypoint for the analytics copilot project."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ENV_PATH = Path(__file__).with_name(".env")
DEFAULTS = {
    "DATABASE_URL": "postgresql://admin:password@localhost:5432/analytics",
    "OLLAMA_MODEL": "qwen2.5:7b",
    "OLLAMA_HOST": "http://localhost:11434",
    "JWT_ALGORITHM": "HS256",
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
        key = key.strip()
        value = value.strip().strip("\"'")
        values[key] = value

    return values


env = {**DEFAULTS, **load_dotenv(ENV_PATH), **os.environ}


def mask_value(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 6:
        return "*" * len(value)
    return value[:3] + "..." + value[-3:]


def summary() -> str:
    lines = [
        "Loaded configuration:",
        f"  DATABASE_URL: {mask_value(env.get('DATABASE_URL', ''))}",
        f"  OLLAMA_MODEL: {env.get('OLLAMA_MODEL')}",
        f"  OLLAMA_HOST: {env.get('OLLAMA_HOST')}",
        f"  JWT_ALGORITHM: {env.get('JWT_ALGORITHM')}",
    ]
    return "\n".join(lines)


def build_ollama_url(host: str) -> str:
    host = host.rstrip("/")
    return f"{host}/v1/chat/completions"


def query_ollama(prompt: str, model: str, host: str, timeout: int = 30) -> dict[str, Any]:
    url = build_ollama_url(host)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Ollama request failed: {exc.code} {exc.reason}\n{message}"
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to connect to Ollama at {host}: {exc.reason}")


def extract_model_text(response: dict[str, Any]) -> str:
    if not isinstance(response, dict):
        return "<unexpected response>"

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message")
        if isinstance(message, dict):
            return message.get("content", "")

    return json.dumps(response, indent=2)


def run_check_ollama() -> int:
    host = env.get("OLLAMA_HOST", DEFAULTS["OLLAMA_HOST"])
    model = env.get("OLLAMA_MODEL", DEFAULTS["OLLAMA_MODEL"])
    print("Checking Ollama status...")
    try:
        result = query_ollama(
            prompt="Say hello from the AI Analytics Copilot.",
            model=model,
            host=host,
        )
        text = extract_model_text(result)
        print("Ollama responded successfully.")
        print("---")
        print(text)
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analytics copilot main entrypoint.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print the loaded environment configuration.",
    )
    parser.add_argument(
        "--check-ollama",
        action="store_true",
        help="Ping the configured Ollama host with a test prompt.",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        help="Send a prompt to the configured Ollama model.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds for Ollama calls.",
    )
    args = parser.parse_args()

    if args.show_config:
        print(summary())

    if args.check_ollama:
        return run_check_ollama()

    if args.prompt:
        host = env.get("OLLAMA_HOST", DEFAULTS["OLLAMA_HOST"])
        model = env.get("OLLAMA_MODEL", DEFAULTS["OLLAMA_MODEL"])
        try:
            response = query_ollama(args.prompt, model=model, host=host, timeout=args.timeout)
            print(extract_model_text(response))
            return 0
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
