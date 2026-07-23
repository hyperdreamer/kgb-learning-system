"""AI provider abstraction — OpenAI-compatible HTTP client.

Supports any OpenAI-compatible endpoint (GPT, DeepSeek, etc.).
Network calls use stdlib urllib; QThread worker keeps the UI responsive.

Non-secret defaults come from a template; API keys are never committed.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_AI_PROVIDER_NAME = "Default"


def _safe_timeout(value, default: int = 30) -> int:
    try:
        return max(5, min(120, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_provider_entry(raw: dict | None) -> dict:
    """Normalize one provider profile dict (no secrets leaked in errors)."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "base_url": str(raw.get("base_url") or "https://api.openai.com/v1").strip(),
        "model": str(raw.get("model") or "gpt-4o-mini").strip(),
        "api_key": str(raw.get("api_key") or ""),
        "timeout": _safe_timeout(raw.get("timeout", 30)),
    }


# Legacy flat keys (pre multi-provider). Migrated into profiles on load/save,
# then stripped so profiles are the only persisted surface.
LEGACY_AI_FLAT_KEYS = ("ai_base_url", "ai_model", "ai_api_key", "ai_timeout")


def _legacy_flat_entry_from_settings(settings: dict) -> dict:
    """Build one profile from obsolete flat ai_* keys (migration only)."""
    return {
        "base_url": str(
            settings.get("ai_base_url") or "https://api.openai.com/v1"
        ).strip(),
        "model": str(settings.get("ai_model") or "gpt-4o-mini").strip(),
        "api_key": str(settings.get("ai_api_key") or ""),
        "timeout": _safe_timeout(settings.get("ai_timeout", 30)),
    }


def strip_legacy_ai_flat_keys(settings: dict) -> dict:
    """Remove obsolete flat ai_* mirrors. Mutates and returns *settings*."""
    for key in LEGACY_AI_FLAT_KEYS:
        settings.pop(key, None)
    return settings


def ensure_ai_provider_profiles(settings: dict) -> dict:
    """Ensure *settings* has ``ai_providers`` + ``ai_active_provider``.

    Migrates legacy flat keys into a single profile when profiles are
    missing. Does **not** keep flat mirrors — profiles are the only
    source of truth. Mutates and returns *settings*.
    """
    providers_raw = settings.get("ai_providers")
    providers: dict[str, dict] = {}
    if isinstance(providers_raw, dict):
        for name, entry in providers_raw.items():
            label = str(name or "").strip()
            if not label:
                continue
            providers[label] = _normalize_provider_entry(entry)

    if not providers:
        providers[DEFAULT_AI_PROVIDER_NAME] = _legacy_flat_entry_from_settings(settings)

    active = str(settings.get("ai_active_provider") or "").strip()
    if active not in providers:
        active = next(iter(providers))

    settings["ai_providers"] = providers
    settings["ai_active_provider"] = active
    # Drop redundant flat keys so config/UI only point at the active profile.
    strip_legacy_ai_flat_keys(settings)
    return settings


def list_ai_provider_names(settings: dict) -> list[str]:
    """Sorted provider profile names (active first, then alpha)."""
    ensure_ai_provider_profiles(settings)
    active = settings["ai_active_provider"]
    names = sorted(settings["ai_providers"].keys(), key=str.casefold)
    if active in names:
        names.remove(active)
        names.insert(0, active)
    return names


def get_ai_provider_entry(settings: dict, name: str | None = None) -> dict:
    """Return a copy of the named (or active) provider profile."""
    ensure_ai_provider_profiles(settings)
    label = (name or settings["ai_active_provider"]).strip()
    entry = settings["ai_providers"].get(label)
    if entry is None:
        entry = settings["ai_providers"][settings["ai_active_provider"]]
    return dict(entry)


