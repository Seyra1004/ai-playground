"""Canonical ingestion record contract — the one shape every adapter must
produce, regardless of source_type. Persistence (ingestion/persistence.py)
is the only thing that knows how this maps onto the raw_items table; no
adapter writes SQL.

Adapter output model vs DB persistence mapping are deliberately kept
distinct: IngestionRecord.extra is a plain dict (or None) here — JSON
serialization into raw_items.extra_json happens only in persistence.py, at
the DB boundary, not in adapter code.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IngestionRecord:
    source_name: str
    source_item_key: str
    source_type: str
    source_url: str
    collected_at: str
    title: str = None
    snippet: str = None
    published_at: str = None
    region: str = None
    payload_hash: str = None
    extra: dict = None

    def __post_init__(self):
        for required in ("source_name", "source_item_key", "source_type", "source_url", "collected_at"):
            if not getattr(self, required):
                raise ValueError(f"IngestionRecord.{required} must be non-empty.")


@dataclass(frozen=True)
class AdapterOutcome:
    """What a single adapter run produced. `parse_errors` counts source
    items that were fetched but could not be turned into a valid
    IngestionRecord (e.g. a malformed RSS entry) — distinct from a total
    fetch failure, which the adapter raises instead of returning."""

    records: list = field(default_factory=list)
    parse_errors: int = 0
