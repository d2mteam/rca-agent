"""Model provider settings, Claude style: API key or auth token, base URL, model.

Stored in ~/.config/rca-agent/settings.json, readable by the owner only. The key never leaves the
server: the UI gets it masked.
"""

import json
import os
from pathlib import Path

PATH = Path(os.environ.get("RCA_UI_SETTINGS", Path.home() / ".config" / "rca-agent" / "settings.json"))

DEFAULTS = {
    "provider": "anthropic",       # anthropic | custom (an Anthropic-compatible third party)
    "base_url": "",                # empty: https://api.anthropic.com
    "auth": "api_key",             # api_key -> x-api-key / ANTHROPIC_API_KEY; auth_token -> Bearer / ANTHROPIC_AUTH_TOKEN
    "api_key": "",
    "model": "claude-opus-5-5",
    "effort": "high",              # official API only
    "thinking": True,              # adaptive thinking with summaries; off by default for custom providers
    "claude_code_model": "sonnet",
    "claude_code_use_provider": False,  # pass base URL, key and model to the claude CLI as ANTHROPIC_* variables
    "use_profile": True,
    "max_iterations": 80,
}


def load() -> dict:
    data = json.loads(PATH.read_text()) if PATH.exists() else {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save(changes: dict) -> dict:
    current = load()
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        if key == "api_key" and (value is None or value == masked(current["api_key"])):
            continue  # the UI sends the mask back when the key was not changed
        current[key] = value
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(PATH)
    return current


def masked(key: str) -> str:
    return "" if not key else ("•" * 8 + key[-4:] if len(key) > 8 else "•" * len(key))


def public(settings: dict) -> dict:
    return {**settings, "api_key": masked(settings["api_key"]), "has_key": bool(settings["api_key"])}


def official(settings: dict) -> bool:
    url = settings["base_url"].strip()
    return settings["provider"] == "anthropic" and (not url or "api.anthropic.com" in url)


def claude_env(settings: dict) -> dict:
    """ANTHROPIC_* variables for the claude CLI, when the user chose to route it through these settings."""
    if not settings["claude_code_use_provider"]:
        return {}
    env = {}
    if settings["base_url"].strip():
        env["ANTHROPIC_BASE_URL"] = settings["base_url"].strip()
    if settings["api_key"]:
        env["ANTHROPIC_AUTH_TOKEN" if settings["auth"] == "auth_token" else "ANTHROPIC_API_KEY"] = settings["api_key"]
    if settings["model"].strip():
        env["ANTHROPIC_MODEL"] = settings["model"].strip()
    return env
