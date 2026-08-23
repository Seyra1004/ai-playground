from __future__ import annotations

"""SOURCE -> SWIPE_INFO TOPIC transformation layer.

SWIPE_INFO is a daily practical-information account, not a news account.
A raw government press-release/news title is a DISCOVERY SOURCE, never a
carousel topic by itself (core/CLAUDE.md product rules). This module turns
a bounded top-N pool of raw discovery candidates into independent,
user-benefit-framed topics -- or rejects a candidate outright when its
real evidence text supports nothing but "this happened" / status-report
value.

Deciding whether a status-report source hides a genuinely derivable
practical topic (and, if so, phrasing that topic) is real semantic
judgment -- not something the existing deterministic keyword scoring in
pipeline/live_discovery.py can do reliably. This calls the same
already-authenticated, ZERO-PAYG `claude` CLI subscription pattern as
pipeline/semantic_claude_cli.py (never api.anthropic.com), exactly ONCE
per discovery run for the whole bounded pool -- never once per source.
"""

import json
import os
import subprocess

from pipeline.semantic_claude_cli import DEFAULT_MODEL, _resolve_executable

DEFAULT_TIMEOUT_SECONDS = 180

SCHEMA = {
    "type": "object",
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["candidate_id", "qualified"],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "qualified": {"type": "boolean"},
                    "derived_topic": {"type": "string"},
                    "user_benefit": {"type": "string"},
                    "why_now": {"type": "string"},
                    "actionability_note": {"type": "string"},
                    "practical_value_signal": {"type": "number"},
                    "save_share_signal": {"type": "number"},
                    "population_reach_signal": {"type": "number"},
                    "rejection_reason": {"type": "string"},
                },
            },
        }
    },
}

SYSTEM_PROMPT = (
    "You are the TOPIC TRANSFORMATION layer for SWIPE_INFO, a Korean daily practical-information "
    "Instagram/Threads account. Core promise: '지금 알아야 돈과 시간을 지키는 정보'. SWIPE_INFO is NOT "
    "a news account -- a reader must finish a post thinking 'I can save money', 'I can get something', "
    "'I avoided a loss/problem', or 'I should save/share this'.\n\n"
    "You receive a batch of raw discovery candidates (government/public-institution source title + a real "
    "body excerpt). Each source is ONLY a discovery opportunity, never a topic by itself. For each candidate, "
    "either (a) derive an independent, practical, user-benefit-framed SWIPE_INFO topic that the ACTUAL "
    "evidence text genuinely supports -- never invent eligibility/amounts/deadlines/procedures beyond what's "
    "in the excerpt -- or (b) reject it if its only real value is 'this happened': a political/institutional "
    "announcement, internal government activity, staffing/personnel news, ceremonial event, corporate PR, "
    "industry promotion, bare statistics with no practical implication, disaster/event status reporting, or "
    "a generic news/press-release summary with no independently derivable benefit, risk, or action for an "
    "ordinary reader.\n\n"
    "Example: source 'Heavy rainfall reported in Geoje' -- BAD topic: '거제에 956mm 폭우가 내렸다' (that's "
    "just reporting). GOOD derived topics, only if the excerpt supports them: '침수 피해를 입었다면 가장 "
    "먼저 해야 할 5가지', '수해 피해 가구가 확인해야 할 지원제도'. Example: source 'Government announces "
    "new program' -- BAD: '정부가 새 제도를 발표했습니다'. GOOD: '오늘부터 신청 가능한 ○○지원금, 나는 "
    "대상일까?' (only if eligibility/deadline/amount are actually in the excerpt).\n\n"
    "For each qualified candidate, also score three signals in [0.0, 1.0] for the DERIVED topic (not the "
    "raw source): practical_value_signal (can the reader actually use this -- save money, get a benefit, "
    "avoid a loss/scam/mistake, save time?), save_share_signal (would someone reasonably save this or send "
    "it to family/friends/coworkers?), population_reach_signal (does this matter to a meaningful number of "
    "ordinary people, not a narrow institutional audience?). For a rejected candidate, qualified=false and "
    "rejection_reason is a short phrase naming which hard-rejection category applies. Output ONLY the "
    "requested JSON: one result object per input candidate_id, same set, any order."
)


class TopicTransformError(RuntimeError):
    pass


def _build_user_prompt(pool: list) -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "candidate_id": c["candidate_id"],
                    "category": c.get("category", ""),
                    "title": c["title"],
                    "body_excerpt": c.get("body_excerpt", "")[:1200],
                }
                for c in pool
            ],
            "instructions": "Transform or reject each candidate per the system prompt. Return one result per candidate_id.",
        },
        ensure_ascii=False,
    )


def _run_once(executable: str, model: str, timeout_seconds: int, user_prompt: str) -> dict:
    cmd = [
        executable, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--tools", "",
        "--no-session-persistence",
        "--model", model,
        "--system-prompt", SYSTEM_PROMPT,
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    try:
        result = subprocess.run(
            cmd, shell=False, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=timeout_seconds, input=user_prompt,
        )
    except subprocess.TimeoutExpired as exc:
        raise TopicTransformError(f"claude CLI timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise TopicTransformError(f"claude CLI failed to start: {exc}") from exc

    if result.returncode != 0:
        raise TopicTransformError(f"claude CLI exited {result.returncode}: {(result.stderr or '')[:300]}")

    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TopicTransformError(f"claude CLI produced non-JSON stdout: {exc}") from exc

    if payload.get("is_error"):
        raise TopicTransformError(f"claude CLI reported is_error=true: {str(payload.get('result', ''))[:300]}")

    parsed = payload.get("structured_output")
    if parsed is None:
        raw = payload.get("result")
        if not isinstance(raw, str):
            raise TopicTransformError("claude CLI response had neither structured_output nor a text result.")
        parsed = json.loads(raw)

    return parsed


def transform_candidates(pool: list, model: str = None, timeout_seconds: int = None) -> dict:
    """pool: list of {candidate_id, title, body_excerpt, category}. Returns
    {candidate_id: result_dict} for every input id -- ONE claude CLI call
    (with one retry on failure) for the whole batch, never one call per
    source. Raises TopicTransformError if both attempts fail -- the caller
    decides the fallback (e.g. treat the whole batch as NEEDS_REVIEW),
    this module never falls back to a paid API."""
    if not pool:
        return {}

    executable = _resolve_executable()
    model = model or os.environ.get("TOPIC_TRANSFORM_CLI_MODEL", DEFAULT_MODEL)
    timeout_seconds = timeout_seconds or int(os.environ.get("TOPIC_TRANSFORM_CLI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    user_prompt = _build_user_prompt(pool)
    expected_ids = {c["candidate_id"] for c in pool}

    last_error = None
    for _attempt in range(2):
        try:
            parsed = _run_once(executable, model, timeout_seconds, user_prompt)
            results = {r["candidate_id"]: r for r in parsed.get("results", []) if r.get("candidate_id") in expected_ids}
            if not results:
                raise TopicTransformError("no recognizable results for the submitted candidate batch")
            return results
        except Exception as exc:  # noqa: BLE001 -- any failure is retry-eligible once, then surfaced
            last_error = exc
            continue

    raise TopicTransformError(f"claude CLI topic transformation failed after 2 attempts: {last_error}")
