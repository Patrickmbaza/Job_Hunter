from functools import lru_cache
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "sqlite:///./data/job_hunter.sqlite3"
    frontend_origin: str = "http://localhost:3000"
    rea_cdp_endpoint: str = "http://127.0.0.1:9224"
    rea_allowed_origins: str = "https://example.com"
    rea_mcp_config_path: Path = Path("~/.codex/config.toml")
    rea_mcp_server_name: str = "rea"
    rea_startup_timeout_seconds: int = 30
    rea_tool_timeout_seconds: int = 45
    max_cv_upload_bytes: int = 10 * 1024 * 1024
    app_password_hash: str = ""
    session_ttl_seconds: int = 28800
    low_memory_mode: bool = True
    worker_concurrency: int = 1
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    @field_validator("app_host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        try:
            if not ipaddress.ip_address(value).is_loopback:
                raise ValueError("APP_HOST must be a loopback address (127.0.0.1 or ::1)")
        except ValueError as exc:
            raise ValueError("APP_HOST must be a loopback address (127.0.0.1 or ::1)") from exc
        return value

    @field_validator("frontend_origin")
    @classmethod
    def frontend_must_be_local(cls, value: str) -> str:
        parsed = urlparse(value)
        try:
            host = parsed.hostname or ""
            local_host = host == "localhost" or ipaddress.ip_address(host).is_loopback
            port = parsed.port
        except ValueError:
            local_host = host == "localhost"
            port = None
        if (
            parsed.scheme != "http" or not local_host or port is None
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
        ):
            raise ValueError("FRONTEND_ORIGIN must be an explicit-port HTTP localhost/loopback origin")
        return value.rstrip("/")

    @field_validator("rea_cdp_endpoint")
    @classmethod
    def cdp_endpoint_loopback_only(cls, value: str) -> str:
        parsed = urlparse(value)
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
            port = parsed.port
        except ValueError as exc:
            raise ValueError("REA_CDP_ENDPOINT must be an explicit-port HTTP URL on 127.0.0.1 or ::1") from exc
        if (
            parsed.scheme != "http" or not address.is_loopback or port is None
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
        ):
            raise ValueError("REA_CDP_ENDPOINT must be an explicit-port HTTP URL on 127.0.0.1 or ::1")
        return value

    @field_validator("rea_mcp_config_path")
    @classmethod
    def expand_config_path(cls, value: Path) -> Path:
        return value.expanduser()

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.rea_allowed_origins.split(",") if origin.strip()]

    @property
    def data_directory(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data"

    @property
    def applications_directory(self) -> Path:
        return Path(__file__).resolve().parents[2] / "Applications"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_data_directory(database_url: str) -> None:
    prefix = "sqlite:///"
    if database_url.startswith(prefix) and database_url != "sqlite:///:memory:":
        path = Path(database_url[len(prefix):])
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
