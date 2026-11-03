"""Configuration (P2.1, architecture.md §12) : une valeur invalide arrête le service en nommant la variable."""

from pathlib import Path

import pytest

from hephaistos.config import ConfigError, Settings, load_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(Settings.model_fields):
        monkeypatch.delenv(name.upper(), raising=False)


def test_defaults_are_those_of_the_architecture():
    settings = load_settings()
    assert settings.gpu_max_concurrent_jobs == 1
    assert settings.gpu_min_free_vram_mb == 8192
    assert settings.gpu_vram_limit_mb == 0
    assert settings.idol_exec_mode == "full"
    assert settings.job_max_retries == 3
    assert settings.job_retry_backoff_seconds == 30
    assert settings.job_lease_seconds == 60
    assert settings.upload_max_bytes == 20_971_520
    assert settings.upload_max_pixels == 40_000_000
    assert settings.enable_fake_adapter is False
    assert settings.hephaistos_data_dir == Path("/data")
    assert settings.hephaistos_models_root == Path("/models")


def test_values_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("JOB_MAX_RETRIES", "5")
    monkeypatch.setenv("DISPATCH_PAUSED", "true")
    settings = load_settings()
    assert settings.job_max_retries == 5
    assert settings.dispatch_paused is True


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        # A2.9, mot pour mot
        ("GPU_MAX_CONCURRENT_JOBS", "0"),
        ("JOB_MAX_RETRIES", "-1"),
        ("GPU_VRAM_LIMIT_MB", "-1"),
        # bornes de la table §12
        ("GPU_WORKERS", "-1"),
        ("GPU_MIN_FREE_VRAM_MB", "-1"),
        ("JOB_MAX_RETRIES", "11"),
        ("JOB_RETRY_BACKOFF_SECONDS", "0"),
        ("JOB_LEASE_SECONDS", "14"),
        ("JOB_LEASE_SECONDS", "601"),
        ("JOB_TIMEOUT_SECONDS", "0"),
        ("STORAGE_RETENTION_DAYS", "-1"),
        ("STORAGE_CLEANUP_INTERVAL_HOURS", "0"),
        ("STORAGE_CLEANUP_INTERVAL_HOURS", "169"),
        ("UPLOAD_MAX_BYTES", "1000"),
        ("UPLOAD_MAX_BYTES", "104857601"),
        ("UPLOAD_MAX_PIXELS", "0"),
        ("IDOL_EXEC_MODE", "turbo"),
        ("IDOL_DINOV3_DTYPE", "fp16"),
        ("DISPATCH_PAUSED", "peut-être"),
        ("GPU_WAIT_TIMEOUT_SECONDS", "deux"),
    ],
)
def test_invalid_value_is_refused_naming_the_variable(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ConfigError) as caught:
        load_settings()
    assert variable in str(caught.value)


def test_every_invalid_variable_is_named_at_once(monkeypatch):
    monkeypatch.setenv("GPU_MAX_CONCURRENT_JOBS", "0")
    monkeypatch.setenv("JOB_LEASE_SECONDS", "1")
    with pytest.raises(ConfigError) as caught:
        load_settings()
    assert "GPU_MAX_CONCURRENT_JOBS" in str(caught.value)
    assert "JOB_LEASE_SECONDS" in str(caught.value)


def test_backoff_ceiling_below_backoff_is_refused(monkeypatch):
    monkeypatch.setenv("JOB_RETRY_BACKOFF_SECONDS", "60")
    monkeypatch.setenv("JOB_RETRY_BACKOFF_MAX_SECONDS", "30")
    with pytest.raises(ConfigError, match="JOB_RETRY_BACKOFF_MAX_SECONDS"):
        load_settings()


def test_more_concurrent_jobs_than_workers_is_a_warning_not_an_error(monkeypatch):
    monkeypatch.setenv("GPU_WORKERS", "1")
    monkeypatch.setenv("GPU_MAX_CONCURRENT_JOBS", "2")
    settings = load_settings()
    assert any("GPU_MAX_CONCURRENT_JOBS" in warning for warning in settings.warnings())


def test_env_example_is_valid_and_complete(monkeypatch):
    # .env.example est la référence commentée (§12) : il doit passer la validation et couvrir tout.
    declared = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            declared[key] = value
    for key, value in declared.items():
        monkeypatch.setenv(key, value)
    load_settings()
    assert {name.upper() for name in Settings.model_fields} <= set(declared)
