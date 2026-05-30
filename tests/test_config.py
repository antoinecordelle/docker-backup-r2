import pytest

from backup_r2.config import ConfigError, load_config


def _base_env(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "backups")


def test_minimal_valid_config(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("KEEP_LAST", "5")
    cfg = load_config()
    assert cfg.bucket == "backups"
    assert cfg.endpoint_url == "https://acct123.r2.cloudflarestorage.com"
    assert cfg.retention.keep_last == 5
    assert cfg.interval_seconds == 3600


def test_missing_credential_raises(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("KEEP_LAST", "5")
    with pytest.raises(ConfigError, match="R2_ACCESS_KEY_ID"):
        load_config()


def test_no_retention_rule_raises(monkeypatch):
    _base_env(monkeypatch)
    with pytest.raises(ConfigError, match="retention rule"):
        load_config()


def test_invalid_regex_raises(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("KEEP_LAST", "5")
    monkeypatch.setenv("BACKUP_TIMESTAMP_REGEX", "(unclosed")
    with pytest.raises(ConfigError, match="valid regex"):
        load_config()


def test_negative_int_raises(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("KEEP_LAST", "-1")
    with pytest.raises(ConfigError, match=">= 0"):
        load_config()


def test_bool_parsing(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("KEEP_LAST", "1")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("RUN_ONCE", "yes")
    cfg = load_config()
    assert cfg.dry_run is True
    assert cfg.run_once is True
