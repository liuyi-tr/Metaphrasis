"""Mask API keys, access tokens and the like so they never reach the history file in clear text."""
import re

# (regex, prefix length to keep): keep the prefix, replace the rest with ***
_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), 7),                    # Anthropic / Claude
    (re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), 3),  # OpenAI and others
    (re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"), 4),  # AWS
    (re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"), 4),       # GitHub
    (re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{30,}"), 11),
    (re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9\-]{10,}"), 5),    # Slack
    (re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{30,}"), 4),          # Google
    (re.compile(r"(?i)(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9._\-]{20,}"), 7),
]
# no fixed prefix but looks like a random key (mixed-case letters + digits, 32+ chars)
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])")
# second half of a key that OCR broke onto the next line
_TAIL = re.compile(r"^[A-Za-z0-9_\-]{12,}$")


def _random_looking(token):
    return (any(c.islower() for c in token) and any(c.isupper() for c in token)
            and any(c.isdigit() for c in token))


def redact(text):
    lines = []
    previous_hit = False
    for line in text.split("\n"):
        original = line
        for pattern, keep in _PATTERNS:
            line = pattern.sub(lambda m, k=keep: m.group(0)[:k] + "***", line)
        line = _LONG_TOKEN.sub(lambda m: "***" if _random_looking(m.group(0)) else m.group(0), line)
        hit = line != original
        stripped = original.strip()
        if not hit and previous_hit and _TAIL.match(stripped) and _random_looking(stripped):
            line, hit = original.replace(stripped, "***"), True
        previous_hit = hit and line.rstrip().endswith("***")
        lines.append(line)
    return "\n".join(lines)


def contains_secret(text):
    return redact(text) != text
