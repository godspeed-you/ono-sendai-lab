"""Secret redaction for every lab output path (LAB-070).

Redaction is applied to JSON results, error messages, human output, logs and
evidence. It is pattern based and deliberately aggressive: a false positive
costs readability, a false negative leaks a key.
"""

from __future__ import annotations

import re

REDACTED = "<redacted>"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM blocks (private keys, certificates with keys)
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S), REDACTED),
    # Authorization headers and SigV4 parts
    (re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n\"']+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(x-amz-(?:signature|credential|security-token)\s*[=:]\s*)[^&\s\"',;]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b(Signature\s*=\s*)[0-9a-f]{16,}"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b(Credential\s*=\s*)[^,\s\"']+"), r"\1" + REDACTED),
    # Shared-credentials style assignments
    (
        re.compile(r"(?i)\b(aws_(?:secret_access_key|access_key_id|session_token)\s*[=:]\s*)\S+"),
        r"\1" + REDACTED,
    ),
    (
        re.compile(r"(?i)(\"?(?:SecretAccessKey|AccessKeyId|SessionToken|secret_key|access_key)\"?\s*[:=]\s*\"?)[^\s\",}]+"),
        r"\1" + REDACTED,
    ),
    # Emulator access keys: EMULATOR + 12 hex; AWS-style key ids
    (re.compile(r"\bEMULATOR[0-9A-F]{12}\b"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b"), REDACTED),
    # 40-character secret-like tokens (emulator secrets are 40 uppercase hex)
    (re.compile(r"(?<![A-Za-z0-9/+=])[0-9A-F]{40}(?![A-Za-z0-9/+=])"), REDACTED),
    # Long opaque tokens (session tokens, signed blobs)
    (re.compile(r"(?<![A-Za-z0-9/+=_-])[A-Za-z0-9/+=_-]{120,}"), REDACTED),
]

# AWS-style secret keys: 40 base64-ish characters with at least one uppercase
# letter. Lowercase-only hex (Git commit IDs) stays readable.
_SECRET40 = re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])")


def _secret40(match: re.Match[str]) -> str:
    token = match.group(0)
    return REDACTED if any(c.isupper() for c in token) else token


_SENSITIVE_KEYS = re.compile(
    r"(?i)^(authorization|secret|secret_access_key|secretaccesskey|aws_secret_access_key|"
    r"session_token|sessiontoken|aws_session_token|password|token|x-amz-security-token|"
    r"x-amz-signature|access_key_id|accesskeyid|aws_access_key_id|credentials)$"
)


def redact_text(text: str) -> str:
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return _SECRET40.sub(_secret40, out)


def redact_obj(obj):
    """Return a deep copy of ``obj`` with secrets redacted."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if isinstance(key, str) and _SENSITIVE_KEYS.match(key) and value not in (None, "", [], {}):
                result[key] = REDACTED
            else:
                result[key] = redact_obj(value)
        return result
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
