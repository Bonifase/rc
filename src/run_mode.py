"""One definition of the run mode, so no module silently picks a different one."""
from __future__ import annotations

import os


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def run_mode() -> str:
    """'live' (real paid calls), 'simulation' (canned, explicit opt-in) or 'off'."""
    live, simulation = _flag("REFCHECK_LIVE_JUDGE"), _flag("REFCHECK_ALLOW_OFFLINE_SIMULATION")
    if live and simulation:
        raise RuntimeError(
            "REFCHECK_LIVE_JUDGE and REFCHECK_ALLOW_OFFLINE_SIMULATION are both enabled. "
            "Enable only one: live calls the real models, simulation produces canned results."
        )
    return "live" if live else "simulation" if simulation else "off"
