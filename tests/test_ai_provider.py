"""Tests for kgb_srs.ai_provider — AI client and validation, no real network."""

import json
import pytest

from kgb_srs.ai_provider import (
    AIClient,
    AIProviderConfig,
    build_sentence_prompt,
    build_word_phrase_prompt,
    AIMissingConfigError,
)


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
            learned_language="French",
            explanation_language="English",
        )
        assert "Je suis ici" in prompt
        assert "suis" in prompt
        assert "ici" in prompt
        assert "French" in prompt
        assert "English" in prompt
        assert "contextual_meaning" in prompt

    def test_default_languages(self):
        prompt = build_sentence_prompt(
            sentence="Hello world",
            unfamiliar_items=["world"],
        )
        assert "English" in prompt  # default learned
        assert "Chinese" in prompt  # default explanation (for barsky)


class TestBuildWordPhrasePrompt:
    def test_basic_prompt(self):
        prompt = build_word_phrase_prompt(
            word="café",
            learned_language="French",
            explanation_language="English",
        )
        assert "café" in prompt
        assert "French" in prompt
        assert "English" in prompt
        assert "meanings" in prompt
        assert "example" in prompt

    def test_up_to_two_meanings(self):
        prompt = build_word_phrase_prompt(word="test")
        assert "up to 2" in prompt.lower() or "two" in prompt.lower()


# ---------------------------------------------------------------------------
# make_http_call — unit-testable stub
# ---------------------------------------------------------------------------

class TestMakeHttpCall:
    def test_default_implementation_returns_mock_response(self):
        """The default no-network _make_http_call should raise on missing
        urllib or return an error. We just verify it exists and is callable."""
        from kgb_srs.ai_provider import _make_http_call
        assert callable(_make_http_call)
