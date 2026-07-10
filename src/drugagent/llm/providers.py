from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from langchain_google_genai import ChatGoogleGenerativeAI


def _bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _probe_timeout_s() -> float:
    raw = os.getenv("A4T_PROVIDER_PROBE_TIMEOUT_S", "10").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 10.0


def _system_provider_pref() -> str:
    raw = os.getenv("A4T_SYSTEM_LLM_PROVIDER", "auto").strip().lower()
    return raw if raw in {"auto", "openai", "google"} else "auto"


def _openai_key_present() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _google_key_present() -> bool:
    return bool((os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")).strip())


async def _probe_openai(*, model: str) -> tuple[bool, str | None]:
    # OpenAI is disabled; always skip.
    if not _openai_key_present():
        return False, "OPENAI_API_KEY not set"
    try:
        from langchain_openai import ChatOpenAI
        from .policy import openai_api_key
        llm = ChatOpenAI(model=model, temperature=0, api_key=openai_api_key())
        await asyncio.wait_for(llm.ainvoke("ping"), timeout=_probe_timeout_s())
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


async def _probe_google(*, model: str) -> tuple[bool, str | None]:
    if not _google_key_present():
        return False, "GOOGLE_API_KEY/GEMINI_API_KEY not set"
    
    # If the default fails (e.g. 404), try these candidates in order.
    candidates = [model, "gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
    seen = set()
    last_err = "Unknown error"
    
    from .policy import google_api_key
    
    for m in candidates:
        if m in seen:
            continue
        seen.add(m)
        try:
            llm = ChatGoogleGenerativeAI(
                model=m, 
                temperature=0, 
                max_output_tokens=1, 
                google_api_key=google_api_key()
            )
            # Defensive: google client has had cases where `ainvoke()` blocks; use a thread.
            await asyncio.wait_for(asyncio.to_thread(llm.invoke, "ping"), timeout=_probe_timeout_s())
            # If we succeed with a different model, set it as the forced default for this run
            if m != model:
                os.environ["A4T_GOOGLE_FAST_MODEL"] = m
                os.environ["A4T_GOOGLE_REASONING_MODEL"] = m
            return True, None
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            # If it's a 404, we try the next candidate. 429/401 should probably fail the whole probe.
            if "404" not in last_err and "NOT_FOUND" not in last_err:
                break
                
    return False, last_err


@dataclass(frozen=True)
class ProviderSelection:
    provider: str  # openai|google|none
    locked: bool
    llm_calls_enabled: bool
    error: str | None = None
    selected_at_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "locked": self.locked,
            "llm_calls_enabled": self.llm_calls_enabled,
            "error": self.error,
            "selected_at_ms": self.selected_at_ms,
            "system_pref": _system_provider_pref(),
        }


_CACHED: ProviderSelection | None = None
_LOCK = asyncio.Lock()


def current_provider_selection() -> ProviderSelection | None:
    return _CACHED


def reset_provider_selection() -> None:
    """Reset the cached selection. Internal/Test use only."""
    global _CACHED  # noqa: PLW0603
    _CACHED = None
    if "A4T_LLM_PROVIDER" in os.environ:
        del os.environ["A4T_LLM_PROVIDER"]
    # Do NOT reset A4T_LLM_CALLS_ENABLED or CROSS_PROVIDER_FALLBACK here as they might be set by .env


async def select_provider_once() -> ProviderSelection:
    """Select and lock the system provider for this process.

    Policy (default):
    - If A4T_SYSTEM_LLM_PROVIDER=auto:
      - probe OpenAI first, then Google
    - If explicitly openai/google:
      - probe that provider, and (by default) do not fall back to the other provider
    - If no provider is reachable:
      - disable live LLM calls for this process (deterministic mode)
    """
    global _CACHED  # noqa: PLW0603
    if _CACHED is not None:
        return _CACHED

    async with _LOCK:
        if _CACHED is not None:
            return _CACHED

        system_pref = _system_provider_pref()
        
        # 0. SKIP PROBES if a specific provider is forced and keyed.
        # This prevents startup timeouts in environments where the probe (a dummy call)
        # might fail or be slow, but the actual inference works.
        skip_google = (system_pref == "google" and _google_key_present())
        skip_openai = (system_pref == "openai" and _openai_key_present())
        
        openai_model = os.getenv("A4T_OPENAI_FAST_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        google_model = os.getenv("A4T_GOOGLE_FAST_MODEL", "gemini-1.5-flash").strip() or "gemini-1.5-flash"

        # Start probes in parallel to minimize latency
        openai_task = (asyncio.sleep(0, (True, None)) if skip_openai else _probe_openai(model=openai_model))
        google_task = (asyncio.sleep(0, (True, None)) if skip_google else _probe_google(model=google_model))
        
        results: tuple[
            tuple[bool, str | None] | BaseException,
            tuple[bool, str | None] | BaseException,
        ] = await asyncio.gather(
            openai_task,
            google_task,
            return_exceptions=True,
        )

        openai_result = results[0]
        openai_ok: bool
        openai_err: str | None
        if isinstance(openai_result, BaseException):
            openai_ok, openai_err = False, str(openai_result)
        else:
            openai_ok, openai_err = openai_result

        google_result = results[1]
        google_ok: bool
        google_err: str | None
        if isinstance(google_result, BaseException):
            google_ok, google_err = False, str(google_result)
        else:
            google_ok, google_err = google_result

        selected: str | None = None
        
        # Selection logic:
        # 1. HONOR SYSTEM PREFERENCE FIRST (if locked and healthy)
        if system_pref == "openai" and openai_ok:
            selected = "openai"
        elif system_pref == "google" and google_ok:
            selected = "google"
        # 2. AUTO-SELECT (Priority: OpenAI > Google)
        elif openai_ok:
            selected = "openai"
        elif google_ok:
            selected = "google"

        selected_at_ms = int(time.time() * 1000)
        if selected is None:
            errors = []
            if openai_err:
                errors.append(f"openai: {openai_err}")
            if google_err:
                errors.append(f"google: {google_err}")
            
            os.environ["A4T_LLM_CALLS_ENABLED"] = "0"
            os.environ["A4T_LLM_CROSS_PROVIDER_FALLBACK"] = "0"
            
            # Log failure to a local file for debugging
            try:
                with open("llm_probe_error.log", "w") as f:
                    f.write(f"Selection Result: FAIL\nErrors: {'; '.join(errors) if errors else 'Unknown'}\n")
            except Exception:
                pass

            # Default to google when probes fail, not openai (we have no OpenAI key).
            os.environ.setdefault("A4T_LLM_PROVIDER", system_pref if system_pref not in {"auto", "openai"} else "google")
            _CACHED = ProviderSelection(
                provider="none",
                locked=True,
                llm_calls_enabled=False,
                error="; ".join(errors) if errors else "Probes failed for both providers",
                selected_at_ms=selected_at_ms,
            )
            return _CACHED

        os.environ["A4T_LLM_PROVIDER"] = selected
        os.environ.setdefault("A4T_LLM_CALLS_ENABLED", "1")
        # Ensure fallback is enabled globally to handle transient errors in the locked provider.
        os.environ["A4T_LLM_CROSS_PROVIDER_FALLBACK"] = "1"
        
        _CACHED = ProviderSelection(
            provider=selected,
            locked=True,
            llm_calls_enabled=True,
            error=None,
            selected_at_ms=selected_at_ms,
        )
        return _CACHED
