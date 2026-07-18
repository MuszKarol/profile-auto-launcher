"""Persistent run history — powers `palaunch last` and HUD recency ordering."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from launcher.config import config_dir


def state_path():
    return config_dir() / "state.json"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_run(profile_name: str) -> None:
    state = load_state()
    state["last_profile"] = profile_name
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    counts = state.setdefault("run_counts", {})
    counts[profile_name] = int(counts.get(profile_name, 0)) + 1
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # history is best-effort; never fail a run over it
