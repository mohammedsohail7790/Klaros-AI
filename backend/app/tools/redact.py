_SENSITIVE_MARKERS = ("password", "secret", "token", "credential", "api_key", "apikey", "private_key")


def redact_input(data: dict) -> dict:
    """Never let a raw credential land in an audit log (section 9)."""

    def _redact(value):
        if isinstance(value, dict):
            return {
                k: ("***REDACTED***" if any(m in k.lower() for m in _SENSITIVE_MARKERS) else _redact(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_redact(v) for v in value]
        return value

    return _redact(data)
