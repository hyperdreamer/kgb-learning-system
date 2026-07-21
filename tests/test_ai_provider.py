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
            "ai_active_provider": "DeepSeek",
            "ai_providers": {
                "DeepSeek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "api_key": "key-test",
                    "timeout": 15,
                }
            },
        }
        cfg = AIProviderConfig.from_settings(settings)
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.model == "deepseek-chat"
        assert cfg.api_key == "key-test"
        assert cfg.timeout_seconds == 15
        assert "ai_api_key" not in settings
        assert "ai_model" not in settings

    def test_from_settings_partial(self):
        """Missing profile bag migrates from legacy flat keys, then strips them."""
        settings = {"ai_api_key": "key-abc"}
        cfg = AIProviderConfig.from_settings(settings)
        assert cfg.api_key == "key-abc"
        assert cfg.model == "gpt-4o-mini"
        assert "ai_api_key" not in settings
        assert settings["ai_providers"][settings["ai_active_provider"]]["api_key"] == (
            "key-abc"
        )

    def test_from_settings_uses_active_profile(self):
        settings = {
            "ai_active_provider": "DeepSeek",
            "ai_providers": {
                "OpenAI": {
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "api_key": "key-openai",
                    "timeout": 30,
                },
                "DeepSeek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "api_key": "key-deep",
                    "timeout": 20,
                },
            },
            # Stale flat keys must not win over active profile and are stripped.
            "ai_base_url": "https://stale.example/v1",
            "ai_model": "stale",
            "ai_api_key": "key-stale",
            "ai_timeout": 5,
        }
        cfg = AIProviderConfig.from_settings(settings)
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.model == "deepseek-chat"
        assert cfg.api_key == "key-deep"
        assert cfg.timeout_seconds == 20
        assert "ai_base_url" not in settings
        assert "ai_model" not in settings
        assert "ai_api_key" not in settings
        assert "ai_timeout" not in settings

    def test_legacy_flat_settings_migrate_to_default_profile(self):
        from kgb_srs.ai_provider import ensure_ai_provider_profiles

        settings = {
            "ai_base_url": "https://tokenhub.example/v1",
            "ai_model": "flash",
            "ai_api_key": "key-legacy",
            "ai_timeout": 40,
        }
        ensure_ai_provider_profiles(settings)
        assert "Default" in settings["ai_providers"]
        assert settings["ai_active_provider"] == "Default"
        assert settings["ai_providers"]["Default"]["model"] == "flash"
        assert settings["ai_providers"]["Default"]["api_key"] == "key-legacy"
        assert settings["ai_providers"]["Default"]["base_url"] == (
            "https://tokenhub.example/v1"
        )
        assert settings["ai_providers"]["Default"]["timeout"] == 40
        assert "ai_api_key" not in settings
        assert "ai_model" not in settings

    def test_load_settings_preserves_legacy_flat_api_key(self, tmp_path, monkeypatch):
        """Flat-only barsky_settings.json migrates key into active profile."""
        import kgb_srs.config as config

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        settings_path.write_text(
            json.dumps(
                {
                    "ai_api_key": "key-migrated",
                    "ai_model": "deepseek-chat",
                    "width": 900,
                }
            ),
            encoding="utf-8",
        )
        loaded = config.load_settings()
        active = loaded["ai_providers"][loaded["ai_active_provider"]]
        assert active["api_key"] == "key-migrated"
        assert active["model"] == "deepseek-chat"
        assert "ai_api_key" not in loaded
        assert "ai_model" not in loaded

    def test_save_settings_strips_legacy_flat_keys(self, tmp_path, monkeypatch):
        """Persisted JSON keeps profiles only — no flat ai_* mirrors."""
        import kgb_srs.config as config

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        config.save_settings(
            {
                "width": 900,
                "ai_api_key": "key-should-migrate",
                "ai_model": "flash",
                "ai_base_url": "https://tokenhub.example/v1",
                "ai_timeout": 40,
            }
        )
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        for key in ("ai_base_url", "ai_model", "ai_api_key", "ai_timeout"):
            assert key not in raw
        active = raw["ai_providers"][raw["ai_active_provider"]]
        assert active["api_key"] == "key-should-migrate"
        assert active["model"] == "flash"

    def test_switch_add_rename_delete_profiles(self):
        from kgb_srs.ai_provider import (
            delete_ai_provider,
            ensure_ai_provider_profiles,
            get_ai_provider_entry,
            rename_ai_provider,
            set_active_ai_provider,
            upsert_ai_provider,
        )

        settings = {
            "ai_base_url": "https://api.openai.com/v1",
            "ai_model": "gpt-4o-mini",
            "ai_api_key": "key-a",
            "ai_timeout": 30,
        }
        ensure_ai_provider_profiles(settings)
        upsert_ai_provider(
            settings,
            "TokenHub",
            base_url="https://tokenhub.example/v1",
            model="deepseek-v4",
            api_key="key-b",
            timeout=25,
            make_active=True,
        )
        assert settings["ai_active_provider"] == "TokenHub"
        assert get_ai_provider_entry(settings)["model"] == "deepseek-v4"
        assert "ai_model" not in settings
        assert set_active_ai_provider(settings, "Default")
        assert get_ai_provider_entry(settings)["model"] == "gpt-4o-mini"
        assert rename_ai_provider(settings, "TokenHub", "TH") == "TH"
        assert "TokenHub" not in settings["ai_providers"]
        assert "TH" in settings["ai_providers"]
        assert delete_ai_provider(settings, "TH") is True
        assert delete_ai_provider(settings, "Default") is False  # last one

    def test_configured_property(self):
        assert AIProviderConfig().configured is False
        assert AIProviderConfig(api_key="sk-test").configured is True

    def test_repr_does_not_leak_key(self):
        cfg = AIProviderConfig(api_key="sk-secret-12345")
        r = repr(cfg)
        assert "sk-secret" not in r
        assert "***" in r


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

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            ([], "JSON object"),
            ({"error": "Invalid API key"}, "error.*object"),
            ({"choices": {}}, "choices.*list"),
            ({"choices": ["not an object"]}, "first choice.*object"),
            ({"choices": [{"message": []}]}, "message.*object"),
        ],
    )
    def test_malformed_response_containers_raise_value_error(
        self, client, response, message
    ):
        with pytest.raises(ValueError, match=message):
            client.parse_response(json.dumps(response))

    @pytest.mark.parametrize(
        "message",
        [{}, {"content": None}, {"content": []}, {"content": {}}, {"content": 1}],
    )
    def test_missing_or_non_string_content_raises_value_error(self, client, message):
        response = {"choices": [{"message": message}]}
        with pytest.raises(ValueError, match="content"):
            client.parse_response(json.dumps(response))


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

        def fake_http(url, headers, *, body=None, timeout=0, method="GET"):
            assert url == "https://api.example.com/v1/chat/completions"
            assert headers["Authorization"] == "Bearer sk-test"
            payload = json.loads(body.decode("utf-8"))
            assert payload["model"] == "test-model"
            assert payload["messages"] == [{"role": "user", "content": "ping"}]
            assert payload["max_tokens"] == 1
            return json.dumps({
                "choices": [{"message": {"content": "pong"}}]
            })

        monkeypatch.setattr(module, "http_request", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is True
        assert "test-model" in message
        assert latency >= 0

    def test_http_200_error_envelope_is_failure(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, *, body=None, timeout=0, method="GET"):
            return json.dumps({"error": {"message": "model not available"}})

        monkeypatch.setattr(module, "http_request", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is False
        assert "model not available" in message
        assert latency >= 0

    def test_http_401(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, *, body=None, timeout=0, method="GET"):
            raise urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(json.dumps({
                    "error": {"message": "Invalid API key"}
                }).encode("utf-8")),
            )

        monkeypatch.setattr(module, "http_request", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is False
        assert "Invalid API key" in message
        assert latency >= 0

    def test_timeout(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, *, body=None, timeout=0, method="GET"):
            raise urllib.error.URLError(TimeoutError("timed out"))

        monkeypatch.setattr(module, "http_request", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is False
        assert "timed out" in message.lower()
        assert latency >= 0

    def test_network_error(self, monkeypatch):
        import kgb_srs.ai_provider as module

        def fake_http(url, headers, *, body=None, timeout=0, method="GET"):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(module, "http_request", fake_http)
        ok, message, latency = check_ai_connection(self._cfg())
        assert ok is False
        assert "connection refused" in message.lower() or "Network error" in message
        assert latency >= 0


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------

class TestListModels:
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
        from kgb_srs.ai_provider import list_models
        ok, message, models = list_models(self._cfg(api_key=""))
        assert ok is False
        assert "API key" in message
        assert models == []

    def test_success_parses_and_sorts(self, monkeypatch):
        import kgb_srs.ai_provider as module
        from kgb_srs.ai_provider import list_models

        def fake_http(url, headers, body=None, timeout=5, method="GET"):
            assert method == "GET"
            assert url == "https://api.example.com/v1/models"
            assert headers["Authorization"] == "Bearer sk-test"
            assert body is None
            return json.dumps({
                "data": [
                    {"id": "zeta-model"},
                    {"id": "alpha-model"},
                    {"id": "alpha-model"},
                    {"object": "model"},
                ]
            })

        monkeypatch.setattr(module, "http_request", fake_http)
        ok, message, models = list_models(self._cfg())
        assert ok is True
        assert models == ["alpha-model", "zeta-model"]
        assert "2 model" in message

    def test_http_401(self, monkeypatch):
        import kgb_srs.ai_provider as module
        from kgb_srs.ai_provider import list_models

        def fake_http(url, headers, body=None, timeout=5, method="GET"):
            raise urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                hdrs=None,
                fp=io.BytesIO(json.dumps({
                    "error": {"message": "Invalid API key"}
                }).encode("utf-8")),
            )

        monkeypatch.setattr(module, "http_request", fake_http)
        ok, message, models = list_models(self._cfg())
        assert ok is False
        assert "Invalid API key" in message
        assert models == []

    def test_empty_data(self, monkeypatch):
        import kgb_srs.ai_provider as module
        from kgb_srs.ai_provider import list_models

        monkeypatch.setattr(
            module,
            "http_request",
            lambda *a, **k: json.dumps({"data": []}),
        )
        ok, message, models = list_models(self._cfg())
        assert ok is False
        assert "No models" in message
        assert models == []
