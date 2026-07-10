from __future__ import annotations

import os
import asyncio
import time
import random
import re
from typing import Any, Literal

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.runnables import Runnable
from pydantic import BaseModel


def _provider_for_model(model: str) -> str:
    m_lower = (model or "").lower()
    if "gemini" in m_lower or "gemma" in m_lower:
        return "google"
    if "gpt-" in m_lower or m_lower.startswith("o") or "openai" in m_lower:
        return "openai"
    return preferred_provider()


def require_llm_agents() -> bool:
    """Check if LLM agents are strictly required. If false, agents use deterministic fallbacks."""
    # Tests should be deterministic by default (no external LLM dependency) unless explicitly enabled.
    default = "0" if os.getenv("PYTEST_CURRENT_TEST") else "1"
    return os.getenv("A4T_REQUIRE_LLM_AGENTS", default).strip().lower() not in {"0", "false", "no"}


def require_llm_planner() -> bool:
    """Whether the planning agent must succeed with an LLM.

    Default: disabled. Planning can fall back deterministically even when
    `A4T_REQUIRE_LLM_AGENTS=1` (which is primarily intended for the report compiler).
    """
    return os.getenv("A4T_REQUIRE_LLM_PLANNER", "0").strip().lower() not in {"0", "false", "no"}


def google_api_key() -> str | None:
    # Support either env var name; LangChain expects GOOGLE_API_KEY by default.
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


def openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or None


def llm_configured() -> bool:
    """True if any supported LLM provider is configured via env vars (keys present)."""
    return bool(openai_api_key() or google_api_key())


def llm_calls_enabled() -> bool:
    """Whether agents should attempt live LLM calls.

    Default behavior:
    - Normal runs: enabled
    - Pytest: disabled (deterministic) unless explicitly enabled.
    """
    raw = os.getenv("A4T_LLM_CALLS_ENABLED")
    if raw is None:
        return not bool(os.getenv("PYTEST_CURRENT_TEST"))
    return raw.strip().lower() not in {"0", "false", "no"}


def preferred_provider() -> str:
    """Resolve the active LLM provider.

    `A4T_LLM_PROVIDER`:
      - auto (default)
      - google|gemini
      - openai
    """
    override = os.getenv("A4T_LLM_PROVIDER", "").strip().lower()

    # If provider_select.py has already run and narrowed the choice to a specific working provider,
    # it will have set A4T_LLM_PROVIDER to that specific value ('openai' or 'google').
    if override in {"google", "gemini"} and google_api_key():
        return "google"
    if override == "openai" and openai_api_key():
        return "openai"

    # Default logic (Google priority for this environment)
    if google_api_key():
        return "google"
    if openai_api_key():
        return "openai"
    return "google"


def forced_provider() -> str | None:
    """Return an explicitly forced provider override, if set.

    Unlike preferred_provider(), this reflects user intent even if keys are missing.
    """
    override = os.getenv("A4T_LLM_PROVIDER", "").strip().lower()
    if override in {"google", "gemini"}:
        return "google"
    if override == "openai":
        return "openai"
    return None


def default_reasoning_model() -> str:
    if preferred_provider() == "google":
        # Check both reasoning and standard model envs
        return os.getenv("A4T_GOOGLE_REASONING_MODEL") or os.getenv("A4T_GOOGLE_FAST_MODEL") or "gemini-2.0-flash"
    return os.getenv("A4T_OPENAI_REASONING_MODEL", "gpt-4o")


def default_fast_model() -> str:
    if preferred_provider() == "google":
        return os.getenv("A4T_GOOGLE_FAST_MODEL", "gemini-2.0-flash")
    return os.getenv("A4T_OPENAI_FAST_MODEL", "gpt-4o-mini")


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [token.strip() for token in raw.split(",") if token.strip()]


def fallback_models(provider: str, *, role: str) -> list[str]:
    """Return fallback model candidates for a provider/role."""
    if provider == "google":
        models = _csv_env("A4T_GOOGLE_FALLBACK_MODELS")
        if models:
            return models
        # Try 2.0/2.5 models which are confirmed active on the user's key.
        if role == "reasoning":
            return ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-flash-latest"]
        return ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]
    models = _csv_env("A4T_OPENAI_FALLBACK_MODELS")
    if models:
        return models
    if role == "reasoning":
        return ["gpt-4o", "gpt-4-turbo", "o1-preview", "gpt-4"]
    return ["gpt-4o-mini", "gpt-3.5-turbo"]


