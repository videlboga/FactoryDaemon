"""Position name normalizer.

FactoryDaemon receives position names from spreadsheets, Telegram copy-paste,
and free-text input. This module collapses common spelling variants into one
canonical string so the planner and norm catalog can use a single key.

Examples:
    ``Л43``, ``л43``, ``Л-43``, ``Л 43`` → ``Л43``
    ``11В-11``, ``11в-11``, ``11В11``   → ``11В-11``

"""

from __future__ import annotations

import re

# Common confusables and punctuation that act as separators between
# meaningful parts of a position name.
_SEPARATORS = re.compile(r"[\s\-_]+")


def normalize_position(value: str | None) -> str:
    """Return the canonical spelling of a position name.

    Args:
        value: A raw position name. Whitespace, hyphens, underscores and case
            variants are collapsed. ``None`` or empty values are rejected.

    Returns:
        The normalized position name.

    Raises:
        ValueError: If the input is empty, ``None``, or contains only
            separators.

    """
    if value is None or not isinstance(value, str):
        raise ValueError("position must be a non-empty string")

    value = value.strip()
    if not value:
        raise ValueError("position must be a non-empty string")

    # Split on separators so "Л-43" / "Л 43" / "11В-11" become token lists.
    tokens = [t for t in _SEPARATORS.split(value) if t]
    if not tokens:
        raise ValueError("position must be a non-empty string")

    # Some spreadsheets paste the position as a single alnum token with an
    # embedded letter ("11В11"). Split such tokens into digit/letter/digit
    # chunks to handle the "11В-11" family without external separators.
    expanded_tokens: list[str] = []
    for token in tokens:
        # Split when an alphabetic block starts/ends inside an alnum token.
        parts = re.split(r"(?<=[0-9])(?=[^\W\d_])|(?<=[^\W\d_])(?=[0-9])", token)
        expanded_tokens.extend(p for p in parts if p)

    tokens = expanded_tokens
    if not tokens:
        raise ValueError("position must be a non-empty string")

    # Collapse case on letter tokens, preserve digit tokens.
    normalized_tokens: list[str] = []
    for token in tokens:
        if token.isalpha():
            normalized_tokens.append(token.upper())
        elif token.isdigit():
            normalized_tokens.append(token)
        elif token.isalnum():
            # Mixed token such as "11В" or "Л43": uppercase letters only.
            normalized_tokens.append(token.upper())
        else:
            # Drop stray punctuation.
            cleaned = re.sub(r"[^\w\d]", "", token, flags=re.UNICODE)
            if cleaned:
                normalized_tokens.append(cleaned.upper())

    if not normalized_tokens:
        raise ValueError("position must be a non-empty string")

    # Decide the canonical form based on the tokens.
    # - A single token (e.g. "Л43", "10") is returned compactly.
    # - If the first token is numeric and the second starts with a letter, we
    #   want a hyphen between them: "11" + "В" + "11" → "11В-11".
    # - If the first token starts with a letter and the rest is numeric, join
    #   without separators: "Л" + "43" → "Л43".
    if len(normalized_tokens) == 1:
        return normalized_tokens[0]

    first = normalized_tokens[0]
    second = normalized_tokens[1]

    if first[0].isdigit() and second and second[0].isalpha():
        # Numeric prefix + letter(s) + numeric suffix: keep the hyphen between
        # the letter block and the trailing digits. Examples:
        # "11" "В" "11" -> "11В-11"
        # "11" "В"      -> "11В"
        tail = "".join(normalized_tokens[2:])
        if tail and tail[0].isdigit():
            return f"{first}{second}-{tail}"
        return f"{first}{second}{tail}"

    # Default: compact join.
    return "".join(normalized_tokens)
