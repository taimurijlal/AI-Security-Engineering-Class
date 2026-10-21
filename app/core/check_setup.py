import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()


def check_ollama():
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen3:4b")
    print(f"Provider: Ollama")
    print(f"Endpoint: {base}")
    print(f"Configured model: {model}")

    try:
        response = requests.get(f"{base}/api/tags", timeout=5)
        response.raise_for_status()
        models = [m.get("name", "") for m in response.json().get("models", [])]
    except Exception as exc:
        print(f"\n[FAIL] Could not reach Ollama: {exc}")
        print("Start Ollama, then run this check again.")
        return 1

    print("\n[PASS] Ollama is reachable.")
    if any(name == model or name.startswith(model + ":") for name in models):
        print(f"[PASS] Model '{model}' is available.")
        return 0

    print(f"[WARN] Model '{model}' was not found.")
    print(f"Installed models: {', '.join(models) if models else 'none'}")
    print(f"Run: ollama pull {model}")
    return 1


def check_openai():
    key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    print("Provider: OpenAI-compatible")
    print(f"Model: {model or 'NOT SET'}")
    if not key:
        print("[FAIL] OPENAI_API_KEY is not configured.")
        return 1
    if not model:
        print("[FAIL] OPENAI_MODEL is not configured.")
        return 1
    print("[PASS] Required configuration is present.")
    print("Run the application to validate live model access.")
    return 0


def main():
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    print("=" * 55)
    print("GLOBALTECH AI — SETUP CHECK")
    print("=" * 55)
    if provider == "ollama":
        code = check_ollama()
    elif provider == "openai":
        code = check_openai()
    else:
        print(f"[FAIL] Unsupported provider: {provider}")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
