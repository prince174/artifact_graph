"""Resolve provider API links without losing context paths or leaking credentials."""

from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit


def _split_url(value: str) -> SplitResult:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("API URL must be a nonempty string without surrounding whitespace")
    if "\\" in value or "#" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("API URL must not contain backslashes, fragments, or control characters")
    try:
        parsed = urlsplit(value)
        # Accessing port validates malformed or out-of-range port numbers.
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid API URL") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("API URL must not contain credentials")
    decoded_path = unquote(parsed.path)
    if (
        "\\" in decoded_path
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded_path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        raise ValueError("API URL must not contain path traversal or control characters")
    return parsed


def _base_url(value: str) -> SplitResult:
    parsed = _split_url(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query:
        raise ValueError("API base URL must be an absolute HTTP(S) URL without a query")
    return parsed


def _origin(parsed: SplitResult) -> tuple[str, str, int]:
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname or "", port


def _in_context(path: str, context: str) -> bool:
    return not context or path == context or path.startswith(context + "/")


def resolve_api_url(base_url: str, path: str) -> str:
    """Resolve API paths and same-origin hrefs against a configured server URL.

    Relative paths inherit the configured context, including when they begin
    with a slash. Already context-prefixed paths keep their existing prefix.
    Absolute same-origin links keep their path; links to another origin fail.
    """
    base, target = _base_url(base_url), _split_url(path)
    if path.startswith("//") or (target.netloc and not target.scheme):
        raise ValueError("Protocol-relative API URLs are not allowed")
    if target.scheme:
        if target.scheme not in {"http", "https"} or not target.hostname or _origin(target) != _origin(base):
            raise ValueError("API URL must use the configured server origin")
        resolved_path = target.path or "/"
    else:
        context = base.path.rstrip("/")
        target_path = "/" + target.path.lstrip("/")
        resolved_path = target_path if _in_context(target_path, context) else context + target_path
    return urlunsplit((base.scheme, base.netloc, resolved_path, target.query, ""))


def public_api_url(internal_base: str, public_base: str, href: str) -> str:
    """Replace an internal API context with the configured public URL context."""
    internal, public = _base_url(internal_base), _base_url(public_base)
    resolved = urlsplit(resolve_api_url(internal_base, href))
    context = internal.path.rstrip("/")
    if not _in_context(resolved.path, context):
        raise ValueError("API URL is outside the configured server context")
    relative_path = resolved.path[len(context):].lstrip("/")
    public_path = public.path.rstrip("/") + "/" + relative_path
    return urlunsplit((public.scheme, public.netloc, public_path, resolved.query, ""))
