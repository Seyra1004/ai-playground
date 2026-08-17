"""LLM structured-output validation: ID-grounding + non-empty reason +
max-5 enforcement. Every category is validated INDEPENDENTLY -- a
hallucinated id or malformed selection in one category never blocks the
other categories' reports (partial-category validation isolation). This is
the only backstop between raw model output and the DB; nothing downstream
re-checks these invariants.

NATIVE KOREAN TEXT QUALITY + FACT GROUNDING (quality-hardening phase):
every LLM-native Korean text field validated here (news-selection
`reason`, Producer/Music-Trend Intelligence's text fields) is additionally
checked with report.text_quality's shared, deterministic gibberish/
refusal-output detector (the same real defect class report/translation_
validation.py already catches for translated text, reused here for text an
LLM wrote directly) and, where real evidence text is available, its own
explicit YEAR/PERCENTAGE/VERSION/CURRENCY-MAGNITUDE tokens must be
traceable to that evidence -- an unsupported token is treated exactly like
any other invalid field: the item is rejected, never silently kept or
partially degraded (matching this module's existing all-or-nothing
per-category/per-insight fail-safe contract).
"""

from report.text_quality import is_malformed_synthesis_text, unsupported_fact_tokens

MAX_SELECTIONS_PER_CATEGORY = 5


class CategoryValidationError(Exception):
    def __init__(self, category, reason):
        self.category = category
        self.reason = reason
        super().__init__(f"{category}: {reason}")


def validate_category_selection(category, selections, candidate_ids, title_by_id=None):
    """selections: the raw value the LLM returned for this category (should
    be a list of {"id": int, "reason": str} dicts). candidate_ids: the set
    of ids that were actually offered to the LLM for this category.
    title_by_id: optional {id: normalized_title} map (the real evidence the
    LLM was given for that candidate) -- when provided, `reason` is checked
    for native-text-quality and evidence-grounded fact tokens; omitted
    (None, the default) skips both new checks so existing callers/tests
    that don't have title text handy are unaffected. Returns `selections`
    unchanged if valid; raises CategoryValidationError otherwise -- never
    silently drops or truncates an invalid selection."""
    if not isinstance(selections, list):
        raise CategoryValidationError(category, f"expected a list, got {type(selections).__name__}")
    if len(selections) > MAX_SELECTIONS_PER_CATEGORY:
        raise CategoryValidationError(
            category, f"{len(selections)} selections exceeds max {MAX_SELECTIONS_PER_CATEGORY}"
        )
    for item in selections:
        if not isinstance(item, dict) or "id" not in item or "reason" not in item:
            raise CategoryValidationError(category, f"malformed selection item: {item!r}")
        if item["id"] not in candidate_ids:
            raise CategoryValidationError(category, f"id {item['id']} is not a valid candidate id")
        if not isinstance(item["reason"], str) or not item["reason"].strip():
            raise CategoryValidationError(category, f"empty reason for id {item['id']}")
        if title_by_id is not None:
            if is_malformed_synthesis_text(item["reason"]):
                raise CategoryValidationError(category, f"malformed/gibberish reason for id {item['id']}")
            evidence_text = title_by_id.get(item["id"]) or ""
            unsupported = unsupported_fact_tokens(item["reason"], evidence_text)
            if unsupported:
                raise CategoryValidationError(
                    category, f"reason for id {item['id']} asserts unsupported fact(s): {unsupported}"
                )
    return selections


MAX_PRODUCER_INSIGHTS = 5


class ProducerValidationError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


_PRODUCER_INSIGHT_TEXT_FIELDS = ("what_is_moving", "why_it_matters", "what_to_watch", "what_could_i_make_now")

