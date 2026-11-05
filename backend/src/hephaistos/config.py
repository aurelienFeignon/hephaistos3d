"""Configuration validée au démarrage (P2.1, architecture.md §12).

Une valeur invalide arrête le service en nommant la variable telle qu'on l'écrit dans `.env`.
Référence commentée : `.env.example`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class ConfigError(Exception):
    """Configuration refusée ; le message nomme chaque variable fautive."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", frozen=True)

    hephaistos_data_dir: Path = Path("/data")
    hephaistos_models_root: Path = Path("/models")

    gpu_workers: int = Field(1, ge=0)
    gpu_max_concurrent_jobs: int = Field(1, ge=1)
    gpu_min_free_vram_mb: int = Field(8192, ge=0)
    gpu_vram_limit_mb: int = Field(0, ge=0)  # 0 = aucun plafond ; ≤ VRAM totale, vérifié au démarrage du worker
    gpu_wait_timeout_seconds: int = Field(0, ge=0)  # 0 = attendre indéfiniment
    model_idle_unload_seconds: int = Field(900, ge=0)  # 0 = jamais
    dispatch_paused: bool = False

    idol_exec_mode: Literal["full", "no_compile", "eager"] = "full"
    idol_dinov3_dtype: Literal["bf16", "fp32"] = "bf16"

    job_max_retries: int = Field(3, ge=0, le=10)
    job_retry_backoff_seconds: int = Field(30, ge=1, le=3600)
    job_retry_backoff_max_seconds: int = 900
    job_lease_seconds: int = Field(60, ge=15, le=600)
    job_timeout_seconds: int = Field(1800, gt=0)
    worker_stop_grace_seconds: int = Field(120, gt=0)
    worker_shutdown_grace_seconds: int = Field(90, gt=0)  # < stop_grace_period de Compose

    storage_retention_days: int = Field(30, ge=0)
    storage_cleanup_interval_hours: int = Field(24, ge=1, le=168)
    upload_max_bytes: int = Field(20_971_520, ge=MIB, le=100 * MIB)
    upload_max_pixels: int = Field(40_000_000, gt=0)

    api_access_token: SecretStr | None = None
    api_allowed_hosts: str = "localhost,127.0.0.1,[::1],api"
    api_allowed_origins: str = "http://localhost:3030,http://127.0.0.1:3030,http://[::1]:3030"
    api_json_max_bytes: int = Field(65536, ge=1024, le=1048576)
    api_request_timeout_seconds: float = Field(30, gt=0, le=300)
    upload_max_concurrent: int = Field(1, ge=1, le=8)
    queue_max_active: int = Field(100, ge=1, le=10000)
    storage_max_bytes: int = Field(20 * 1024**3, gt=0)
    storage_min_free_bytes: int = Field(1024**3, ge=0)
    generation_result_budget_bytes: int = Field(128 * 1024**2, ge=128 * 1024**2)
    logs_tail_max_bytes: int = Field(1048576, ge=1024, le=16 * 1048576)

    enable_fake_adapter: bool = False

    @field_validator("api_access_token", mode="before")
    @classmethod
    def blank_token(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _backoff_ceiling(self) -> Settings:
        if "*" in self.api_allowed_hosts or not self.api_allowed_hosts.strip():
            raise ValueError("API_ALLOWED_HOSTS must contain explicit hostnames")
        if self.api_access_token is not None:
            import re

            if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", self.api_access_token.get_secret_value()):
                raise ValueError("API_ACCESS_TOKEN must contain 32 to 512 URL-safe characters")
        from urllib.parse import urlsplit

        for origin in self.api_allowed_origins.split(","):
            parsed = urlsplit(origin.strip())
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("API_ALLOWED_ORIGINS must contain explicit HTTP origins without paths")
        if self.worker_stop_grace_seconds <= self.worker_shutdown_grace_seconds:
            raise ValueError("WORKER_STOP_GRACE_SECONDS must exceed WORKER_SHUTDOWN_GRACE_SECONDS")
        if self.job_retry_backoff_max_seconds < self.job_retry_backoff_seconds:
            raise ValueError(
                f"JOB_RETRY_BACKOFF_MAX_SECONDS ({self.job_retry_backoff_max_seconds}) doit être "
                f"≥ JOB_RETRY_BACKOFF_SECONDS ({self.job_retry_backoff_seconds})"
            )
        return self

    def warnings(self) -> list[str]:
        """Réglages admis mais probablement involontaires."""
        if self.gpu_max_concurrent_jobs > self.gpu_workers:
            return [
                f"GPU_MAX_CONCURRENT_JOBS ({self.gpu_max_concurrent_jobs}) > GPU_WORKERS ({self.gpu_workers}) : "
                "certains jobs ne trouveront jamais de worker"
            ]
        return []


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            variable = ".".join(str(part) for part in error["loc"]).upper()
            lines.append(f"{variable} : {error['msg']}" if variable else error["msg"])
        raise ConfigError("configuration invalide\n  " + "\n  ".join(lines)) from exc
