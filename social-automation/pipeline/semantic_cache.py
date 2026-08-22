from __future__ import annotations

import json
import os

from core.cache import compute_hash

# Bump when the editorial approach/prompt for semantic authoring changes, so
# old cached copy isn't silently reused under a new writing standard.
SEMANTIC_VERSION = "v1"


def compute_semantic_cache_key(evidence_hash: str, account_config_hash: str, brand_hash: str) -> str:
    return compute_hash(
        {
            "evidence_hash": evidence_hash,
            "account_config_hash": account_config_hash,
            "brand_hash": brand_hash,
            "semantic_version": SEMANTIC_VERSION,
        }
    )


def _path(cache_dir: str, cache_key: str) -> str:
    return os.path.join(cache_dir, f"{cache_key}.json")


def load_semantic_output(cache_dir: str, cache_key: str):
    """Returns the cached {"pages": [...], "instagram_caption": ..., "threads_text": ...}
    dict, or None if this evidence/config/brand/prompt-version combination has
    never been authored. Never regenerates -- that's the caller's job (the
    semantic layer), this module only reads/writes the cache."""
    path = _path(cache_dir, cache_key)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_semantic_output(cache_dir: str, cache_key: str, data: dict) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    path = _path(cache_dir, cache_key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
