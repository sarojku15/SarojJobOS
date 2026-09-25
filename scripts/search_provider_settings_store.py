#!/usr/bin/env python3

"""
Non-secret local settings for the search-provider pool (Phase 14.2,
Part 3/4): provider priority order and per-provider enable/disable
overrides, set from the GUI's Settings -> Search Providers page.

Deliberately holds NO API keys (those live in .env only -- see
env_config.py) and is NOT the production database -- a small JSON file
at data/applications/search_provider_settings.json, git-ignored by the
same existing "data/applications/" rule as everything else under that
directory.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from search_provider import DEFAULT_PROVIDER_ORDER

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_STORE_PATH = ROOT / "data" / "applications" / "search_provider_settings.json"


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _env_order_override():
    """SEARCH_PROVIDER_ORDER=you,tavily,exa,brave,serper (Part 2) --
    only a subset/reordering of known provider names is honored;
    unknown tokens are dropped rather than raising, since this is a
    convenience override, not a strict schema."""
    raw = os.environ.get("SEARCH_PROVIDER_ORDER", "").strip()
    if not raw:
        return None
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    known = set(DEFAULT_PROVIDER_ORDER)
    ordered = [n for n in names if n in known]
    # Append any known provider the override omitted, preserving the
    # default's relative order -- never silently drops a configured
    # provider just because SEARCH_PROVIDER_ORDER forgot to list it.
    for n in DEFAULT_PROVIDER_ORDER:
        if n not in ordered:
            ordered.append(n)
    return ordered


def _env_enabled_override():
    """SEARCH_PROVIDER_ENABLED=you,serper (Part 2) -- an explicit
    allow-list. None (the env var unset) means "no restriction beyond
    having a configured key," matching Part 4's default behavior."""
    raw = os.environ.get("SEARCH_PROVIDER_ENABLED", "").strip()
    if not raw:
        return None
    return {n.strip().lower() for n in raw.split(",") if n.strip()}


def load_settings(path=SETTINGS_STORE_PATH):
    """{"order": [...], "disabled": [...]}. `order` defaults to
    SEARCH_PROVIDER_ORDER's env override or DEFAULT_PROVIDER_ORDER;
    `disabled` is a GUI-set override (a provider the user explicitly
    turned off via [Disable], independent of whether it has a key)."""
    data = {"order": None, "disabled": []}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                stored = json.load(f)
            data["order"] = stored.get("order")
            data["disabled"] = stored.get("disabled", [])
        except (json.JSONDecodeError, OSError):
            pass

    if data["order"] is None:
        data["order"] = _env_order_override() or list(DEFAULT_PROVIDER_ORDER)

    env_enabled = _env_enabled_override()
    if env_enabled is not None:
        # SEARCH_PROVIDER_ENABLED is an explicit allow-list -- anything
        # not named there is treated as disabled, on top of (never
        # replacing) the GUI's own disabled list.
        data["disabled"] = sorted(set(data["disabled"]) | (set(DEFAULT_PROVIDER_ORDER) - env_enabled))

    return data


def save_settings(order=None, disabled=None, path=SETTINGS_STORE_PATH):
    current = load_settings(path)
    if order is not None:
        current["order"] = [p for p in order if p in DEFAULT_PROVIDER_ORDER]
        for p in DEFAULT_PROVIDER_ORDER:
            if p not in current["order"]:
                current["order"].append(p)
    if disabled is not None:
        current["disabled"] = sorted(set(disabled) & set(DEFAULT_PROVIDER_ORDER))
    _atomic_write(path, {"order": current["order"], "disabled": current["disabled"]})
    return current


def set_provider_enabled(provider_name, enabled, path=SETTINGS_STORE_PATH):
    current = load_settings(path)
    disabled = set(current["disabled"])
    if enabled:
        disabled.discard(provider_name)
    else:
        disabled.add(provider_name)
    return save_settings(order=current["order"], disabled=sorted(disabled), path=path)


def is_provider_enabled(provider_name, path=SETTINGS_STORE_PATH):
    settings = load_settings(path)
    return provider_name not in set(settings["disabled"])