# FINAL 90+ QUALITY CORRECTION PASS (confirmed real, systemic defect: every
# recent what_could_i_make_now value recommended writing a newsletter/
# article/explainer about the news itself instead of a real music-making
# action). A producer/composer reader has no use for editorial-content-
# creation advice -- deterministic keyword reject, shared with report/
# web_render_v2.py's render-time suppression of already-cached bad rows.
CONTENT_CREATION_ADVICE_KEYWORDS = (
    "뉴스레터", "newsletter", "explainer", "브리핑", "briefing", "타임라인", "timeline",
    "카드뉴스", "요약 카드", "정리 카드", "뉴스 카드", "기사를 작성", "기사를 쓸", "아티클", "article",
    "블로그", "blog post", "포스트를 작성", "recap", "리캡", "뉴스레터 섹션", "콘텐츠를 제작",
    "글을 작성", "글을 쓸", "정리 글", "요약 글", "리스티클",
    "분석 메모", "메모를 작성", "메모 작성", "정리 메모",
)


def is_content_creation_advice(text):
    """True when `text` recommends making a piece of EDITORIAL CONTENT
    (a newsletter/article/explainer/briefing/recap about the news) rather
    than a real music-making/A&R/business action -- see
    report.producer_synthesis's own what_could_i_make_now prompt fix for
    the forward-looking half of this guard."""
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in CONTENT_CREATION_ADVICE_KEYWORDS)


# SUPER NEWS FINAL ROLLBACK-RESTORE PASS (2026-08-17, confirmed real
# defect): Producer/A&R Takeaways draws from its OWN evidence catalog
# (report.producer_synthesis / music_trend_intelligence's kpop_ar_notes/
# producer_references), a separate pipeline from the INDUSTRY-section/
# hero gossip downrank in report.web_data_v2's
# _MUSIC_INDUSTRY_DOWNRANK_KEYWORDS -- so a pure fan/social-comment
# gossip item (no songwriting/production/A&R/business signal) can still
# reach a Producer/A&R card even though the same item would already be
# downranked out of INDUSTRY/hero placement. Deterministic keyword
# reject, same style as CONTENT_CREATION_ADVICE_KEYWORDS above: only
# rejects when the text carries a real gossip/social-comment marker AND
# none of the topics a composer/producer reader actually needs.
GOSSIP_MARKER_KEYWORDS = (
    "삭제된", "deleted comment", "deleted tweet", "deleted tiktok",
    "댓글 논란", "trolled", "claps back", "clapped back", "팬덤 갈등", "fan feud",
    "논란", "controversy", "가십", "gossip", "열애설", "dating rumor",
    "이혼", "divorce", "결별", "breakup", "루머", "rumor",
)
MUSIC_RELEVANCE_KEYWORDS = (
    "프로듀싱", "production", "작곡", "songwriting", "편곡", "arrangement",
    "사운드", "sound design", "믹싱", "mixing", "보컬", "vocal",
    "a&r", "레이블", "label", "저작권", "copyright", "라이선스", "licensing",
    "로열티", "royalty", "플랫폼 정책", "platform policy", "차트", "chart",
    "시장", "market", "장르", "genre", "발매", "release", "계약", "deal",
)


def is_low_value_gossip_takeaway(text):
    """True when `text` is a fan/social-comment gossip item (a deleted-
    comment spat, a fandom feud, an idol-controversy story) that carries
    NONE of the production/songwriting/A&R/label-business/platform-
    policy/rights-copyright/royalty-licensing/market-signal topics a
    composer/producer reader actually needs -- see GOSSIP_MARKER_KEYWORDS/
    MUSIC_RELEVANCE_KEYWORDS. A story that happens to mention a gossip
    marker (e.g. a real rights "논란"/controversy that IS a genuine
    licensing dispute) is never rejected as long as it also carries a
    real relevance keyword."""
    if not text:
        return False
    lowered = text.lower()
    has_gossip_marker = any(keyword in lowered for keyword in GOSSIP_MARKER_KEYWORDS)
    has_relevance = any(keyword in lowered for keyword in MUSIC_RELEVANCE_KEYWORDS)
    return has_gossip_marker and not has_relevance


