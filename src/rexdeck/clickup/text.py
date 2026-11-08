"""Render untrusted ClickUp text without terminal or invisible-format commands."""

from __future__ import annotations

import re
import unicodedata

_ESCAPE = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|.)")


def clean(value: str) -> str:
    """Preserve readable multiline content, not terminal or Unicode formatting."""
    value = _ESCAPE.sub("", value)
    return "".join(
        character
        for character in value
        if unicodedata.category(character) != "Cf"
        and (
            character in "\n\t"
            or (ord(character) >= 32 and not 127 <= ord(character) <= 159)
        )
    )


def single_line(value: str) -> str:
    """Keep labels and identifiers on one visible line."""
    return clean(
        value.replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
