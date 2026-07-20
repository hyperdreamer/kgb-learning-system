"""AI provider abstraction — OpenAI-compatible HTTP client.

Supports any OpenAI-compatible endpoint (GPT, DeepSeek, etc.).
Network calls use stdlib urllib; QThread worker keeps the UI responsive.

Non-secret defaults come from a template; API keys are never committed.
"""

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class AIProviderConfig:
    """AI provider configuration.

    Non-secret defaults are set here (model, base URL, timeout).
    API key is always blank by default — users supply it in their
    personal barsky_settings.json (which is git-ignored).
    """
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    timeout_seconds: int = 30

    @classmethod
    def from_settings(cls, settings: dict) -> "AIProviderConfig":
        return cls(
            base_url=settings.get("ai_base_url", cls.base_url),
            model=settings.get("ai_model", cls.model),
            api_key=settings.get("ai_api_key", ""),
            timeout_seconds=int(settings.get("ai_timeout", cls.timeout_seconds)),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def __repr__(self) -> str:
        return (
            f"AIProviderConfig(base_url={self.base_url!r}, model={self.model!r}, "
            f"api_key={'***' if self.api_key else '(not set)'}, "
            f"timeout={self.timeout_seconds}s)"
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AIMissingConfigError(Exception):
    """AI provider is not configured (missing API key, etc.)."""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SENTENCE_PROMPT_TEMPLATE = """\
You are a language learning assistant. Given a sentence and a list of
unfamiliar words or phrases from that sentence, provide the contextual
meaning of each item as it is used in the sentence.

Sentence: {sentence}
Unfamiliar items: {items}

Respond in {explanation_language} with a JSON object:
{{"items": [{{"expression": "<item>", "contextual_meaning": "<meaning>"}}]}}

- The number of items MUST equal {count}.
- Match the order of unfamiliar items given above.
- Each meaning should be concise and specific to this sentence."""


def build_sentence_prompt(
    sentence: str,
    unfamiliar_items: list[str],
    explanation_language: str = "Chinese",
) -> str:
    """Build the prompt for sentence-based AI generation."""
    items_list = "\n".join(f"  - {item}" for item in unfamiliar_items)
    return _SENTENCE_PROMPT_TEMPLATE.format(
        sentence=sentence,
        items=items_list,
        explanation_language=explanation_language,
        count=len(unfamiliar_items),
    )


_WORD_PHRASE_PROMPT_TEMPLATE = """\
You are a language learning assistant. Given a word or phrase, provide up to
{max_meanings} common modern meanings, each with an example sentence showing
typical usage.

Word/Phrase: {word}

Respond in {explanation_language} with a JSON object:
{{"meanings": [{{"meaning": "...", "example": "..."}}]}}

- Provide at most {max_meanings} meanings. Prefer the most common ones.
- It is acceptable to provide only 1 meaning.
- Do NOT invent extra meanings if fewer are common.
- Each example should be a natural sentence using the word/phrase."""


def build_word_phrase_prompt(
    word: str,
    explanation_language: str = "Chinese",
) -> str:
    """Build the prompt for word/phrase-based AI generation."""
    from .ai_parser import MAX_WORD_PHRASE_MEANINGS

    return _WORD_PHRASE_PROMPT_TEMPLATE.format(
        word=word,
        explanation_language=explanation_language,
        max_meanings=MAX_WORD_PHRASE_MEANINGS,
    )


_MEMBERSHIP_PROMPT_TEMPLATE = """\
You check whether learner items appear in a sentence as the same words or
as inflected / irregular surface forms (tense, number, participle, etc.).

Sentence:
{sentence}

Items that a local checker could not match:
{items}

Respond with JSON only:
{{"items": [{{"expression": "<item>", "found": true/false, "surface": "<exact span from the sentence or empty>"}}]}}

Rules:
- The number of items MUST equal {count}.
- Keep the same order as the list above.
- found=true only if the item (or a clear inflection/irregular form of it)
  appears as a consecutive span in the sentence.
- When found=true, surface MUST be copied exactly from the sentence
  (same spelling as in the sentence, including that form's tense).
- When found=false, surface must be "".
- Do not invent spans that are not in the sentence.
- Do not translate the sentence."""


def build_membership_prompt(sentence: str, missing_items: list[str]) -> str:
    """Build the prompt for AI membership fallback (local-first residual only)."""
    items_list = "\n".join(f"  - {item}" for item in missing_items)
    return _MEMBERSHIP_PROMPT_TEMPLATE.format(
        sentence=sentence,
        items=items_list,
        count=len(missing_items),
    )


# ---------------------------------------------------------------------------
# HTTP helper (stdlib only — no extra dependency)
# ---------------------------------------------------------------------------

def _make_http_call(
    url: str,
    headers: dict,
    body: bytes,
    timeout: int,
) -> str:
    """Make a synchronous HTTP POST call using stdlib urllib.

    Returns the response body as a string.
    Raises urllib.error.URLError on network/timeout errors.
    """
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# AI Client
# ---------------------------------------------------------------------------

class AIClient:
    """OpenAI-compatible chat completions client.

    Usage:
        client = AIClient(config)
        url, headers, body = client.build_request(user_prompt)
        raw = _make_http_call(url, headers, body, config.timeout_seconds)
        text = client.parse_response(raw)
    """

    def __init__(self, config: AIProviderConfig):
        self.config = config

    def build_request(self, user_prompt: str) -> tuple[str, dict, dict]:
        """Build (url, headers, body_dict) for a chat completions request.

        Raises AIMissingConfigError if api_key is not set.
        """
        if not self.config.api_key:
            raise AIMissingConfigError(
                "AI API key is not configured. Add 'ai_api_key' to barsky_settings.json."
            )

        base = self.config.base_url.rstrip("/")
        url = f"{base}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "You are a helpful language learning assistant. Respond with valid JSON only."},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
        }

        return url, headers, body

    def parse_response(self, response_text: str) -> str:
        """Extract the message content from an OpenAI-style chat response.

        Raises ValueError on malformed or error responses.
        """
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse API response as JSON: {e}")

        if "error" in data:
            msg = data["error"].get("message", str(data["error"]))
            raise ValueError(f"API error: {msg}")

        choices = data.get("choices")
        if choices is None:
            raise ValueError("API response has no 'choices'")

        if len(choices) == 0:
            raise ValueError("API response 'choices' is empty")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        return content


# ---------------------------------------------------------------------------
# Connection test (stdlib only)
# ---------------------------------------------------------------------------

def test_connection(config: AIProviderConfig) -> tuple[bool, str, float]:
    """Probe the configured model and return (ok, message, latency_ms).

    Sends a minimal chat-completions request to prove auth + model reachability.
    Does not raise for expected network/HTTP failures — they become (False, ...).
    """
    if not config.api_key:
        return False, "API key is not set", -1.0

    base = config.base_url.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }
    body = json.dumps({
        "model": config.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode("utf-8")

    started = time.monotonic()
    try:
        _make_http_call(url, headers, body, timeout=config.timeout_seconds)
        latency_ms = (time.monotonic() - started) * 1000.0
        return True, f"OK — {config.model} reachable", latency_ms
    except urllib.error.HTTPError as exc:
        latency_ms = (time.monotonic() - started) * 1000.0
        detail = _http_error_message(exc)
        return False, detail, latency_ms
    except urllib.error.URLError as exc:
        latency_ms = (time.monotonic() - started) * 1000.0
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
            return False, "timed out", latency_ms
        return False, f"Network error: {reason or exc}", latency_ms
    except TimeoutError:
        latency_ms = (time.monotonic() - started) * 1000.0
        return False, "timed out", latency_ms
    except Exception as exc:
        latency_ms = (time.monotonic() - started) * 1000.0
        return False, f"Unexpected error: {exc}", latency_ms


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Best-effort human-readable message from an HTTP error response."""
    body_text = ""
    try:
        body_text = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body_text = ""
    if body_text:
        try:
            data = json.loads(body_text)
            if isinstance(data, dict) and "error" in data:
                err = data["error"]
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("code")
                    if msg:
                        return str(msg)
                return str(err)
        except (json.JSONDecodeError, TypeError):
            pass
    reason = getattr(exc, "reason", None) or getattr(exc, "msg", None)
    if reason:
        return f"HTTP {exc.code}: {reason}"
    return f"HTTP {exc.code}"


# ---------------------------------------------------------------------------
# QThread worker (lazy PyQt6 import)
# ---------------------------------------------------------------------------

def _get_ai_worker_class():
    """Lazy import of AIWorker to avoid requiring PyQt6 at module level."""
    from PyQt6.QtCore import QThread, pyqtSignal

    class AIWorker(QThread):
        """Background thread for AI API calls — keeps the PyQt UI responsive."""

        result = pyqtSignal(str)     # emits response text
        error = pyqtSignal(str)      # emits error message

        def __init__(self, config: AIProviderConfig, prompt: str):
            super().__init__()
            self._config = config
            self._prompt = prompt

        def run(self):
            try:
                client = AIClient(self._config)
                url, headers, body = client.build_request(self._prompt)
                raw = _make_http_call(
                    url, headers,
                    json.dumps(body).encode("utf-8"),
                    timeout=self._config.timeout_seconds,
                )
                content = client.parse_response(raw)
                self.result.emit(content)
            except AIMissingConfigError as e:
                self.error.emit(str(e))
            except urllib.error.URLError as e:
                self.error.emit(f"Network error: {e.reason}")
            except ValueError as e:
                self.error.emit(str(e))
            except Exception as e:
                self.error.emit(f"Unexpected error: {e}")

    return AIWorker


def create_ai_worker(config: AIProviderConfig, prompt: str):
    """Create an AIWorker thread for the given config and prompt.

    Safe to call from any context where PyQt6 is available.
    """
    return _get_ai_worker_class()(config, prompt)


def _get_ai_test_worker_class():
    """Lazy import of AITestWorker to avoid requiring PyQt6 at module level."""
    from PyQt6.QtCore import QThread, pyqtSignal

    class AITestWorker(QThread):
        """Background thread that probes AI provider reachability."""

        result = pyqtSignal(bool, str, float)  # ok, message, latency_ms

        def __init__(self, config: AIProviderConfig):
            super().__init__()
            self._config = config

        def run(self):
            ok, message, latency_ms = test_connection(self._config)
            self.result.emit(ok, message, latency_ms)

    return AITestWorker


def create_ai_test_worker(config: AIProviderConfig):
    """Create an AITestWorker thread for the given config.

    Safe to call from any context where PyQt6 is available.
    """
    return _get_ai_test_worker_class()(config)
