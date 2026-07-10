from __future__ import annotations

# Ensure system-wide defaults are applied once when the agents package loads.
# This sets only env defaults (explicit env vars always win).
try:
    from drugagent.core.config import apply_system_defaults

    apply_system_defaults()
except Exception:
    # Never block imports if config is missing/misconfigured.
    pass
