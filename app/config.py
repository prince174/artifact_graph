from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_mode: str = "demo"
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
    bitbucket_url: str = "http://localhost:7990"
    bitbucket_workspace: str = ""
    bitbucket_email: str = ""
    bitbucket_auth: str = "api_token"
    bitbucket_token: str = ""
    teamcity_url: str = "http://localhost:8111"
    teamcity_public_url: str = "http://localhost:8111"
    teamcity_token: str = ""
    teamcity_build_limit: int = 3
    registry_enabled: bool = False
    registry_provider: str = "docker"
    registry_url: str = "http://localhost:5000"
    registry_public_url: str = "http://localhost:5000"
    registry_username: str = ""
    registry_token: str = ""
    verify_tls: bool = True
    web_auth_enabled: bool = True
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


settings = Settings()
