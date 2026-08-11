"""Deterministic hash over the EFFECTIVE parsed source registry — never
over the raw YAML bytes, so comments/whitespace/key-ordering in
sources.yaml never change the hash, but any real configuration change
does.

Hash input is built from the already-validated SourceConfig objects
returned by ingestion.registry.load_source_registry(), which never carry a
secret VALUE — only credential_env variable NAMES (see registry.py). That
is what keeps a rotated NAVER_CLIENT_ID/SECRET value out of the hash while
still making a renamed credential_env entry change it.
"""

import hashlib
import json
from dataclasses import asdict


def compute_registry_hash(registry):
    """registry: dict[source_name -> SourceConfig] as returned by
    ingestion.registry.load_source_registry(). Returns a SHA-256 hex
    digest. Same effective configuration (regardless of YAML formatting)
    -> same hash; any field change -> a different hash."""
    canonical = [asdict(cfg) for _, cfg in sorted(registry.items())]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
