'use strict';

const SENSITIVE_ASSIGNMENT = /(["']?\b(?:api[_-]?(?:key|secret)|secret[_-]?key|authorization|cookie|session(?:[_-]?token)?|access[_-]?token|refresh[_-]?token|signature|sign|encryption[_-]?key|ciphertext)\b["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;&]+)/gi;
const BEARER = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const FERNET = /\bgAAAA[A-Za-z0-9_-]{20,}\b/g;
const V2_CREDENTIAL = /\bv2:(?:[A-Fa-f0-9]{16,}:){3}[A-Fa-f0-9]{16,}\b/g;

function redactSensitive(value) {
    return String(value ?? '')
        .replace(BEARER, 'Bearer [REDACTED]')
        .replace(SENSITIVE_ASSIGNMENT, (_match, prefix, original) => {
            const replacement = original.startsWith('"')
                ? '"[REDACTED]"'
                : original.startsWith("'")
                    ? "'[REDACTED]'"
                    : '[REDACTED]';
            return `${prefix}${replacement}`;
        })
        .replace(FERNET, '[REDACTED_CREDENTIAL]')
        .replace(V2_CREDENTIAL, '[REDACTED_CREDENTIAL]');
}

function safeMetadata(metadata) {
    if (metadata === null || metadata === undefined) return null;
    const serialized = typeof metadata === 'string'
        ? metadata
        : JSON.stringify(metadata);
    return redactSensitive(serialized);
}

module.exports = { redactSensitive, safeMetadata };
