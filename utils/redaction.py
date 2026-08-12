"""Conservative redaction for messages persisted or emitted as logs."""
import re
from typing import Any


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?(?:key|secret)|secret[_-]?key|authorization|"
    r"cookie|session(?:[_-]?token)?|access[_-]?token|refresh[_-]?token|"
    r"signature|sign|encryption[_-]?key|ciphertext)\b[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_FERNET = re.compile(r"\bgAAAA[A-Za-z0-9_-]{20,}\b")
_V2_CREDENTIAL = re.compile(
    r"\bv2:(?:[A-Fa-f0-9]{16,}:){3}[A-Fa-f0-9]{16,}\b")


def redact_sensitive(value: Any) -> str:
    text = str(value if value is not None else '')
    text = _BEARER.sub('Bearer [REDACTED]', text)
    def replace_assignment(match: re.Match) -> str:
        original = match.group(2)
        if original.startswith('"'):
            replacement = '"[REDACTED]"'
        elif original.startswith("'"):
            replacement = "'[REDACTED]'"
        else:
            replacement = '[REDACTED]'
        return f"{match.group(1)}{replacement}"

    text = _SENSITIVE_ASSIGNMENT.sub(replace_assignment, text)
    text = _FERNET.sub('[REDACTED_CREDENTIAL]', text)
    return _V2_CREDENTIAL.sub('[REDACTED_CREDENTIAL]', text)
