"""Neutralise caller-supplied text before it is stored or placed into a model prompt.

Anything a caller or web form can influence (names, qualification answers, extracted custom
fields, notes) is untrusted. It is flattened to a single short line, stripped of markup that
could close our prompt delimiters, and capped in length and count. The prompt then wraps it in
a <caller_data> block that the model is told to treat as data only.
"""

import re
from typing import Any

from src.config import get_settings

_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]")
_MARKUP = re.compile(r"[<>{}\[\]`]")
_SPACES = re.compile(r"\s+")
_KEY = re.compile(r"[^a-z0-9_]+")
_NAME = re.compile(r"[^A-Za-zÀ-ÖØ-öø-ɏ'\- ]+")

MAX_FIELDS = 20
MAX_KEY_CHARS = 40


def clean_text(value: Any, max_chars: int | None = None) -> str:
    limit = max_chars or get_settings().max_caller_field_chars
    text = _CONTROL.sub(" ", str(value))
    text = _MARKUP.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    return text[:limit].rstrip()


def clean_key(key: Any) -> str:
    return _KEY.sub("_", str(key).lower()).strip("_")[:MAX_KEY_CHARS]


def clean_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = _SPACES.sub(" ", _NAME.sub("", name)).strip()[:60]
    return cleaned or None


def clean_fields(fields: dict[str, Any] | None, max_chars: int | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (fields or {}).items():
        k, v = clean_key(key), clean_text(value, max_chars)
        if k and v and len(out) < MAX_FIELDS:
            out[k] = v
    return out