def ensure_llm_available(agent_name: str) -> None:
    """Ensure at least one LLM provider is configured."""
    if llm_configured():
        return
    if require_llm_agents():
        raise RuntimeError(
            f"{agent_name} requires an API key because A4T_REQUIRE_LLM_AGENTS=1. "
            "Set OPENAI_API_KEY or GOOGLE_API_KEY (or GEMINI_API_KEY)."
        )


def get_llm(model: str, temperature: float = 0.0, **kwargs: Any) -> ChatOpenAI | ChatGoogleGenerativeAI:
    """
    Factory function to get a LangChain LLM instance.
    Correctly routes to OpenAI or Google based on model name substrings.
    """
    provider = preferred_provider()
    m_lower = model.lower()

    # Force Google if model looks like Gemini/Gemma
    if "gemini" in m_lower or "gemma" in m_lower:
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            google_api_key=google_api_key(),
            **kwargs,
        )
    
    # Force Gemini for ANY model if Google is configured and OpenAI is not.
    if google_api_key() and not openai_api_key():
        # Map gpt- or legacy models to a safe Gemini default
        target_model = os.getenv("A4T_GOOGLE_FAST_MODEL", "gemini-2.5-flash")
        return ChatGoogleGenerativeAI(
            model=target_model,
            temperature=temperature,
            google_api_key=google_api_key(),
            **kwargs,
        )
    
    # Force OpenAI ONLY if model looks like GPT AND we have a key.
    if "gpt-" in m_lower and openai_api_key():
        return ChatOpenAI(
            model=model, 
            temperature=temperature, 
            api_key=openai_api_key(),
            **kwargs
        )
    
    # Default fallback (strictly Google prioritized)
    if google_api_key():
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=temperature,
            google_api_key=google_api_key(),
            **kwargs,
        )
    raise RuntimeError(f"No functional LLM provider (Google/Gemini) configured. Attempted model: {model}")


def structured_runnable(
    *,
    schema: type,
    model: str,
    temperature: float = 0.0,
    method: Literal["function_calling", "json_mode", "json_schema"] = "function_calling",
) -> Runnable:
    llm = get_llm(model=model, temperature=temperature)
    # Gemini (langchain-google-genai) currently warns/ignores `$defs` in pydantic schemas.
    # Inline `$ref` definitions so structured outputs work reliably across providers.
    schema_obj: Any = schema
    if isinstance(llm, ChatGoogleGenerativeAI) and hasattr(schema, "model_json_schema"):
        schema_obj = _inline_pydantic_schema(schema)
    return llm.with_structured_output(schema_obj, method=method)


