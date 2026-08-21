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
def test_translation_keys_match_strings_json(
    lang: str, strings_keys: set[str]
) -> None:
    """Every leaf key in strings.json must exist in the translation, and vice-versa."""
    path = _base_dir() / "translations" / f"{lang}.json"
    assert path.exists(), f"Missing translation file: {path}"

    translation_keys = _get_keys(_load_json(path))

    missing = strings_keys - translation_keys
    extra = translation_keys - strings_keys

    errors: list[str] = []
    if missing:
        errors.append(
            f"Keys in strings.json missing from translations/{lang}.json:\n"
            f"  {sorted(missing)}"
        )
    if extra:
        errors.append(
            f"Keys in translations/{lang}.json not in strings.json:\n"
            f"  {sorted(extra)}"
        )
    assert not errors, "\n".join(errors)


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
    """Every ``{placeholder}`` in strings.json must also appear in the translation.

    The set is matched positionally by the leaf string's text, not by key path,
    because the same English string can legitimately appear at multiple leaf
    locations (e.g. ``"Battery"`` reused across two sensors) and any one of
    them being untranslated would still leave the other valid — so the check
    must apply to every occurrence.

    A translation may introduce *additional* placeholders only if the source
    string had none; if the source has placeholders, the translation must keep
    exactly that set (no renames, no drops, no extras). HA replaces
    ``{placeholder}`` tokens at runtime — a missing or renamed placeholder
    shows up as a literal ``{api_url}`` to the user, and an extra one crashes
    the formatter with ``KeyError``.
    """
    source_strings = list(_walk_strings(_load_json(_base_dir() / "strings.json")))
    translation_strings = list(_walk_strings(_load_json(_base_dir() / "translations" / f"{lang}.json")))

    assert len(translation_strings) == len(source_strings), (
        f"translations/{lang}.json has a different leaf-string count "
        f"({len(translation_strings)}) than strings.json ({len(source_strings)}); "
        "the structural key check should have caught this first."
    )

    mismatches: list[str] = []
    for index, (source, translated) in enumerate(zip(source_strings, translation_strings)):
        source_placeholders = _placeholders(source)
        if not source_placeholders:
            continue
        translated_placeholders = _placeholders(translated)
        if translated_placeholders != source_placeholders:
            mismatches.append(
                f"  leaf #{index}: source={source_placeholders} "
                f"translated={translated_placeholders}\n"
                f"    source:     {source!r}\n"
                f"    translated: {translated!r}"
            )

    assert not mismatches, (
        f"Placeholder set mismatch in translations/{lang}.json:\n"
        + "\n".join(mismatches)
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
