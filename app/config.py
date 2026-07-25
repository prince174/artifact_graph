from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_mode: str = "demo"
    database_url: str = "sqlite:///./graph.db"
    refresh_minutes: int = 60
    scan_history_limit: int = 100
    incremental_cache_size: int = 2000
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
    registry_enabled: bool = False
    registry_provider: str = "docker"
    registry_url: str = "http://localhost:5000"
    registry_public_url: str = "http://localhost:5000"
    registry_username: str = ""
    registry_token: str = ""
    verify_tls: bool = True


settings = Settings()
