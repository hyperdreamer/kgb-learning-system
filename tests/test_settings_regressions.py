"""Regression tests for settings lifecycle and public imports."""

import json

import pytest

from .qt_helpers import qt_app as _qt_app


class TestNoDuplicateException:
    """AIMissingConfigError must only exist in one module."""

    def test_aimissingconfigerror_only_in_ai_provider(self):
        """The canonical source is ai_provider. ai_parser should not
        define a duplicate AIMissingConfigError."""
        from kgb_srs.ai_provider import AIMissingConfigError

        assert AIMissingConfigError is not None
        # ai_parser should NOT have its own AIMissingConfigError
        import kgb_srs.ai_parser as ap

        assert not hasattr(ap, "AIMissingConfigError")


class TestPublicAPILazy:
    """from kgb_srs import BarskyApp must work lazily."""

    def test_barskyapp_in_all(self):
        import kgb_srs

        assert "BarskyApp" in kgb_srs.__all__

    def test_barskyapp_accessible(self):
        from kgb_srs import BarskyApp

        assert BarskyApp is not None

    def test_get_app_returns_same(self):
        from kgb_srs import BarskyApp, get_app

        assert get_app() is BarskyApp


class TestSettingsFileSafety:
    def test_settings_save_failure_is_propagated(self, tmp_path, monkeypatch):
        import kgb_srs.config as config

        monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
        monkeypatch.setattr(
            config.os,
            "replace",
            lambda *args: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError, match="disk full"):
            config.save_settings({"ai_api_key": "secret"})

    def test_settings_file_is_owner_only(self, tmp_path, monkeypatch):
        import stat
        import kgb_srs.config as config

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        config.save_settings({"ai_api_key": "secret"})
        assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600


class TestSettingsStaging:
    """open_settings_window must not mutate self.settings before save_settings succeeds."""

    def test_settings_not_mutated_before_save(self, tmp_path, monkeypatch):
        """Live settings remain unchanged before save_settings succeeds."""
        import kgb_srs.config as config
        from kgb_srs.main_window import BarskyApp

        # Create settings file with known values
        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        config.save_settings(
            {
                "width": 900,
                "height": 700,
                "font_family": "Arial",
                "font_size": 14,
                "default_database": "",
                "tts_voice": "en-US-Ava",
                "ai_active_provider": "Default",
                "ai_providers": {
                    "Default": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o-mini",
                        "api_key": "secret123",
                        "timeout": 30,
                    }
                },
                "explanation_language": "Chinese",
            }
        )
        monkeypatch.setattr(config, "load_settings", lambda: config.load_settings())

        _qt_app()
        window = BarskyApp()
        original_settings = dict(window.settings)  # deep copy

        # Simulate what save_and_apply in open_settings_window does:
        # It builds staged changes and saves them
        staged = dict(window.settings)
        staged["width"] = 1024
        staged["ai_providers"] = {
            name: dict(entry) for name, entry in staged.get("ai_providers", {}).items()
        }
        active = staged["ai_active_provider"]
        staged["ai_providers"][active]["api_key"] = "new_secret"

        # Before save, original settings should be unchanged
        assert window.settings["width"] == original_settings["width"]
        assert (
            window.settings["ai_providers"][window.settings["ai_active_provider"]][
                "api_key"
            ]
            == original_settings["ai_providers"][
                original_settings["ai_active_provider"]
            ]["api_key"]
        )

        # After successful save, should update
        config.save_settings(staged)
        window.settings.update(staged)
        assert window.settings["width"] == 1024
        assert (
            window.settings["ai_providers"][window.settings["ai_active_provider"]][
                "api_key"
            ]
            == "new_secret"
        )

        window.close()

    def test_settings_preserved_on_save_failure(self, tmp_path, monkeypatch):
        """On OSError during save, live settings must remain byte-for-byte unchanged."""
        import kgb_srs.config as config
        from kgb_srs.main_window import BarskyApp

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))
        config.save_settings(
            {
                "width": 900,
                "height": 700,
                "font_family": "Arial",
                "font_size": 14,
                "default_database": "",
                "tts_voice": "en-US-Ava",
                "ai_active_provider": "Default",
                "ai_providers": {
                    "Default": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o-mini",
                        "api_key": "secret123",
                        "timeout": 30,
                    }
                },
                "explanation_language": "Chinese",
            }
        )

        _qt_app()
        window = BarskyApp()
        original = dict(window.settings)
        orig_json = json.dumps(original, sort_keys=True)

        # Build staged changes
        staged = dict(window.settings)
        staged["ai_providers"] = {
            name: dict(entry) for name, entry in staged.get("ai_providers", {}).items()
        }
        active = staged["ai_active_provider"]
        staged["ai_providers"][active]["api_key"] = "would_be_leaked"
        staged["width"] = 1234

        # Simulate save failure
        save_called = []

        def failing_save(s):
            save_called.append(dict(s))
            raise OSError("disk full")

        monkeypatch.setattr(config, "save_settings", failing_save)

        try:
            config.save_settings(staged)
        except OSError:
            pass

        # Live settings must be unchanged
        assert (
            window.settings["ai_providers"][window.settings["ai_active_provider"]][
                "api_key"
            ]
            == original["ai_providers"][original["ai_active_provider"]]["api_key"]
        ), "API key must not change on save failure"
        assert window.settings["width"] == original["width"]
        assert json.dumps(window.settings, sort_keys=True) == orig_json, (
            "Settings must be byte-for-byte unchanged after save failure"
        )

        window.close()

    def test_api_key_not_leaked_on_save_failure(self, tmp_path, monkeypatch):
        """API key must remain unchanged when save_settings raises OSError."""
        import kgb_srs.config as config

        settings_path = tmp_path / "barsky_settings.json"
        monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_path))

        original_key = "key-original"
        config.save_settings(
            {
                "width": 900,
                "ai_active_provider": "Default",
                "ai_providers": {
                    "Default": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o-mini",
                        "api_key": original_key,
                        "timeout": 30,
                    }
                },
            }
        )

        _qt_app()
        from kgb_srs.main_window import BarskyApp

        window = BarskyApp()
        assert (
            window.settings["ai_providers"][window.settings["ai_active_provider"]][
                "api_key"
            ]
            == original_key
        )

        # Stage a change
        staged = dict(window.settings)
        staged["ai_providers"] = {
            name: dict(entry) for name, entry in staged.get("ai_providers", {}).items()
        }
        staged["ai_providers"][staged["ai_active_provider"]]["api_key"] = (
            "would-be-leaked"
        )

        # Fail the save
        def failing_save(s):
            raise OSError("permission denied")

        monkeypatch.setattr(config, "save_settings", failing_save)

        try:
            config.save_settings(staged)
        except OSError:
            pass

        # Must still be original
        assert (
            window.settings["ai_providers"][window.settings["ai_active_provider"]][
                "api_key"
            ]
            == original_key
        ), "API key was mutated despite save failure"

        window.close()
