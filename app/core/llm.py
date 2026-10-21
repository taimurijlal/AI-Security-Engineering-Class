import os
from typing import Protocol

import requests
from dotenv import load_dotenv

load_dotenv()


class LLMError(RuntimeError):
    pass


class Provider(Protocol):
    def generate(self, system_prompt: str, user_prompt: str, context: str) -> str:
        ...

    def health_check(self) -> tuple[bool, str]:
        ...


class OllamaProvider:
    PROVIDER_NAME = "Ollama"

    def __init__(self):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:4b")

    def health_check(self) -> tuple[bool, str]:
        """Lightweight connectivity check. Does not call the chat endpoint."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
        except requests.RequestException:
            return False, f"Could not reach Ollama at {self.base_url}."

        if any(name == self.model or name.startswith(self.model + ":") for name in models):
            return True, f"Ollama is reachable and '{self.model}' is installed."
        return False, f"Ollama is reachable, but model '{self.model}' is not installed."

    def generate(self, system_prompt: str, user_prompt: str, context: str) -> str:
        combined_system = (
            f"{system_prompt}\n\n"
            "The application supplied the following company context:\n"
            "----- BEGIN COMPANY CONTEXT -----\n"
            f"{context}\n"
            "----- END COMPANY CONTEXT -----"
        )

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": combined_system},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    # Disable reasoning output on thinking models such as qwen3.
                    "think": False,
                    "options": {"temperature": 0.2},
                },
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"].strip()
        except requests.RequestException as exc:
            raise LLMError(
                "Could not reach Ollama. Make sure Ollama is running and the "
                f"model '{self.model}' is installed."
            ) from exc
        except (KeyError, ValueError) as exc:
            raise LLMError("Ollama returned an unexpected response.") from exc


class OpenAIProvider:
    PROVIDER_NAME = "OpenAI-compatible"

    def __init__(self):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMError("The openai package is not installed.") from exc

        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL")
        # NOTE: a blank OPENAI_BASE_URL="" in .env still sets the environment
        # variable (just to an empty string). The openai SDK's own fallback
        # only checks `is None`, so an empty string is passed through as a
        # literal (invalid) base URL instead of the real default. Resolving
        # a concrete URL ourselves avoids relying on that fallback at all.
        base_url = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"

        if not api_key:
            raise LLMError("OPENAI_API_KEY is not configured.")
        if not model:
            raise LLMError("OPENAI_MODEL is not configured.")

        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate(self, system_prompt: str, user_prompt: str, context: str) -> str:
        combined_system = (
            f"{system_prompt}\n\n"
            "The application supplied the following company context:\n"
            "----- BEGIN COMPANY CONTEXT -----\n"
            f"{context}\n"
            "----- END COMPANY CONTEXT -----"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": combined_system},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

    def health_check(self) -> tuple[bool, str]:
        """Lightweight connectivity check using a cheap read-only API call."""
        try:
            self.client.models.retrieve(self.model)
            return True, f"Connected to the OpenAI-compatible endpoint for model '{self.model}'."
        except Exception as exc:
            return False, f"Could not verify the OpenAI-compatible endpoint: {exc}"


def get_provider() -> Provider:
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

    if provider == "ollama":
        return OllamaProvider()
    if provider == "openai":
        return OpenAIProvider()

    raise LLMError(
        f"Unsupported LLM_PROVIDER '{provider}'. Use 'ollama' or 'openai'."
    )
