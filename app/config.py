from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
import re
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_mode: str = "demo"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"
    deployment_mode: str = "lab"
    tls_ca_file: str = ""
    database_url: str = "sqlite:///./graph.db"
    database_mode: str = "external"
    refresh_minutes: int = 60
    scan_history_limit: int = 100
    incremental_cache_size: int = 2000
    persistent_cache_ttl_hours: int = 168
    persistent_cache_max_rows: int = 5000
    snapshot_history_limit: int = 50
    api_retry_attempts: int = 4
    api_retry_backoff_seconds: float = 0.5
    bitbucket_provider: str = "cloud"
    bitbucket_project_keys: str = ""
    bitbucket_url: str = "http://localhost:7990"
    bitbucket_workspace: str = ""
    bitbucket_email: str = ""
    bitbucket_auth: str = "api_token"
    bitbucket_token: str = ""
    teamcity_url: str = "http://localhost:8111"
    teamcity_public_url: str = "http://localhost:8111"
    teamcity_token: str = ""
    teamcity_build_limit: int = 3
    teamcity_build_type_ids: str = ""
    registry_enabled: bool = False
    registry_provider: str = "docker"
    registry_url: str = "http://localhost:5000"
    registry_public_url: str = "http://localhost:5000"
    registry_username: str = ""
    registry_token: str = ""
    verify_tls: bool = True
    web_auth_enabled: bool = True
    metrics_token: str = ""
    web_username: str = "root"
    web_password: str = ""
    web_session_hours: int = 12
    web_cookie_secure: bool = False
    web_login_attempts: int = 5
    web_login_window_seconds: int = 300
    mapping_rules_path: str = ""
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_verify_tls: bool = True
    webhook_allow_http: bool = False
    webhook_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 6

    @property
    def selected_bitbucket_projects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(key.strip() for key in self.bitbucket_project_keys.split(",") if key.strip()))

    @field_validator("teamcity_build_type_ids")
    @classmethod
    def validate_build_type_ids(cls, value):
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", item.strip())
               for item in value.split(",") if item.strip()):
            raise ValueError("TEAMCITY_BUILD_TYPE_IDS must contain comma-separated configuration IDs")
        return value

    @property
    def selected_teamcity_build_types(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip() for item in self.teamcity_build_type_ids.split(",") if item.strip()))


settings = Settings()