def set_active_ai_provider(settings: dict, name: str) -> bool:
    """Switch active provider; return False if *name* is unknown."""
    ensure_ai_provider_profiles(settings)
    label = (name or "").strip()
    if label not in settings["ai_providers"]:
        return False
    settings["ai_active_provider"] = label
    ensure_ai_provider_profiles(settings)
    return True


def upsert_ai_provider(
    settings: dict,
    name: str,
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout: int,
    make_active: bool = False,
) -> str:
    """Create or update a named provider profile. Returns the stored name."""
    ensure_ai_provider_profiles(settings)
    label = (name or "").strip() or DEFAULT_AI_PROVIDER_NAME
    settings["ai_providers"][label] = _normalize_provider_entry(
        {
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "timeout": timeout,
        }
    )
    if (
        make_active
        or settings.get("ai_active_provider") not in settings["ai_providers"]
    ):
        settings["ai_active_provider"] = label
    ensure_ai_provider_profiles(settings)
    return label


def rename_ai_provider(settings: dict, old_name: str, new_name: str) -> str | None:
    """Rename a profile. Returns new name, or None if rename failed."""
    ensure_ai_provider_profiles(settings)
    old = (old_name or "").strip()
    new = (new_name or "").strip()
    if not old or not new or old not in settings["ai_providers"]:
        return None
    if new != old and new in settings["ai_providers"]:
        return None
    if new == old:
        return old
    settings["ai_providers"][new] = settings["ai_providers"].pop(old)
    if settings.get("ai_active_provider") == old:
        settings["ai_active_provider"] = new
    ensure_ai_provider_profiles(settings)
    return new


