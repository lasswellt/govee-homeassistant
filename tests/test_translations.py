"""Tests for translation file consistency."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


# Languages shipped alongside the integration. Add a new file under
# ``custom_components/govee/translations/<code>.json`` and append its code
# here to make every structural and placeholder test cover it.
LANGUAGES: tuple[str, ...] = ("en", "es", "ca")

# Home Assistant placeholders are written ``{snake_case_name}``. The pattern
# keeps the test strict (no whitespace inside braces) so accidental typos like
# ``{ device_name }`` are flagged instead of silently breaking formatting at
# runtime.
PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _base_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "custom_components" / "govee"


def _load_json(path: Path) -> object:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _get_keys(obj: dict | list | str, prefix: str = "") -> set[str]:
    """Recursively extract all key paths from a nested dict."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            keys.add(full_key)
            keys.update(_get_keys(v, full_key))
    return keys


def _walk_strings(obj: object) -> list[str]:
    """Yield every leaf string value in a nested translation tree."""
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_strings(value)
    elif isinstance(obj, str):
        yield obj


def _leaf_strings(obj: object, prefix: str = "") -> dict[str, str]:
    """Map every leaf string to its dotted key path."""
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, str):
                out[path] = value
            else:
                out.update(_leaf_strings(value, path))
    return out


def _placeholders(text: str) -> set[str]:
    """Return the set of placeholder names present in a translated string."""
    return set(PLACEHOLDER_RE.findall(text))


# ---------------------------------------------------------------------------
# Source file (strings.json) — unchanged contract from the original tests.
# ---------------------------------------------------------------------------


def test_strings_json_is_valid() -> None:
    """Verify strings.json is valid JSON with the required top-level keys."""
    path = _base_dir() / "strings.json"
    data = _load_json(path)
    assert isinstance(data, dict)
    assert "config" in data
    assert "entity" in data


# ---------------------------------------------------------------------------
# Per-language structural checks (parametrized over LANGUAGES).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def strings_keys() -> set[str]:
    return _get_keys(_load_json(_base_dir() / "strings.json"))  # type: ignore[arg-type]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_file_is_valid_json(lang: str) -> None:
    """Each translations/<lang>.json must be valid JSON with required keys."""
    path = _base_dir() / "translations" / f"{lang}.json"
    assert path.exists(), f"Missing translation file: {path}"

    data = _load_json(path)
    assert isinstance(data, dict), f"{path} must be a JSON object"
    assert "config" in data, f"{path} missing 'config' section"
    assert "entity" in data, f"{path} missing 'entity' section"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_has_no_extra_keys(lang: str, strings_keys: set[str]) -> None:
    """No translation may carry a key that strings.json doesn't have.

    An extra key is always a bug — a typo, or a rename that updated the source
    and not the translation — and dead weight either way, since Home Assistant
    will never look it up. Enforced for every language.
    """
    path = _base_dir() / "translations" / f"{lang}.json"
    assert path.exists(), f"Missing translation file: {path}"

    extra = _get_keys(_load_json(path)) - strings_keys
    assert (
        not extra
    ), f"Keys in translations/{lang}.json not in strings.json:\n  {sorted(extra)}"


def test_en_translation_has_every_key(strings_keys: set[str]) -> None:
    """en.json must mirror strings.json exactly — drift there is a real bug.

    Deliberately not applied to the other languages. Home Assistant falls back
    to English for a missing key, so an untranslated string degrades quietly
    rather than breaking. Enforcing full parity everywhere would block every PR
    that adds an English string until someone can also write it in each
    translated language, stalling the source language on translator
    availability. Missing keys are translation work, not a test failure.
    """
    translation_keys = _get_keys(_load_json(_base_dir() / "translations" / "en.json"))

    missing = strings_keys - translation_keys
    assert (
        not missing
    ), f"Keys in strings.json missing from translations/en.json:\n  {sorted(missing)}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_values_are_non_empty(lang: str) -> None:
    """No translated string may be empty or whitespace-only.

    Empty strings are usually a copy-paste mistake where someone added a key
    but forgot to translate it. Home Assistant would fall back to the English
    string silently, so the bug is invisible until a user notices the locale
    switch didn't work.
    """
    path = _base_dir() / "translations" / f"{lang}.json"
    assert path.exists(), f"Missing translation file: {path}"

    empty_keys: list[str] = _find_empty_leaf_paths(_load_json(path))
    assert not empty_keys, (
        f"Empty/whitespace-only translated strings in translations/{lang}.json: "
        f"{empty_keys}"
    )


@pytest.mark.parametrize("lang", LANGUAGES)
def test_translation_preserves_placeholders(
    lang: str,
) -> None:
    """A translated string must keep its source string's ``{placeholder}`` set.

    Matched by key path rather than by leaf position. Positional matching only
    holds while both files enumerate their leaves in exactly the same order —
    insert a key in the middle of strings.json and every later index shifts,
    silently pairing unrelated strings against each other. Key paths are
    stable, and they also let a translation omit a key without invalidating
    the comparison of the ones it does have.

    HA substitutes these tokens at runtime: a dropped or renamed placeholder
    renders as a literal ``{api_url}`` to the user, and an extra one raises
    ``KeyError`` in the formatter. A translation may add placeholders only
    where the source string had none.
    """
    source = _leaf_strings(_load_json(_base_dir() / "strings.json"))
    translated = _leaf_strings(_load_json(_base_dir() / "translations" / f"{lang}.json"))

    mismatches: list[str] = []
    for key, source_text in source.items():
        if key not in translated:
            # Missing keys fall back to English in HA — see
            # test_en_translation_has_every_key for why that's allowed here.
            continue
        source_placeholders = _placeholders(source_text)
        if not source_placeholders:
            continue
        translated_placeholders = _placeholders(translated[key])
        if translated_placeholders != source_placeholders:
            mismatches.append(
                f"  {key}: source={source_placeholders} "
                f"translated={translated_placeholders}\n"
                f"    source:     {source_text!r}\n"
                f"    translated: {translated[key]!r}"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_empty_leaf_paths(obj: object, prefix: str = "") -> list[str]:
    """Return dotted paths leading to empty/whitespace-only leaf strings."""
    paths: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            paths.extend(_find_empty_leaf_paths(v, full_key))
    elif isinstance(obj, list):
        for index, v in enumerate(obj):
            full_key = f"{prefix}[{index}]"
            paths.extend(_find_empty_leaf_paths(v, full_key))
    elif isinstance(obj, str):
        if not obj.strip():
            paths.append(prefix or "<root>")
    return paths
