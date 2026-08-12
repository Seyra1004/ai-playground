"""News synthesis: ONE combined LLM call covering all 3 news categories
(AI/ECONOMY/SOCIETY) per daily run, not 3 separate calls -- the core
cost/latency decision from the Report V1 design. Depends only on
report.llm_interface.StructuredLLM: never imports `anthropic` or any
provider SDK, never hardcodes a model string.

Canonical input-hashing contract (must not drift): UTF-8 encoding,
json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False),
hashed with sha256. sort_keys makes dict-key insertion order irrelevant;
candidate list order is made deterministic upstream by
report.candidate_selection (sorted by (-source_count, event_key)), so this
module never re-sorts -- it hashes exactly the list it was given. The hash
covers report_date_kst + prompt_version + the exact candidate payload, so a
prompt_version bump or ANY candidate change (add/remove/reorder/edit)
produces a different hash and therefore a fresh LLM call + a new
llm_interpretations row (a "revision"), while an identical call on the same
day reuses the prior row's output with zero LLM calls.
"""

import hashlib
import json

from report.validation import MAX_SELECTIONS_PER_CATEGORY

PROMPT_VERSION = "v1"

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["id", "reason"],
    "additionalProperties": False,
}


def canonical_json(obj):
    """The one and only serialization used anywhere an input_hash is
    computed. Deterministic across dict key insertion order (sort_keys) and
    across Python versions/locales (fixed separators, explicit UTF-8)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hashable_candidates(candidates_by_category):
    """Strips fields that don't affect what the LLM was actually shown
    (event_key, item_ids, entity_type are internal bookkeeping) so the hash
    reflects the LLM-visible input only."""
    return {
        category: [
            {
                "id": c["id"],
                "entity_name": c["entity_name"],
                "normalized_title": c["normalized_title"],
                "source_count": c["source_count"],
            }
            for c in candidates
        ]
        for category, candidates in candidates_by_category.items()
    }


def compute_input_hash(report_date_kst, prompt_version, candidates_by_category):
    payload = {
        "report_date_kst": report_date_kst,
        "prompt_version": prompt_version,
        "candidates": _hashable_candidates(candidates_by_category),
    }
    canonical = canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_schema(categories):
    category_schema = {"type": "array", "items": _ITEM_SCHEMA}
    return {
        "type": "object",
        "properties": {category: category_schema for category in categories},
        "required": list(categories),
        "additionalProperties": False,
    }


def _build_prompts(candidates_by_category):
    system_prompt = (
        "You are a news editor selecting the most important stories for a daily "
        "Korean-audience intelligence digest. For each category below, select up "
        f"to {MAX_SELECTIONS_PER_CATEGORY} candidates by their exact integer id, each "
        "with a one-sentence reason for why it matters. Only use ids that appear "
        "in the candidate list for that category -- never invent an id. If a "
        "category has no candidates worth reporting, return an empty array for it."
    )
    user_prompt = canonical_json({"candidates": _hashable_candidates(candidates_by_category)})
    return system_prompt, user_prompt


def find_reusable_interpretation(conn, input_hash):
    return conn.execute(
        "SELECT * FROM llm_interpretations WHERE input_hash = ? ORDER BY id DESC LIMIT 1",
        (input_hash,),
    ).fetchone()


def synthesize_news(conn, llm, candidates_by_category, report_date_kst, prompt_version=PROMPT_VERSION):
    """Returns None if every category has zero candidates (no LLM call is
    made, no llm_interpretations row is written). Otherwise returns a dict
    ready for report.persistence to write as one llm_interpretations row:
    {"input_hash", "prompt_version", "model_used", "output_text",
    "input_tokens", "output_tokens", "estimated_cost", "parsed", "reused"}."""
    if all(len(candidates) == 0 for candidates in candidates_by_category.values()):
        return None

    input_hash = compute_input_hash(report_date_kst, prompt_version, candidates_by_category)
    existing = find_reusable_interpretation(conn, input_hash)
    if existing is not None:
        return {
            "input_hash": input_hash,
            "prompt_version": prompt_version,
            "model_used": existing["model_used"],
            "output_text": existing["output_text"],
            "input_tokens": existing["input_tokens"],
            "output_tokens": existing["output_tokens"],
            "estimated_cost": existing["estimated_cost"],
            "parsed": json.loads(existing["output_text"]),
            "reused": True,
        }

    system_prompt, user_prompt = _build_prompts(candidates_by_category)
    schema = _build_schema(candidates_by_category.keys())
    response = llm.generate_structured(system_prompt, user_prompt, schema)
    return {
        "input_hash": input_hash,
        "prompt_version": prompt_version,
        "model_used": response.model_used,
        "output_text": response.raw_text,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "estimated_cost": None,
        "parsed": response.parsed,
        "reused": False,
    }