def validate_producer_insights(parsed_output, valid_refs, evidence_by_ref=None):
    """parsed_output: the raw value the LLM returned for the Producer
    Intelligence call (should be {"insights": [...]}). valid_refs: the set
    of evidence `ref` labels actually offered in the evidence catalog for
    this call. evidence_by_ref: optional {ref: summary_text} map (the real
    evidence catalog text) -- when provided, every text field is checked
    for native-text-quality (gibberish/refusal) and its own explicit
    YEAR/PERCENTAGE/VERSION/CURRENCY-MAGNITUDE tokens must be traceable to
    the combined text of that insight's OWN cited evidence_refs; omitted
    (None, the default) skips both new checks so existing callers/tests
    are unaffected. Returns the `insights` list unchanged if valid; raises
    ProducerValidationError otherwise -- same id/ref-grounding discipline
    validate_category_selection already enforces for news selections, so
    the LLM can never cite evidence that wasn't actually computed this
    run. Every insight must cite at least one real ref -- an insight with
    no evidence_refs is not grounded and is rejected, never silently kept
    as an ungrounded opinion.

    MUSIC INTELLIGENCE COMPLETION phase's 6-question contract: each
    insight has 4 required text fields (what_is_moving/why_it_matters/
    what_to_watch/what_could_i_make_now) instead of the older bare
    action/why pair -- see report.producer_synthesis's own schema/prompt
    docstring for which of the four is the OBSERVED FACT vs. AI
    INFERENCE."""
    if not isinstance(parsed_output, dict) or "insights" not in parsed_output:
        raise ProducerValidationError(f"expected an object with an 'insights' key, got {parsed_output!r}")
    insights = parsed_output["insights"]
    if not isinstance(insights, list):
        raise ProducerValidationError(f"'insights' must be a list, got {type(insights).__name__}")
    # The prompt asks for at most MAX_PRODUCER_INSIGHTS, ordered strongest
    # first -- a real model occasionally overshoots the count despite that
    # instruction. Truncating here (rather than rejecting the whole day's
    # output) still enforces the hard cap deterministically, and every kept
    # insight is still individually validated below exactly like any other.
    insights = insights[:MAX_PRODUCER_INSIGHTS]
    for insight in insights:
        if not isinstance(insight, dict):
            raise ProducerValidationError(f"malformed insight: {insight!r}")
        for field in _PRODUCER_INSIGHT_TEXT_FIELDS + ("evidence_refs", "confidence"):
            if field not in insight:
                raise ProducerValidationError(f"insight missing required field {field!r}: {insight!r}")
        for field in _PRODUCER_INSIGHT_TEXT_FIELDS:
            if not isinstance(insight[field], str) or not insight[field].strip():
                raise ProducerValidationError(f"empty {field}: {insight!r}")
        if is_content_creation_advice(insight["what_could_i_make_now"]):
            raise ProducerValidationError(
                f"what_could_i_make_now recommends editorial content creation, not a real "
                f"music-making/A&R action: {insight['what_could_i_make_now']!r}"
            )
        if not isinstance(insight["evidence_refs"], list) or not insight["evidence_refs"]:
            raise ProducerValidationError(f"evidence_refs must be a non-empty list: {insight!r}")
        for ref in insight["evidence_refs"]:
            if ref not in valid_refs:
                raise ProducerValidationError(f"evidence ref {ref!r} is not in the evidence catalog")
        if insight["confidence"] not in ("LOW", "MEDIUM", "HIGH"):
            raise ProducerValidationError(f"invalid confidence: {insight.get('confidence')!r}")
        if evidence_by_ref is not None:
            evidence_text = " ".join(
                evidence_by_ref.get(ref, "") for ref in insight["evidence_refs"]
            )
            for field in _PRODUCER_INSIGHT_TEXT_FIELDS:
                if is_malformed_synthesis_text(insight[field]):
                    raise ProducerValidationError(f"malformed/gibberish {field}: {insight!r}")
                unsupported = unsupported_fact_tokens(insight[field], evidence_text)
                if unsupported:
                    raise ProducerValidationError(f"{field} asserts unsupported fact(s) {unsupported}: {insight!r}")
    return insights


_MUSIC_TREND_LIST_FIELDS = ("genre_signals", "production_notes", "producer_references", "kpop_ar_notes")
MAX_MUSIC_TREND_ITEMS_PER_LIST = 3


