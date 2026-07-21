"""Tests for kgb_srs.ai_provider — AI client and validation, no real network."""

import io
import json
import urllib.error

import pytest

from kgb_srs.ai_provider import (
    AIClient,
    AIProviderConfig,
    build_sentence_prompt,
    build_word_phrase_prompt,
    AIMissingConfigError,
)
# Import under a non-test name so pytest does not collect the pure function.
from kgb_srs.ai_provider import test_connection as check_ai_connection


# ---------------------------------------------------------------------------
# AIProviderConfig
# ---------------------------------------------------------------------------

class TestAIProviderConfig:
    def test_defaults(self):
        cfg = AIProviderConfig()
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.api_key == ""
        assert cfg.timeout_seconds == 30

    def test_from_settings(self):
        settings = {
            "ai_base_url": "https://api.deepseek.com/v1",
            "ai_model": "deepseek-chat",
            "ai_api_key": "sk-test",
            "ai_timeout": 15,
        }
        cfg = AIProviderConfig.from_settings(settings)
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.model == "deepseek-chat"
        assert cfg.api_key == "sk-test"
        assert cfg.timeout_seconds == 15

    def test_from_settings_partial(self):
        """Missing keys fall back to defaults."""
        settings = {"ai_api_key": "sk-abc"}
        cfg = AIProviderConfig.from_settings(settings)
        assert cfg.api_key == "sk-abc"
        assert cfg.model == "gpt-4o-mini"

    def test_configured_property(self):
        assert AIProviderConfig().configured is False
        assert AIProviderConfig(api_key="sk-test").configured is True

    def test_repr_does_not_leak_key(self):
        cfg = AIProviderConfig(api_key="sk-secret-12345")
        r = repr(cfg)
        assert "sk-secret" not in r


# ---------------------------------------------------------------------------
# AIClient — build_request
# ---------------------------------------------------------------------------

class TestAIClientBuildRequest:
    def test_builds_correct_url(self):
        cfg = AIProviderConfig(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="test-model",
        )
        client = AIClient(cfg)
        url, headers, body = client.build_request("Hello")
        assert url == "https://api.example.com/v1/chat/completions"
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer sk-test"
        assert body["model"] == "test-model"

    def test_missing_api_key_raises(self):
        cfg = AIProviderConfig(api_key="")
        client = AIClient(cfg)
        with pytest.raises(AIMissingConfigError):
            client.build_request("test")

    def test_strips_trailing_slash(self):
        cfg = AIProviderConfig(
            base_url="https://api.example.com/v1/",
            api_key="sk-test",
        )
        client = AIClient(cfg)
        url, _, _ = client.build_request("hello")
        assert url == "https://api.example.com/v1/chat/completions"

    def test_messages_structure(self):
        cfg = AIProviderConfig(api_key="sk-test")
        client = AIClient(cfg)
        _, _, body = client.build_request("Hello, AI!")
        msgs = body["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "Hello, AI!"
        assert "temperature" in body


# ---------------------------------------------------------------------------
# AIClient — parse_response
# ---------------------------------------------------------------------------

class TestAIClientParseResponse:
    @pytest.fixture
    def client(self):
        return AIClient(AIProviderConfig(api_key="sk-test"))

    def test_extracts_content(self, client):
        response_json = json.dumps({
            "choices": [{"message": {"content": "Hello back!"}}]
        })
        result = client.parse_response(response_json)
        assert result == "Hello back!"

    def test_missing_choices(self, client):
        with pytest.raises(ValueError, match="choices"):
            client.parse_response(json.dumps({}))

    def test_empty_choices(self, client):
        with pytest.raises(ValueError, match="choices"):
            client.parse_response(json.dumps({"choices": []}))

    def test_not_json(self, client):
        with pytest.raises(ValueError, match="JSON"):
            client.parse_response("not json")

    def test_error_response(self, client):
        response_json = json.dumps({
            "error": {"message": "Invalid API key"}
        })
        with pytest.raises(ValueError, match="Invalid API key"):
            client.parse_response(response_json)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

class TestBuildSentencePrompt:
    def test_basic_prompt(self):
        prompt = build_sentence_prompt(
            sentence="Je suis ici.",
            unfamiliar_items=["suis", "ici"],
            explanation_language="English",
        )
        assert "Je suis ici" in prompt
        assert "suis" in prompt
        assert "ici" in prompt
        assert "English" in prompt
        assert "contextual_meaning" in prompt

    def test_default_explanation_language(self):
        prompt = build_sentence_prompt(
            sentence="Hello world",
            unfamiliar_items=["world"],
        )
        assert "Chinese" in prompt  # default explanation (for barsky)


class TestBuildWordPhrasePrompt:
    def test_basic_prompt(self):
        prompt = build_word_phrase_prompt(
            word="café",
            explanation_language="English",
        )
        assert "café" in prompt
        assert "English" in prompt
        assert "meanings" in prompt
        assert "example" in prompt

    def test_mentions_max_meanings(self):
        from kgb_srs.ai_parser import MAX_WORD_PHRASE_MEANINGS

        prompt = build_word_phrase_prompt(word="test")
        assert str(MAX_WORD_PHRASE_MEANINGS) in prompt
        assert "up to" in prompt.lower()


# ---------------------------------------------------------------------------
# make_http_call — unit-testable stub
# ---------------------------------------------------------------------------

class TestMakeHttpCall:
    def test_default_implementation_returns_mock_response(self):
        """The default no-network _make_http_call should raise on missing
        urllib or return an error. We just verify it exists and is callable."""
        from kgb_srs.ai_provider import _make_http_call
        assert callable(_make_http_call)


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------

class TestTestConnection:
    def _cfg(self, **overrides):
        data = dict(
            base_url="https://api.example.com/v1",
            model="test-model",
            api_key="sk-test",
            timeout_seconds=5,
        )
        data.update(overrides)
        return AIProviderConfig(**data)

    def test_missing_api_key(self):
        ok, message, latency = check_ai_connection(self._cfg(api_key=""))
        assert ok is False
        assert "API key" in message
        assert latency == -1.0

    def test_success(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, body, timeout):
            assert url == "https://api.example.com/v1/chat/completions"
            assert headers["Authorization"] == "Bearer sk-test"
            payload = json.loads(body.decode("utf-8"))
            assert payload["model"] == "test-model"
            assert payload["messages"] == [{"role": "user", "content": "ping"}]
            assert payload["max_tokens"] == 1
            return json.dumps({
                "choices": [{"message": {"content": "pong"}}]
            })

        monkeypatch.setattr(module, "_make_http_call", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is True
        assert "test-model" in message
        assert latency >= 0

    def test_http_401(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, body, timeout):
            raise urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(json.dumps({
                    "error": {"message": "Invalid API key"}
                }).encode("utf-8")),
            )

        monkeypatch.setattr(module, "_make_http_call", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is False
        assert "Invalid API key" in message
        assert latency >= 0

    def test_timeout(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, body, timeout):
            raise urllib.error.URLError(TimeoutError("timed out"))

        monkeypatch.setattr(module, "_make_http_call", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is False
        assert "timed out" in message.lower()
        assert latency >= 0

    def test_network_error(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, body, timeout):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(module, "_make_http_call", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is False
        assert "connection refused" in message.lower() or "Network error" in message
        assert latency >= 0
