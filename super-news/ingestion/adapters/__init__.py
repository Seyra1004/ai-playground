"""Explicit source_type -> adapter map. No dynamic plugin discovery — a
plain dict is sufficient at this scale (see Section 44 of the Phase 2A
contract). Each adapter module exposes fetch_source(source_config,
sleep=None) -> ingestion.records.AdapterOutcome."""

from ingestion.adapters import naver, rss

ADAPTER_REGISTRY = {
    "rss": rss.fetch_source,
    "naver_news_api": naver.fetch_source,
}


def get_adapter(source_type):
    try:
        return ADAPTER_REGISTRY[source_type]
    except KeyError:
        raise ValueError(f"No adapter registered for source_type {source_type!r}.")
