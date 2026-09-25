#!/usr/bin/env python3

"""
Minimal .env reader/writer -- no external dependency (no python-dotenv
requirement added to this project). This is the ONE place a search-
provider API key entered through the GUI (Phase 14.2, Part 17) is ever
written to disk: .env is this project's own, pre-existing, git-ignored
secrets file (see .gitignore's "# Secrets\n.env"), never a new project
file this codebase didn't already treat as sensitive.

This module never logs a key's value, never returns a key's full value
to a caller that only needs to know IF it's set (see
mask_secret()/get_masked() below), and never writes anywhere except
.env itself.

A key written here takes effect for THIS RUNNING PROCESS only via
os.environ (updated immediately, so an in-process check like
search_provider.is_provider_configured() sees it right away) -- but
any adapter status that was already fixed at an earlier import time
(see search_provider_adapter.py's module docstring on that convention)
still requires a process restart to pick it up, exactly like every
other adapter in this codebase. The GUI says so explicitly.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

_KEY_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env_file(path=ENV_PATH):
    """{"KEY": "value"} parsed from .env, ignoring blank lines and
    lines starting with '#'. Never raises if the file is missing."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _KEY_LINE_RE.match(stripped)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def load_env_file_into_environ(path=ENV_PATH):
    """
    Load every KEY=value in .env into os.environ, using standard
    dotenv setdefault semantics -- never overrides a variable the real
    shell/process environment already set. Idempotent and safe to call
    more than once (e.g. from multiple entrypoints, or after another
    caller already loaded it) -- setdefault makes a second call a
    no-op for every key already present.

    Root-cause fix (see search_provider.py's own note on this): every
    entrypoint that imports search_provider.py -- api/main.py,
    scripts/submit_search.py, scripts/run_search_worker.py, or any
    future CLI script -- must see .env's *_API_KEY values BEFORE
    search_provider_adapter.py's module-level `status` class attribute
    is computed (it is fixed once at first import, per that module's
    own documented convention). A caller that only sets os.environ
    itself (e.g. a real shell export) is never overridden by this.
    """
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def write_env_key(key, value, path=ENV_PATH):
    """Set KEY=value in .env, updating the existing line if present or
    appending a new one otherwise. Also updates os.environ immediately
    so this process sees the new value right away. Never echoes
    `value` back to any caller -- the caller already has it (they just
    supplied it)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found = False
    new_lines = []
    for line in lines:
        match = _KEY_LINE_RE.match(line.strip())
        if match and match.group(1) == key:
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def remove_env_key(key, path=ENV_PATH):
    """Delete KEY's line from .env (if present) and from this
    process's os.environ."""
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        new_lines = [line for line in lines if not (_KEY_LINE_RE.match(line.strip()) and _KEY_LINE_RE.match(line.strip()).group(1) == key)]
        path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
    os.environ.pop(key, None)


def mask_secret(value):
    """"sk-abc123...XYZ9" -> "••••••••••••XYZ9" (last 4 chars visible,
    everything else replaced) -- never returns enough of the real
    value to reconstruct it. Empty/short values are fully masked."""
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def get_masked(env_key):
    """The masked value of an environment variable currently set in
    THIS process (checked via os.environ, which write_env_key() above
    keeps in sync with .env for keys set through this module), or None
    if it is not set. Never returns the real value."""
    value = os.environ.get(env_key)
    if not value:
        return None
    return mask_secret(value)
