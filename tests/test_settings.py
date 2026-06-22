"""Unit tests for settings.py — the load path that must tolerate unknown
keys (forward-compat for new config fields) and a UTF-8 BOM (PowerShell
writes one), plus the autotopup_enabled convenience.

All file I/O lands in the isolated temp APPDATA from conftest.
"""
import json

from settings import Settings, settings_path


def _write_settings(text, encoding="utf-8"):
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding=encoding)
    return p


def test_defaults_on_first_run_and_file_created():
    s = Settings.load()
    assert s.api_key == ""
    assert s.key_refresh_seconds == 60
    assert s.balance_warning == 5.0
    # load() persists defaults on first run.
    assert settings_path().exists()


def test_unknown_keys_are_dropped_not_crashed():
    _write_settings(json.dumps({
        "api_key": "sk-or-test",
        "balance_warning": 3.0,
        "some_future_field": {"nested": True},  # must be ignored gracefully
        "another_unknown": 42,
    }))
    s = Settings.load()
    assert s.api_key == "sk-or-test"
    assert s.balance_warning == 3.0
    assert not hasattr(s, "some_future_field")


def test_missing_keys_fall_back_to_defaults():
    _write_settings(json.dumps({"api_key": "sk-or-test"}))
    s = Settings.load()
    assert s.api_key == "sk-or-test"
    assert s.key_refresh_seconds == 60  # default preserved


def test_utf8_bom_is_tolerated():
    # PowerShell's `Set-Content -Encoding utf8` writes a BOM that plain
    # json.loads rejects; the loader must use utf-8-sig.
    _write_settings(json.dumps({"api_key": "sk-or-bom"}), encoding="utf-8-sig")
    s = Settings.load()
    assert s.api_key == "sk-or-bom"


def test_corrupt_json_returns_defaults_not_crash():
    _write_settings("{ this is not valid json ")
    s = Settings.load()
    assert s.api_key == ""  # fell back to defaults


def test_autotopup_enabled_requires_both_threshold_and_amount():
    assert Settings(auto_topup_threshold=2, auto_topup_amount=25).autotopup_enabled
    assert not Settings(auto_topup_threshold=2, auto_topup_amount=0).autotopup_enabled
    assert not Settings(auto_topup_threshold=0, auto_topup_amount=25).autotopup_enabled


def test_save_then_load_roundtrip_preserves_tracked_models():
    s = Settings(api_key="k", tracked_models=["anthropic/claude-sonnet-4.6", "openai/gpt-5"])
    s.save()
    reloaded = Settings.load()
    assert reloaded.tracked_models == ["anthropic/claude-sonnet-4.6", "openai/gpt-5"]
