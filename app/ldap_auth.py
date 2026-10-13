"""Read-only LDAP search followed by a user bind over verified TLS."""
import logging
import ssl
from urllib.parse import urlsplit

from .config import settings

log = logging.getLogger(__name__)


def validate_ldap_settings():
    if settings.web_auth_provider != "ldap":
        return
    url = urlsplit(settings.ldap_url)
    if (url.scheme not in {"ldap", "ldaps"} or not url.hostname or url.username
            or url.password or url.path not in {"", "/"} or url.query or url.fragment):
        raise ValueError("LDAP_URL must be an ldap:// or ldaps:// server URL without credentials or path")
    if not all((settings.ldap_bind_dn, settings.ldap_bind_password, settings.ldap_user_base_dn)):
        raise ValueError("LDAP requires bind DN, bind password and user base DN")
    if settings.ldap_user_filter.count("{username}") != 1:
        raise ValueError("LDAP_USER_FILTER must contain one {username} placeholder")
    if len(settings.web_password) < 32:
        raise ValueError("LDAP mode requires a random WEB_PASSWORD of at least 32 characters to sign sessions")
    ssl.create_default_context(cafile=settings.ldap_ca_file or None)


def authenticate_ldap(username, password):
    if not username or not password:
        return False
    from ldap3 import Server, Connection, Tls, SUBTREE
    from ldap3.utils.conv import escape_filter_chars
    from ldap3.core.exceptions import LDAPException

    connections = []
    try:
        validate_ldap_settings()
        url = urlsplit(settings.ldap_url)
        secure = url.scheme == "ldaps"
        server = Server(url.hostname, port=url.port or (636 if secure else 389), use_ssl=secure,
                        tls=Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=settings.ldap_ca_file or None),
                        connect_timeout=settings.ldap_timeout_seconds)
        options = dict(auto_bind=False, auto_referrals=False, read_only=True,
                       receive_timeout=settings.ldap_timeout_seconds, raise_exceptions=True)
        # Explicit open/TLS/bind keeps connections available for cleanup on failure.
        def connect(dn, secret):
            connection = Connection(server, user=dn, password=secret, **options)
            connections.append(connection)
            connection.open()
            if not secure and not connection.start_tls():
                raise ValueError("LDAP StartTLS failed")
            if not connection.bind():
                raise ValueError("LDAP bind failed")
            return connection
        search = connect(settings.ldap_bind_dn, settings.ldap_bind_password)
        search.search(settings.ldap_user_base_dn,
                      settings.ldap_user_filter.replace("{username}", escape_filter_chars(username)),
                      search_scope=SUBTREE, attributes=["1.1"], size_limit=2,
                      time_limit=settings.ldap_timeout_seconds)
        if search.result.get("result") != 0 or len(search.entries) != 1:
            return False
        connect(search.entries[0].entry_dn, password)
        return True
    except (LDAPException, OSError, ValueError) as exc:
        log.warning("ldap_auth_failed error=%s", type(exc).__name__)
        return False
    finally:
        for connection in reversed(connections):
            try:
                connection.unbind()
            except Exception:
                pass