def delete_ai_provider(settings: dict, name: str) -> bool:
    """Delete a profile. Refuses to delete the last remaining one."""
    ensure_ai_provider_profiles(settings)
    label = (name or "").strip()
    if label not in settings["ai_providers"]:
        return False
    if len(settings["ai_providers"]) <= 1:
        return False
    del settings["ai_providers"][label]
    if settings.get("ai_active_provider") == label:
        settings["ai_active_provider"] = next(iter(settings["ai_providers"]))
    ensure_ai_provider_profiles(settings)
    return True


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
        """Build config from the *active* provider profile."""
        ensure_ai_provider_profiles(settings)
        entry = get_ai_provider_entry(settings)
        return cls(
            base_url=entry.get("base_url", cls.base_url),
            model=entry.get("model", cls.model),
            api_key=entry.get("api_key", ""),
            timeout_seconds=int(entry.get("timeout", cls.timeout_seconds)),
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


_SENSE_ASSIGN_PROMPT_TEMPLATE = """\
You assign dictionary senses for language learning.

Given a sentence and one expression from that sentence, decide whether an
existing sense already fits this context, or a new sense is needed.

Sentence:
{sentence}

Expression:
{expression}

Known prior senses for this expression (may be empty):
{prior_senses}

Respond in {explanation_language} with JSON only:
{{"expression": "{expression}", "action": "reuse"|"create", "sense_id": <int or null>, "meaning": "<text>"}}

Rules:
- If a prior sense matches this sentence's usage, action="reuse" and
  sense_id MUST be one of the listed ids. meaning may be "".
- If no prior sense fits, action="create", sense_id=null, and meaning MUST
  be a concise contextual meaning of the expression as used in this sentence.
- Do not invent sense_id values that are not listed.
- Prefer reuse when the sense is the same even if wording differs slightly.
- Meaning text (when create) must be specific to this sentence's usage."""


def build_sense_assignment_prompt(
    sentence: str,
    expression: str,
    prior_senses: list[tuple[int, str]],
    explanation_language: str = "Chinese",
) -> str:
    """Build prompt: reuse prior sense id or create a new contextual meaning.

    *prior_senses* is a list of (sense_id, meaning_text).
    """
    if prior_senses:
        lines = [f"  - id={sid}: {meaning}" for sid, meaning in prior_senses]
        prior_block = "\n".join(lines)
    else:
        prior_block = "  (none)"
    # Escape braces in expression for .format safety by not putting
    # free user text into format fields that contain braces — expression
    # is substituted only in plain slots.
    return _SENSE_ASSIGN_PROMPT_TEMPLATE.format(
        sentence=sentence,
        expression=expression,
        prior_senses=prior_block,
        explanation_language=explanation_language,
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
You check whether learner items appear in a sentence as the same words,
as inflected / irregular surface forms (tense, number, participle, etc.), or
as clear grammatical variants of an idiomatic expression.

Sentence:
{sentence}

Items that a local checker could not match:
{items}

Respond with JSON only:
{{"items": [{{"expression": "<item>", "found": true/false, "surface": "<exact span from the sentence or empty>"}}]}}

Rules:
- The number of items MUST equal {count}.
- Keep the same order as the list above.
- found=true only if the item (or a clear inflection/irregular or
  grammatical variant of it) appears as one consecutive span in the sentence.
- Treat a copular idiom as found when its "be" verb is conjugated and a
  degree modifier is inserted without changing the idiom's meaning. For
  example, "be worse off" is found in "Our tribe is even worse off!";
  return "is even worse off" as its surface.
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

_ALLOWED_HTTP_SCHEMES = frozenset({"http", "https"})


def _validate_http_url(url: str) -> None:
    """Require an absolute HTTP(S) URL for AI provider requests."""
    if not isinstance(url, str):
        raise ValueError("AI provider URL must use an absolute http or https URL")

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.casefold() not in _ALLOWED_HTTP_SCHEMES or not parsed.netloc:
        raise ValueError("AI provider URL must use an absolute http or https URL")


class _HTTPOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave the HTTP(S) transport boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_request(
    url: str,
    headers: dict,
    *,
    body: bytes | None = None,
    timeout: int,
    method: str = "GET",
) -> str:
    """Synchronous HTTP request via stdlib urllib (GET or POST)."""
    _validate_http_url(url)
    # The initial URL and every redirect are constrained to HTTP(S) above.
    req = urllib.request.Request(  # noqa: S310
        url, data=body, headers=headers, method=method
    )
    opener = urllib.request.build_opener(_HTTPOnlyRedirectHandler())
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# AI Client
# ---------------------------------------------------------------------------


class AIClient:
    """OpenAI-compatible chat completions client.

    Usage:
        client = AIClient(config)
        url, headers, body = client.build_request(user_prompt)
        raw = http_request(url, headers, body=body, timeout=config.timeout_seconds, method="POST")
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
                "AI API key is not configured. Set it under Settings → AI Providers."
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
                {
                    "role": "system",
                    "content": "You are a helpful language learning assistant. Respond with valid JSON only.",
                },
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

        if not isinstance(data, dict):
            raise ValueError("API response must be a JSON object")

        if "error" in data:
            error = data["error"]
            if not isinstance(error, dict):
                raise ValueError("API response 'error' must be an object")
            msg = error.get("message", str(error))
            raise ValueError(f"API error: {msg}")

        choices = data.get("choices")
        if choices is None:
            raise ValueError("API response has no 'choices'")

        if not isinstance(choices, list):
            raise ValueError("API response 'choices' must be a list")

        if len(choices) == 0:
            raise ValueError("API response 'choices' is empty")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("API response first choice must be an object")
        message = choice.get("message", {})
        if not isinstance(message, dict):
            raise ValueError("API response first choice message must be an object")
        if "content" not in message:
            raise ValueError("API response first choice message has no 'content'")
        content = message["content"]
        if not isinstance(content, str):
            raise ValueError(
                "API response first choice message 'content' must be a string"
            )
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
    body = json.dumps(
        {
            "model": config.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    ).encode("utf-8")

    started = time.monotonic()
    try:
        raw = http_request(
            url, headers, body=body, timeout=config.timeout_seconds, method="POST"
        )
        AIClient(config).parse_response(raw)
        latency_ms = (time.monotonic() - started) * 1000.0
        return True, f"OK — {config.model} reachable", latency_ms
    except ValueError as exc:
        latency_ms = (time.monotonic() - started) * 1000.0
        return False, str(exc), latency_ms
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


def list_models(config: AIProviderConfig) -> tuple[bool, str, list[str]]:
    """Discover models from the OpenAI-compatible ``/models`` endpoint.

    Returns ``(ok, message, model_ids)``. On success *model_ids* is sorted
    case-insensitively and de-duplicated. On failure *model_ids* is empty.
    Does not raise for expected network/HTTP failures.
    """
    if not config.api_key:
        return False, "API key is not set", []

    base = config.base_url.rstrip("/")
    url = f"{base}/models"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    try:
        raw = http_request(
            url, headers, body=None, timeout=config.timeout_seconds, method="GET"
        )
    except urllib.error.HTTPError as exc:
        return False, _http_error_message(exc), []
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
            return False, "timed out", []
        return False, f"Network error: {reason or exc}", []
    except TimeoutError:
        return False, "timed out", []
    except Exception as exc:
        return False, f"Unexpected error: {exc}", []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"Failed to parse models response: {exc}", []

    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or str(err)
        else:
            msg = str(err)
        return False, str(msg), []

    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return False, "Models response has no 'data' list", []

    ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "").strip()
        if not mid:
            continue
        key = mid.casefold()
        if key in seen:
            continue
        seen.add(key)
        ids.append(mid)
    ids.sort(key=str.casefold)
    if not ids:
        return False, "No models returned", []
    return True, f"{len(ids)} model(s)", ids


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

_AI_WORKER_CLASS = None


def _get_ai_worker_class():
    """Return the cached lazy PyQt AI worker class.

    Importing this module remains usable without PyQt6; the worker class is
    constructed only on its first UI use and reused thereafter.
    """
    global _AI_WORKER_CLASS
    if _AI_WORKER_CLASS is not None:
        return _AI_WORKER_CLASS

    from PyQt6.QtCore import QThread, pyqtSignal

    class AIWorker(QThread):
        """Background thread for AI API calls — keeps the PyQt UI responsive."""

        result = pyqtSignal(str)  # emits response text
        error = pyqtSignal(str)  # emits error message

        def __init__(self, config: AIProviderConfig, prompt: str):
            super().__init__()
            self._config = config
            self._prompt = prompt

        def run(self):
            try:
                client = AIClient(self._config)
                url, headers, body = client.build_request(self._prompt)
                raw = http_request(
                    url,
                    headers,
                    body=json.dumps(body).encode("utf-8"),
                    timeout=self._config.timeout_seconds,
                    method="POST",
                )
                content = client.parse_response(raw)
                self.result.emit(content)
            except AIMissingConfigError as e:
                self.error.emit(str(e))
            except urllib.error.URLError as e:
                self.error.emit(f"Network error: {getattr(e, 'reason', str(e))}")
            except ValueError as e:
                self.error.emit(str(e))
            except Exception as e:
                self.error.emit(f"Unexpected error: {e}")

    _AI_WORKER_CLASS = AIWorker
    return _AI_WORKER_CLASS


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


def _get_ai_models_worker_class():
    """Lazy import of AIModelsWorker to avoid requiring PyQt6 at module level."""
    from PyQt6.QtCore import QThread, pyqtSignal

    class AIModelsWorker(QThread):
        """Background thread that lists models from the active provider."""

        result = pyqtSignal(bool, str, list)  # ok, message, model_ids

        def __init__(self, config: AIProviderConfig):
            super().__init__()
            self._config = config

        def run(self):
            ok, message, models = list_models(self._config)
            self.result.emit(ok, message, list(models))

    return AIModelsWorker


def create_ai_models_worker(config: AIProviderConfig):
    """Create an AIModelsWorker thread for the given config.

    Safe to call from any context where PyQt6 is available.
    """
    return _get_ai_models_worker_class()(config)
