from __future__ import annotations

import os

# Fail-closed by default. There is currently no legitimate call site for
# PAYG in this pipeline: every stage is deterministic Python, SQLite, or
# local Playwright driving the already-installed system Chrome. This guard
# exists so any future paid-API integration (LLM billing, paid image/search
# APIs, any metered external service) fails closed instead of silently
# running. Claude Code's own subscription/session reasoning (WebSearch,
# WebFetch, semantic writing done by the assistant) is NOT gated by this --
# it is not externally metered API billing.
NO_PAYG_ENV_VAR = "SOCIAL_AUTOMATION_NO_PAYG"
ALLOW_PAYG_ENV_VAR = "SOCIAL_AUTOMATION_ALLOW_PAYG"


class PAYGBlockedError(RuntimeError):
    pass


def payg_guard_active() -> bool:
    """True (the safe default) unless PAYG has been explicitly allowed."""
    return os.environ.get(ALLOW_PAYG_ENV_VAR, "0") != "1"


def assert_no_payg(provider: str, reason: str = "") -> None:
    """Call this at the top of any code path that would require paid,
    externally-metered API billing (a paid LLM call, paid image generation,
    a paid search/data API, etc). Raises PAYGBlockedError unless PAYG has
    been explicitly allowed via SOCIAL_AUTOMATION_ALLOW_PAYG=1. Never enables
    a paid path silently -- the caller must catch this and report
    PAYG_BLOCKED rather than degrade to a paid fallback.
    """
    if payg_guard_active():
        detail = f" ({reason})" if reason else ""
        raise PAYGBlockedError(
            f"PAYG_BLOCKED: '{provider}' requires paid external billing{detail}; "
            f"{NO_PAYG_ENV_VAR}=1 is in effect. Set {ALLOW_PAYG_ENV_VAR}=1 to override explicitly."
        )
