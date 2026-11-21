"""Tiny server-side i18n helper.

Every user-facing text produced by the server is a *Msg*: a dict ``{"pt": "...", "en": "..."}``.
The browser picks the language, so switching it never needs another request to the server.

To add a language:
  1. append its code to LANGS,
  2. add a dict for it in ``messages.py`` (copy EN and translate),
  3. add ``obd2dash/kb/curated.<lang>.txt`` (copy the English file and translate),
  4. add the UI strings in ``web/i18n.js``.
The test-suite checks that every catalogue has exactly the same keys.
"""
from __future__ import annotations

LANGS = ("pt", "en")
FALLBACK = "en"


class Msg(dict):
    """``{"pt": ..., "en": ...}``. ``str(msg)`` returns English (used for log files and exception text)."""

    def __str__(self) -> str:
        return self.get(FALLBACK) or next(iter(self.values()), "")


def _resolve(value, lang: str):
    if isinstance(value, dict):
        return value.get(lang) or value.get(FALLBACK) or next(iter(value.values()), "")
    return value


def msg(key: str, **params) -> Msg:
    """Build a Msg from the catalogue. Parameter values may themselves be Msg objects."""
    from .messages import CATALOGS

    out = Msg()
    for lang in LANGS:
        template = CATALOGS[lang].get(key)
        if template is None:
            template = CATALOGS[FALLBACK][key]
        try:
            out[lang] = template.format(**{k: _resolve(v, lang) for k, v in params.items()})
        except (KeyError, IndexError, ValueError):
            out[lang] = template
    return out


def literal(text: str) -> Msg:
    """Wrap a text that needs no translation (device names, raw error text...)."""
    return Msg({lang: text for lang in LANGS})


def as_msg(value) -> Msg:
    if isinstance(value, Msg):
        return value
    if isinstance(value, dict):
        return Msg(value)
    return literal(str(value))


def pick(value, lang: str = FALLBACK) -> str:
    """Return the text of ``value`` (Msg, dict or str) in ``lang``."""
    return "" if value is None else _resolve(value, lang)


def tr(key: str, lang: str, **params) -> str:
    """Shortcut: catalogue text already rendered in one language."""
    return pick(msg(key, **params), lang)


class LocalizedError(Exception):
    """Exception whose message can be shown to the user in any language (``.msg``)."""

    def __init__(self, message=""):
        self.msg = as_msg(message)
        super().__init__(str(self.msg))


class BadRequest(LocalizedError):
    """The request is invalid (HTTP 400)."""


class Conflict(LocalizedError):
    """The request cannot be served in the current state (HTTP 409)."""


class NotConnected(Conflict):
    """There is no open connection to the adapter."""