def _inline_pydantic_schema(model: type) -> dict[str, Any]:
    """Inline $defs/$ref in pydantic-generated JSON schema.

    Some providers (notably Gemini) reject or ignore `$defs`, which breaks `$ref` resolution.
    """
    raw: dict[str, Any] = model.model_json_schema()  # type: ignore[attr-defined]
    defs: dict[str, Any] = dict(raw.get("$defs") or {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node

        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            key = ref.split("/")[-1]
            base = defs.get(key, {})
            merged = {}
            if isinstance(base, dict):
                merged.update(resolve(base))
            # Merge sibling keys over the referenced schema (minus $ref).
            for k, v in node.items():
                if k == "$ref":
                    continue
                merged[k] = resolve(v)
            return merged

        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "$defs":
                continue
            out[k] = resolve(v)
        return out

    return resolve(raw)


def structured_runnable_with_fallbacks(
    *,
    schema: type,
    primary_model: str,
    role: str,
    temperature: float = 0.0,
    method: Literal["function_calling", "json_mode", "json_schema"] = "function_calling",
) -> Runnable:
    """Build a structured runnable with provider/model fallbacks.

    Fallbacks are controlled via:
    - `A4T_LLM_FALLBACK_ENABLED` (default: 1)
    - `A4T_GOOGLE_FALLBACK_MODELS`
    - `A4T_OPENAI_FALLBACK_MODELS`
    """
    primary = structured_runnable(schema=schema, model=primary_model, temperature=temperature, method=method)
    enabled = os.getenv("A4T_LLM_FALLBACK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    if not enabled:
        return primary

    provider = preferred_provider()
    candidates: list[str] = []
    candidates.extend(fallback_models(provider, role=role))
    # Cross-provider fallback:
    # - enabled by default only when provider is not explicitly forced
    # - can be overridden with A4T_LLM_CROSS_PROVIDER_FALLBACK=1|0
    cross_default = "0" if forced_provider() else "1"
    cross_enabled = os.getenv("A4T_LLM_CROSS_PROVIDER_FALLBACK", cross_default).strip().lower() not in {"0", "false", "no"}
    if cross_enabled:
        if provider == "google" and openai_api_key():
            candidates.extend(fallback_models("openai", role=role))
            candidates.append(os.getenv("A4T_OPENAI_REASONING_MODEL", "gpt-4o") if role == "reasoning" else os.getenv("A4T_OPENAI_FAST_MODEL", "gpt-4o-mini"))
        if provider == "openai" and google_api_key():
            candidates.extend(fallback_models("google", role=role))
            candidates.append(default_reasoning_model() if role == "reasoning" else default_fast_model())

    fallbacks = [
        structured_runnable(schema=schema, model=m, temperature=temperature, method=method)
        for m in candidates
        if m and m != primary_model
    ]
    return primary.with_fallbacks(fallbacks)


def _timeout_s() -> float:
    try:
        return float(os.getenv("A4T_LLM_TIMEOUT_S", "45"))
    except ValueError:
        return 45.0


def _retry_attempts() -> int:
    try:
        return max(1, int(os.getenv("A4T_LLM_RETRY_ATTEMPTS", "3")))
    except ValueError:
        return 3


def _retry_delay_s(attempt_index: int) -> float:
    """Deterministic backoff: 1s, 2s, 4s... (capped)."""
    try:
        base = float(os.getenv("A4T_LLM_RETRY_BASE_DELAY_S", "1.0"))
    except ValueError:
        base = 1.0
    delay = base * (2**attempt_index)
    return min(delay, 8.0)


def _llm_concurrency(provider: str) -> int:
    """Global max in-flight LLM requests per provider."""
    default = "1" if provider == "google" else "2"
    raw = os.getenv("A4T_LLM_CONCURRENCY", default).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return int(default)

def _llm_gate_acquire_timeout_s(provider: str) -> float:
    """How long we're willing to wait for the global provider semaphore.

    This prevents UI runs from hanging indefinitely if another LLM call is stuck.
    """
    default = "8.0" if provider == "google" else "4.0"
    raw = os.getenv("A4T_LLM_GATE_ACQUIRE_TIMEOUT_S", default).strip()
    try:
        return max(0.1, float(raw))
    except ValueError:
        return float(default)


def _llm_min_interval_s(provider: str) -> float:
    """Minimum spacing between request starts for a provider."""
    default = "2.5" if provider == "google" else "0.0"
    raw = os.getenv("A4T_LLM_MIN_INTERVAL_S", default).strip()
    try:
        base = max(0.0, float(raw))
    except ValueError:
        base = float(default)

    rpm_raw = os.getenv("A4T_LLM_RPM", "").strip()
    if rpm_raw:
        try:
            rpm = float(rpm_raw)
            if rpm > 0:
                base = max(base, 60.0 / rpm)
        except ValueError:
            pass
    return base


def _jitter(mult: float = 0.25) -> float:
    """Small jitter to avoid synchronized retry bursts."""
    return 1.0 + random.uniform(-mult, mult)


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in ("429", "too many requests", "rate limit", "resource_exhausted", "quota"))

def _is_quota_exhausted_error(exc: Exception) -> bool:
    """True for provider responses that indicate the quota will not recover quickly.

    For these we should fail fast (no retries / long sleeps), and allow deterministic fallbacks.
    """
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "resource_exhausted",
            "quota exceeded",
            "quota_exceeded",
            "free_tier_requests",
            "insufficient_quota",
        )
    )


def _is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in str(exc).lower()


def _rate_limit_base_delay_s(provider: str) -> float:
    default = "6.0" if provider == "google" else "2.0"
    raw = os.getenv("A4T_LLM_429_BASE_DELAY_S", default).strip()
    try:
        return max(0.5, float(raw))
    except ValueError:
        return float(default)


def _rate_limit_max_delay_s() -> float:
    raw = os.getenv("A4T_LLM_429_MAX_DELAY_S", "60.0").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 60.0