class MusicTrendValidationError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def validate_music_trend_signals(parsed_output, valid_refs, evidence_by_ref=None):
    """parsed_output: the raw value the LLM returned for Music Trend
    Intelligence (should have genre_signals/production_notes/
    producer_references/kpop_ar_notes list keys). valid_refs: the set of
    evidence `ref` labels actually offered in the evidence catalog for
    this call. evidence_by_ref: optional {ref: summary_text} map -- when
    provided, `observed`/`interpretation` are checked for native-text-
    quality (gibberish/refusal) and their own explicit YEAR/PERCENTAGE/
    VERSION/CURRENCY-MAGNITUDE tokens must be traceable to the combined
    text of that item's OWN cited evidence_refs; omitted (None, the
    default) skips both new checks so existing callers/tests are
    unaffected. Returns parsed_output unchanged if valid; raises
    MusicTrendValidationError otherwise -- same ref-grounding discipline
    validate_producer_insights already enforces, applied independently to
    all four lists so a bad item in one category can never silently
    invalidate an honest, well-grounded item in another. An ungrounded
    item (empty evidence_refs, or a ref that doesn't exist in the
    catalog) is always rejected outright, never silently kept."""
    if not isinstance(parsed_output, dict):
        raise MusicTrendValidationError(f"expected an object, got {type(parsed_output).__name__}")
    for field in _MUSIC_TREND_LIST_FIELDS:
        if field not in parsed_output:
            raise MusicTrendValidationError(f"missing required key {field!r}")
        items = parsed_output[field]
        if not isinstance(items, list):
            raise MusicTrendValidationError(f"{field!r} must be a list, got {type(items).__name__}")
        # Same truncate-rather-than-reject discipline as
        # validate_producer_insights above -- the prompt asks for at most
        # MAX_MUSIC_TREND_ITEMS_PER_LIST per field, ordered strongest first;
        # a real model occasionally overshoots despite that instruction.
        items = items[:MAX_MUSIC_TREND_ITEMS_PER_LIST]
        parsed_output[field] = items
        for item in items:
            if not isinstance(item, dict):
                raise MusicTrendValidationError(f"{field!r}: malformed item: {item!r}")
            for key in ("observed", "interpretation", "evidence_refs", "confidence"):
                if key not in item:
                    raise MusicTrendValidationError(f"{field!r}: item missing required field {key!r}: {item!r}")
            if not isinstance(item["observed"], str) or not item["observed"].strip():
                raise MusicTrendValidationError(f"{field!r}: empty observed: {item!r}")
            if not isinstance(item["interpretation"], str) or not item["interpretation"].strip():
                raise MusicTrendValidationError(f"{field!r}: empty interpretation: {item!r}")
            if not isinstance(item["evidence_refs"], list) or not item["evidence_refs"]:
                raise MusicTrendValidationError(f"{field!r}: evidence_refs must be a non-empty list: {item!r}")
            for ref in item["evidence_refs"]:
                if ref not in valid_refs:
                    raise MusicTrendValidationError(f"{field!r}: evidence ref {ref!r} is not in the evidence catalog")
            if item["confidence"] not in ("LOW", "MEDIUM", "HIGH"):
                raise MusicTrendValidationError(f"{field!r}: invalid confidence: {item.get('confidence')!r}")
            if evidence_by_ref is not None:
                evidence_text = " ".join(evidence_by_ref.get(ref, "") for ref in item["evidence_refs"])
                for key in ("observed", "interpretation"):
                    if is_malformed_synthesis_text(item[key]):
                        raise MusicTrendValidationError(f"{field!r}: malformed/gibberish {key}: {item!r}")
                    unsupported = unsupported_fact_tokens(item[key], evidence_text)
                    if unsupported:
                        raise MusicTrendValidationError(
                            f"{field!r}: {key} asserts unsupported fact(s) {unsupported}: {item!r}"
                        )
    return parsed_output


