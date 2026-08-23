from __future__ import annotations

"""Unattended, ZERO-PAYG semantic authoring for SWIPE_INFO: shells out to
the already-authenticated `claude` CLI (Claude Code, subscription auth) in
non-interactive print mode -- the exact pattern already proven in
production by super-news/report/llm_claude_cli.py. Never imports the
anthropic SDK, never calls api.anthropic.com, explicitly strips
ANTHROPIC_API_KEY from the child process environment. A CLI failure never
falls back to a paid API -- the caller (scripts/run_daily.py) falls back to
the existing deterministic mechanical assembler instead.
"""

import json
import os
import shutil
import subprocess

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_CLI_PATH = "/home/ubuntu/.local/bin/claude"

# The only visual_data["type"] values the renderer/visual-QA actually
# support (renderer/html_renderer.py::_render_visual) -- "real_image" is
# deliberately excluded since that's assigned later by image_acquisition.py,
# never authored by the semantic layer.
_VISUAL_TYPES = [
    "stat_hero", "highlight_box", "checklist", "exclusion_list", "steps",
    "comparison", "process_flow", "evidence_card", "bar_chart", "cta_panel",
]

SCHEMA = {
    "type": "object",
    "required": ["pages", "instagram_caption", "threads_text"],
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["page_number", "role", "headline", "body", "visual_ref", "visual_data"],
                "properties": {
                    "page_number": {"type": "integer"},
                    "role": {"type": "string"},
                    "headline": {"type": "string"},
                    "body": {"type": "string"},
                    "visual_ref": {"type": "string"},
                    "visual_data": {
                        "type": "object",
                        "required": ["type"],
                        "properties": {"type": {"type": "string", "enum": _VISUAL_TYPES}},
                        "additionalProperties": True,
                    },
                },
            },
        },
        "instagram_caption": {"type": "string"},
        "threads_text": {"type": "string"},
    },
}

SYSTEM_PROMPT = (
    "You are the semantic/editorial layer for SWIPE_INFO, a Korean practical-information "
    "Instagram/Threads account. Core promise: '지금 알아야 돈과 시간을 지키는 정보'. "
    "Rules: preserve every verified fact EXACTLY as given in fact_sheet -- never invent numbers, "
    "deadlines, eligibility, exclusions, or procedures beyond what's in the evidence. Page 1 must "
    "be a concrete benefit/loss hook, not the formal policy name. Simple, clear Korean, "
    "mobile-readable, no exaggeration/clickbait. Threads copy must NOT be a copy of the Instagram "
    "caption -- different structure/opening, same underlying facts. Output ONLY the requested JSON "
    "matching the schema: one page object per role in page_plan, in the exact same order, each "
    "with a headline, a body, and a short visual_ref label.\n\n"
    "Every page also REQUIRES a visual_data object -- pick whichever of these types genuinely fits "
    "that page's content (do not force a mismatched one), and its visual_ref must describe the same "
    "visual: "
    '{"type":"stat_hero","big_text":str,"sub_text":str} single huge stat/amount; '
    '{"type":"highlight_box","icon":emoji,"highlight":str} one-line emphasis; '
    '{"type":"checklist","items":[str,...]} eligibility/requirement list; '
    '{"type":"exclusion_list","items":[str,...]} exclusions/warnings list; '
    '{"type":"steps","items":[str,...]} ordered procedure; '
    '{"type":"comparison","left":{"icon":emoji,"title":str,"desc":str},"right":{...}} two-option compare; '
    '{"type":"process_flow","steps":[str,...]} vertical stage flow; '
    '{"type":"evidence_card","publisher":str,"source_label":str,"published_at":str,"url":str} official-source citation, '
    "values must come from fact_sheet's own source fields; "
    '{"type":"bar_chart","items":[[label,value],...],"unit":str} numeric comparison; '
    '{"type":"cta_panel","button_text":str,"region":str (optional)} final call-to-action. '
    "Every items/big_text/highlight/etc. value must be derived only from fact_sheet -- never invented."
)


class SemanticCLIError(RuntimeError):
    pass


def _resolve_executable():
    override = os.environ.get("CLAUDE_CLI_PATH", "")
    if override:
        return override
    resolved = shutil.which("claude") or (DEFAULT_CLI_PATH if os.path.isfile(DEFAULT_CLI_PATH) else None)
    if not resolved:
        raise SemanticCLIError("claude CLI not found on PATH or at default install path.")
    return resolved


def _build_user_prompt(fact_sheet_dict: dict, page_plan: list) -> str:
    return json.dumps(
        {
            "fact_sheet": fact_sheet_dict,
            "page_plan": page_plan,
            "instructions": (
                "Author 'pages' (exactly len(page_plan) items, one per role, same order), "
                "'instagram_caption', and 'threads_text' using ONLY the fact_sheet's fields/claims/sources."
            ),
        },
        ensure_ascii=False,
    )


def _run_once(executable, model, timeout_seconds, user_prompt):
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
        raise SemanticCLIError(f"claude CLI timed out after {timeout_seconds}s") from exc
    except OSError as exc:
        raise SemanticCLIError(f"claude CLI failed to start: {exc}") from exc

    if result.returncode != 0:
        raise SemanticCLIError(f"claude CLI exited {result.returncode}: {(result.stderr or '')[:300]}")

    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SemanticCLIError(f"claude CLI produced non-JSON stdout: {exc}") from exc

    if payload.get("is_error"):
        raise SemanticCLIError(f"claude CLI reported is_error=true: {str(payload.get('result', ''))[:300]}")

    parsed = payload.get("structured_output")
    if parsed is None:
        raw = payload.get("result")
        if not isinstance(raw, str):
            raise SemanticCLIError("claude CLI response had neither structured_output nor a text result.")
        parsed = json.loads(raw)  # a plain parse failure here is a normal retry-eligible exception

    return parsed


def generate_semantic_output(fact_sheet_dict: dict, page_plan: list, model: str = None, timeout_seconds: int = None) -> dict:
    """One Claude CLI call, with one retry on any failure, validated against
    page_plan (role list + order must match exactly). Raises
    SemanticCLIError if both attempts fail -- the caller decides the
    fallback (deterministic mechanical assembly or NEEDS_REVIEW), this
    module never falls back to a paid API itself."""
    executable = _resolve_executable()
    model = model or os.environ.get("SEMANTIC_CLI_MODEL", DEFAULT_MODEL)
    timeout_seconds = timeout_seconds or int(os.environ.get("SEMANTIC_CLI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    user_prompt = _build_user_prompt(fact_sheet_dict, page_plan)

    last_error = None
    for _attempt in range(2):  # one call + one retry, per spec
        try:
            parsed = _run_once(executable, model, timeout_seconds, user_prompt)
            roles = [p.get("role") for p in parsed.get("pages", [])]
            if roles != page_plan:
                raise SemanticCLIError(f"returned page roles {roles} do not match page_plan {page_plan}")
            if not parsed.get("instagram_caption") or not parsed.get("threads_text"):
                raise SemanticCLIError("missing instagram_caption or threads_text")
            return parsed
        except Exception as exc:  # noqa: BLE001 -- any failure is retry-eligible once, then surfaced
            last_error = exc
            continue

    raise SemanticCLIError(f"claude CLI semantic authoring failed after 2 attempts: {last_error}")