_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?:\\s*['\"]?(\\d+)s['\"]?", re.IGNORECASE)


def _extract_retry_after_s(exc: Exception) -> float | None:
    """Best-effort extraction of provider-suggested retry delay from error text."""
    match = _RETRY_DELAY_RE.search(str(exc))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


_GATE_LOCKS: dict[str, asyncio.Lock] = {}
_GATE_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
_NEXT_ALLOWED_AT: dict[str, float] = {}


async def _llm_gate_enter(provider: str) -> None:
    sem = _GATE_SEMAPHORES.get(provider)
    if sem is None:
        sem = asyncio.Semaphore(_llm_concurrency(provider))
        _GATE_SEMAPHORES[provider] = sem
    await asyncio.wait_for(sem.acquire(), timeout=_llm_gate_acquire_timeout_s(provider))

    min_interval = _llm_min_interval_s(provider)
    if min_interval <= 0:
        return

    lock = _GATE_LOCKS.get(provider)
    if lock is None:
        lock = asyncio.Lock()
        _GATE_LOCKS[provider] = lock

    async with lock:
        now = time.monotonic()
        allowed_at = _NEXT_ALLOWED_AT.get(provider, 0.0)
        if now < allowed_at:
            await asyncio.sleep(allowed_at - now)
            now = time.monotonic()
        _NEXT_ALLOWED_AT[provider] = now + min_interval


def _llm_gate_exit(provider: str) -> None:
    sem = _GATE_SEMAPHORES.get(provider)
    if sem is not None:
        sem.release()


async def _ainvoke_guarded(runnable: Runnable, prompt: list[tuple[str, str]] | str, *, provider: str) -> Any:
    await _llm_gate_enter(provider)
    try:
        # langchain-google-genai has had cases where `ainvoke()` blocks the event loop (no await points),
        # which defeats asyncio timeouts and can hang UI runs. Run Google calls in a thread.
        if provider == "google":
            return await asyncio.wait_for(asyncio.to_thread(runnable.invoke, prompt), timeout=_timeout_s())
        return await asyncio.wait_for(runnable.ainvoke(prompt), timeout=_timeout_s())
    finally:
        _llm_gate_exit(provider)


