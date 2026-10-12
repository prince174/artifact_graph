"""Application-only debug logging; do not enable HTTP header/body tracing."""
import logging

from .config import settings


class SafeMessages(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        for name in ("bitbucket_token", "teamcity_token", "web_password", "metrics_token",
                     "webhook_secret", "registry_token", "database_url"):
            secret = getattr(settings, name, "")
            if secret:
                message = message.replace(secret, "[REDACTED]")
        record.msg, record.args = message, ()
        # Exception messages and tracebacks can contain URLs/credentials.
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def configure_logging(level=None):
    logger = logging.getLogger("app")
    logger.setLevel(level or settings.log_level)
    if not any(getattr(handler, "artifact_graph", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.artifact_graph = True
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.addFilter(SafeMessages())
        logger.addHandler(handler)
    logger.propagate = False
