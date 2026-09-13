import ssl
from urllib.parse import urlsplit

from .config import settings
from sqlalchemy.engine import make_url


def validate_database_settings():
    if settings.deployment_mode != "production":
        return
    try:
        url = make_url(settings.database_url)
    except Exception:
        raise ValueError("Invalid production DATABASE_URL") from None
    if url.drivername != "postgresql+psycopg" or not all((url.host, url.username, url.password, url.database)):
        raise ValueError("Production requires a complete PostgreSQL URL")
    if settings.database_mode == "internal":
        if url.host != "postgres" or url.port not in (None, 5432) or url.query:
            raise ValueError("Internal database mode is restricted to the bundled postgres service")
        return
    if settings.database_mode != "external":
        raise ValueError("DATABASE_MODE must be external or internal")
    # Forbid libpq parameters that can override the validated endpoint or TLS.
    if set(url.query) - {"sslmode", "sslrootcert", "connect_timeout", "application_name"}:
        raise ValueError("Unsupported external database connection parameter")
    if any(not isinstance(value, str) for value in url.query.values()):
        raise ValueError("Duplicate database connection parameters are forbidden")
    if url.query.get("sslmode") not in {"verify-ca", "verify-full"}:
        raise ValueError("External database requires sslmode=verify-ca or verify-full")


def tls_verification():
    if not settings.verify_tls:
        return False
    context = ssl.create_default_context()
    if settings.tls_ca_file:
        context.load_verify_locations(cafile=settings.tls_ca_file)
    return context


def validate_runtime_settings():
    validate_database_settings()
    if not 1 <= settings.teamcity_build_limit <= 100:
        raise ValueError("TEAMCITY_BUILD_LIMIT must be between 1 and 100")
    if settings.deployment_mode not in {"lab", "production"}:
        raise ValueError("DEPLOYMENT_MODE must be lab or production")
    if settings.deployment_mode != "production":
        return
    if settings.app_mode != "live":
        raise ValueError("Production requires APP_MODE=live")
    if not settings.verify_tls or not settings.web_auth_enabled or not settings.web_cookie_secure:
        raise ValueError("Production requires TLS verification, web authentication and Secure cookies")
    if len(settings.web_password) < 32:
        raise ValueError("Production WEB_PASSWORD must contain at least 32 characters")
    if settings.metrics_token and (len(settings.metrics_token) < 32 or settings.metrics_token == settings.web_password):
        raise ValueError("METRICS_TOKEN must be a separate secret of at least 32 characters")
    if not settings.bitbucket_token or not settings.teamcity_token:
        raise ValueError("Production requires read-only Bitbucket and TeamCity tokens")
    urls = {"TEAMCITY_URL": settings.teamcity_url, "TEAMCITY_PUBLIC_URL": settings.teamcity_public_url}
    if settings.bitbucket_provider == "datacenter":
        urls["BITBUCKET_URL"] = settings.bitbucket_url
    if settings.registry_enabled:
        urls.update(REGISTRY_URL=settings.registry_url, REGISTRY_PUBLIC_URL=settings.registry_public_url)
    for name, value in urls.items():
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{name} must be an HTTPS base URL without credentials, query or fragment")
    # Fail before starting a scan if the provided corporate CA cannot be loaded.
    tls_verification()