async def ainvoke_with_fallbacks(
    *,
    prompt: list[tuple[str, str]] | str,
    primary_model: str,
    role: str,
    temperature: float = 0.0,
) -> Any:
    """Invoke a raw text call with explicit per-candidate timeouts and provider fallbacks."""
    provider = preferred_provider()
    enabled = os.getenv("A4T_LLM_FALLBACK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    candidates: list[str] = [primary_model]
    if enabled:
        candidates.extend(fallback_models(provider, role=role))
        cross_default = "0" if forced_provider() else "1"
        cross_enabled = os.getenv("A4T_LLM_CROSS_PROVIDER_FALLBACK", cross_default).strip().lower() not in {"0", "false", "no"}
        if cross_enabled:
            if provider == "google" and openai_api_key():
                candidates.extend(fallback_models("openai", role=role))
                candidates.append(
                    os.getenv("A4T_OPENAI_REASONING_MODEL", "gpt-4o")
                    if role == "reasoning"
                    else os.getenv("A4T_OPENAI_FAST_MODEL", "gpt-4o-mini")
                )
            if provider == "openai" and google_api_key():
                candidates.extend(fallback_models("google", role=role))
                candidates.append(default_reasoning_model() if role == "reasoning" else default_fast_model())

    seen: set[str] = set()
    ordered_candidates: list[str] = []
    for model in candidates:
        if not model or model in seen:
            continue
        seen.add(model)
        ordered_candidates.append(model)

    errors: list[str] = []
    for model in ordered_candidates:
        model_provider = _provider_for_model(model)
        llm = get_llm(model=model, temperature=temperature)
        for attempt in range(_retry_attempts()):
            try:
                return await _ainvoke_guarded(llm, prompt, provider=model_provider)
            except Exception as exc:  # noqa: BLE001
                if _is_timeout_error(exc):
                    errors.append(f"{model}: TimeoutError(attempt={attempt+1}/{_retry_attempts()}): {exc}")
                    if attempt + 1 >= _retry_attempts():
                        break
                    await asyncio.sleep(_retry_delay_s(attempt) * _jitter(0.2))
                    continue
                if _is_rate_limit_error(exc):
                    base = _rate_limit_base_delay_s(model_provider)
                    retry_after = _extract_retry_after_s(exc)
                    delay = retry_after if retry_after is not None else base * (2**attempt)
                    delay = min(delay, _rate_limit_max_delay_s())
                    errors.append(f"{model}: RateLimit(attempt={attempt+1}/{_retry_attempts()} delay_s={delay:.1f}): {exc}")
                    if _is_quota_exhausted_error(exc):
                        break
                    if attempt + 1 >= _retry_attempts():
                        break
                    await asyncio.sleep(delay * _jitter(0.35))
                    continue
                # If it's a 401 or other fatal error, record it and try the NEXT model/provider.
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                break

    raise RuntimeError(
        "All LLM candidates failed. "
        + f"provider={provider} role={role} timeout_s={_timeout_s()} "
        + " | ".join(errors[:5])
    )


async def structured_ainvoke_with_fallbacks(
    *,
    schema: type,
    prompt: str,
    primary_model: str,
    role: str,
    temperature: float = 0.0,
    method: Literal["function_calling", "json_mode", "json_schema"] = "function_calling",
) -> Any:
    """Invoke a structured output call with explicit per-candidate timeouts.

    This avoids the failure mode where a provider blocks for a long time (quota/rate-limit retry delays),
    preventing fallbacks from being attempted.
    """
    provider = preferred_provider()
    enabled = os.getenv("A4T_LLM_FALLBACK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    candidates: list[str] = [primary_model]
    if enabled:
        candidates.extend(fallback_models(provider, role=role))
        cross_default = "0" if forced_provider() else "1"
        cross_enabled = os.getenv("A4T_LLM_CROSS_PROVIDER_FALLBACK", cross_default).strip().lower() not in {"0", "false", "no"}
        if cross_enabled:
            if provider == "google" and openai_api_key():
                candidates.extend(fallback_models("openai", role=role))
                candidates.append(
                    os.getenv("A4T_OPENAI_REASONING_MODEL", "gpt-4o")
                    if role == "reasoning"
                    else os.getenv("A4T_OPENAI_FAST_MODEL", "gpt-4o-mini")
                )
            if provider == "openai" and google_api_key():
                candidates.extend(fallback_models("google", role=role))
                candidates.append(default_reasoning_model() if role == "reasoning" else default_fast_model())

    seen: set[str] = set()
    ordered_candidates: list[str] = []
    for model in candidates:
        if not model or model in seen:
            continue
        seen.add(model)
        ordered_candidates.append(model)

    errors: list[str] = []
    for model in ordered_candidates:
        model_provider = _provider_for_model(model)
        runnable = structured_runnable(schema=schema, model=model, temperature=temperature, method=method)
        for attempt in range(_retry_attempts()):
            try:
                result = await _ainvoke_guarded(runnable, prompt, provider=model_provider)
                # Some providers return plain dicts even when a Pydantic schema is provided.
                # Normalize to the requested schema type for consistent downstream access.
                if isinstance(result, dict) and isinstance(schema, type) and issubclass(schema, BaseModel):
                    return schema.model_validate(result)
                return result
            except Exception as exc:  # noqa: BLE001
                if _is_timeout_error(exc):
                    errors.append(f"{model}: TimeoutError(attempt={attempt+1}/{_retry_attempts()}): {exc}")
                    if attempt + 1 >= _retry_attempts():
                        break
                    await asyncio.sleep(_retry_delay_s(attempt) * _jitter(0.2))
                    continue
                if _is_rate_limit_error(exc):
                    base = _rate_limit_base_delay_s(model_provider)
                    retry_after = _extract_retry_after_s(exc)
                    delay = retry_after if retry_after is not None else base * (2**attempt)
                    delay = min(delay, _rate_limit_max_delay_s())
                    errors.append(f"{model}: RateLimit(attempt={attempt+1}/{_retry_attempts()} delay_s={delay:.1f}): {exc}")
                    # If quota is exhausted, retries/sleeps won't help. Fail fast so callers can fall back.
                    if _is_quota_exhausted_error(exc):
                        break
                    if attempt + 1 >= _retry_attempts():
                        break
                    await asyncio.sleep(delay * _jitter(0.35))
                    continue
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                break

    raise RuntimeError(
        "All LLM candidates failed. "
        + f"provider={provider} role={role} timeout_s={_timeout_s()} "
        + " | ".join(errors[:5])
    )
