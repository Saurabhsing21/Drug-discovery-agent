from __future__ import annotations

import os
from typing import Literal

ProfileName = Literal["dev", "test", "staging", "prod"]

CONFIG_PROFILES: dict[str, dict[str, object]] = {
    "dev": {
        "summary_model": "gpt-5",
        "retry_max_attempts": 3,
        "pharos_start_timeout_s": 30,
        "observability_level": "debug",
        "offline_mode": False,
    },
    "test": {
        "summary_model": "deterministic_fallback",
        "retry_max_attempts": 3,
        "pharos_start_timeout_s": 5,
        "observability_level": "test",
        "offline_mode": True,
    },
    "staging": {
        "summary_model": "gpt-5-mini",
        "retry_max_attempts": 3,
        "pharos_start_timeout_s": 20,
        "observability_level": "info",
        "offline_mode": False,
    },
    "prod": {
        "summary_model": "gpt-5",
        "retry_max_attempts": 3,
        "pharos_start_timeout_s": 20,
        "observability_level": "info",
        "offline_mode": False,
    },
}


def get_config_profile(name: str | None = None) -> dict[str, object]:
    raw = name if name is not None else (os.getenv("A4T_ENV_PROFILE") or "dev")
    profile_name = raw.strip().lower()
    if profile_name not in CONFIG_PROFILES:
        raise ValueError(f"Unknown config profile: {profile_name}")
    return dict(CONFIG_PROFILES[profile_name])


def validate_config_profiles() -> None:
    required_fields = {
        "summary_model",
        "retry_max_attempts",
        "pharos_start_timeout_s",
        "observability_level",
        "offline_mode",
    }
    for name, profile in CONFIG_PROFILES.items():
        missing = required_fields.difference(profile)
        if missing:
            raise ValueError(f"Profile '{name}' missing fields: {sorted(missing)}")


def _nonempty(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _setdefault_if_nonempty(key: str, value: str | None) -> None:
    if _nonempty(value) is None:
        return
    os.environ.setdefault(key, str(value).strip())


def apply_system_defaults() -> None:
    """Apply system-wide runtime defaults for LLM selection and agent models.

    Rules:
    - Never hardcode secrets.
    - Only set defaults (do not override explicit env vars).
    - Prefer a single place to configure provider/models for the whole system.

    Supported non-secret knobs:
    - A4T_SYSTEM_LLM_PROVIDER=openai|google|auto
    - A4T_SYSTEM_REASONING_MODEL=<model>
    - A4T_SYSTEM_FAST_MODEL=<model>
    - A4T_ENV_PROFILE=dev|test|staging|prod (baseline via config_profiles.py)
    """
    try:
        profile = get_config_profile()
    except Exception:
        profile = {}

    system_provider_raw = _nonempty(os.getenv("A4T_SYSTEM_LLM_PROVIDER"))
    system_provider = system_provider_raw.lower() if system_provider_raw else None
    if system_provider in {"openai", "google", "auto"}:
        _setdefault_if_nonempty("A4T_LLM_PROVIDER", system_provider)

    # Allow a single pair of system-wide model names, then map them into provider-specific defaults.
    system_reasoning = _nonempty(os.getenv("A4T_SYSTEM_REASONING_MODEL"))
    system_fast = _nonempty(os.getenv("A4T_SYSTEM_FAST_MODEL"))

    # Baseline: config profile can define a summary_model (e.g., gpt-5). Keep it as a low-priority default.
    profile_summary_model = _nonempty(str(profile.get("summary_model")) if "summary_model" in profile else None)

    _setdefault_if_nonempty("A4T_SUMMARY_MODEL", system_fast or profile_summary_model)
    _setdefault_if_nonempty("A4T_PLANNER_MODEL", system_reasoning or profile_summary_model)
    _setdefault_if_nonempty("A4T_SUPERVISOR_MODEL", system_reasoning or profile_summary_model)

    # Provider-specific model defaults (used by llm_policy.default_*_model()).
    if system_reasoning:
        _setdefault_if_nonempty("A4T_OPENAI_REASONING_MODEL", system_reasoning)
        _setdefault_if_nonempty("A4T_GOOGLE_REASONING_MODEL", system_reasoning)
    if system_fast:
        _setdefault_if_nonempty("A4T_OPENAI_FAST_MODEL", system_fast)
        _setdefault_if_nonempty("A4T_GOOGLE_FAST_MODEL", system_fast)