def validate_all_categories(parsed_output, candidates_by_category, snippet_by_id=None):
    """Returns (valid, errors): valid is dict[category -> selections] for
    categories that passed; errors is dict[category -> CategoryValidationError]
    for categories that didn't. Every category in candidates_by_category
    appears in exactly one of the two dicts.

    snippet_by_id (FINAL 90+ QUALITY CORRECTION PASS -- confirmed real
    defect): optional {id: snippet_text} map. The fact-check evidence text
    used to be the candidate's normalized_title ONLY -- a real ECONOMY
    story's headline read "선 넘은 가계빚, 사상첫 2000조" (no currency-unit
    word after "조"), so the currency-magnitude extractor found nothing
    there even though the item's own real snippet said "2000조원대에
    진입", producing a FALSE-POSITIVE "unsupported fact" rejection on a
    genuinely well-supported figure. When provided, snippet_by_id's text is
    appended to the title for fact-checking (never REPLACES the title --
    still real, already-collected text, just more of it); omitted (None,
    the default) keeps prior behavior for existing callers/tests."""
    valid = {}
    errors = {}

    if not isinstance(parsed_output, dict):
        for category in candidates_by_category:
            errors[category] = CategoryValidationError(
                category, f"malformed structured output: root is {type(parsed_output).__name__}, not an object"
            )
        return valid, errors

    snippet_by_id = snippet_by_id or {}
    for category, candidates in candidates_by_category.items():
        candidate_ids = {c["id"] for c in candidates}
        title_by_id = {}
        for c in candidates:
            title = c.get("normalized_title") or ""
            snippet = snippet_by_id.get(c["id"])
            title_by_id[c["id"]] = f"{title} {snippet}".strip() if snippet else title
        selections = parsed_output.get(category)
        try:
            if selections is None and category not in parsed_output:
                raise CategoryValidationError(category, "missing category key in structured output")
            valid[category] = validate_category_selection(category, selections, candidate_ids, title_by_id)
        except CategoryValidationError as exc:
            errors[category] = exc

    return valid, errors


# ---- INCOMPLETE SUMMARY DETECTION (content-quality hardening pass,
# 2026-08-17) -- deterministic, keyword-presence check: when the
# INGESTED snippet (the only source text this pipeline's synthesis layer
# ever has access to -- RSS-only ingestion never fetches the full
# original article body) mentions a high-severity fact that the
# GENERATED summary/why_it_matters text omits entirely, the event's real
# meaning may be understated without it. Deliberately narrow in scope:
# this can only ever catch "did the synthesis drop a fact that WAS
# present in its own available input" -- it structurally CANNOT detect a
# fact that exists only in the full original article but never made it
# into the ingested snippet at all (confirmed real case: a TechCrunch
# article's stepfather-suicide detail was never in the RSS snippet this
# pipeline ingested, so no synthesis-layer fix could have surfaced it --
# that is an ingestion-scope limitation, not a synthesis defect this
# check is meant to catch). A diagnostic signal, not a hard content-
# generation rule -- never used to reject/regenerate output on its own,
# since a real editorial summary is legitimately allowed to omit a
# secondary detail while keeping the primary fact; it flags candidates
# for human/editorial review. ----
_HIGH_SEVERITY_FACT_KEYWORDS = (
    "사망", "사망자", "숨졌다", "숨진", "자살", "살해", "피살", "사살", "폭발",
    "died", "death", "dead", "killed", "suicide", "fatality", "fatalities",
)


def is_incomplete_summary(source_snippet, generated_text):
    """True when `source_snippet` (the real, already-ingested article
    snippet -- never the full original article) mentions a high-severity
    fact that `generated_text` (a synthesized summary/why_it_matters/
    bullet, or several such fields concatenated) omits entirely. False
    when either argument is empty, or when the snippet carries none of
    the tracked high-severity keywords -- an ordinary summary is never
    flagged just for being shorter than its source."""
    if not source_snippet or not generated_text:
        return False
    snippet_lower = source_snippet.lower()
    generated_lower = generated_text.lower()
    return any(
        keyword in snippet_lower and keyword not in generated_lower
        for keyword in _HIGH_SEVERITY_FACT_KEYWORDS
    )
