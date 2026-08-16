"""report.validation: ID-grounding, non-empty reason, max-5 enforcement,
partial-category isolation, malformed-output handling."""

from report.validation import (
    MAX_MUSIC_TREND_ITEMS_PER_LIST,
    MAX_PRODUCER_INSIGHTS,
    MAX_SELECTIONS_PER_CATEGORY,
    CategoryValidationError,
    MusicTrendValidationError,
    ProducerValidationError,
    validate_all_categories,
    validate_category_selection,
    validate_music_trend_signals,
    validate_producer_insights,
)

CANDIDATES = {
    "AI": [{"id": 1}, {"id": 2}, {"id": 3}],
    "ECONOMY": [{"id": 10}],
    "SOCIETY": [{"id": 20}, {"id": 21}],
}
CANDIDATE_IDS = {cat: {c["id"] for c in items} for cat, items in CANDIDATES.items()}


def test_valid_selection_passes():
    selections = [{"id": 1, "reason": "important"}]
    result = validate_category_selection("AI", selections, CANDIDATE_IDS["AI"])
    assert result == selections


# ---- hallucinated-ID rejection ---------------------------------------------


def test_hallucinated_id_rejected():
    selections = [{"id": 999, "reason": "not a real candidate"}]
    try:
        validate_category_selection("AI", selections, CANDIDATE_IDS["AI"])
        assert False, "expected CategoryValidationError"
    except CategoryValidationError as exc:
        assert exc.category == "AI"
        assert "999" in exc.reason


# ---- empty-reason rejection -------------------------------------------------


def test_empty_reason_rejected():
    selections = [{"id": 1, "reason": ""}]
    try:
        validate_category_selection("AI", selections, CANDIDATE_IDS["AI"])
        assert False, "expected CategoryValidationError"
    except CategoryValidationError as exc:
        assert "reason" in exc.reason


def test_whitespace_only_reason_rejected():
    selections = [{"id": 1, "reason": "   "}]
    try:
        validate_category_selection("AI", selections, CANDIDATE_IDS["AI"])
        assert False, "expected CategoryValidationError"
    except CategoryValidationError:
        pass


# ---- max_selection enforcement ----------------------------------------------


def test_max_selection_enforcement():
    selections = [{"id": 1, "reason": "x"}] * (MAX_SELECTIONS_PER_CATEGORY + 1)
    try:
        validate_category_selection("AI", selections, {1})
        assert False, "expected CategoryValidationError"
    except CategoryValidationError as exc:
        assert str(MAX_SELECTIONS_PER_CATEGORY + 1) in exc.reason


def test_exactly_max_selections_allowed():
    selections = [{"id": i, "reason": "x"} for i in range(1, MAX_SELECTIONS_PER_CATEGORY + 1)]
    result = validate_category_selection("AI", selections, set(range(1, MAX_SELECTIONS_PER_CATEGORY + 1)))
    assert len(result) == MAX_SELECTIONS_PER_CATEGORY


# ---- malformed-structured-output failure ------------------------------------


def test_malformed_selection_item_rejected():
    try:
        validate_category_selection("AI", [{"id": 1}], CANDIDATE_IDS["AI"])  # missing "reason"
        assert False, "expected CategoryValidationError"
    except CategoryValidationError:
        pass


def test_non_list_selections_rejected():
    try:
        validate_category_selection("AI", "not a list", CANDIDATE_IDS["AI"])
        assert False, "expected CategoryValidationError"
    except CategoryValidationError:
        pass


def test_root_not_object_fails_every_category():
    valid, errors = validate_all_categories(["not", "an", "object"], CANDIDATES)
    assert valid == {}
    assert set(errors.keys()) == {"AI", "ECONOMY", "SOCIETY"}


# ---- partial-category validation isolation ----------------------------------


def test_partial_category_validation_isolation():
    parsed_output = {
        "AI": [{"id": 999, "reason": "hallucinated"}],  # invalid
        "ECONOMY": [{"id": 10, "reason": "중요한 소식입니다"}],
        "SOCIETY": [{"id": 20, "reason": "중요한 소식입니다"}],
    }
    valid, errors = validate_all_categories(parsed_output, CANDIDATES)
    assert "AI" in errors
    assert "AI" not in valid
    assert valid["ECONOMY"] == [{"id": 10, "reason": "중요한 소식입니다"}]
    assert valid["SOCIETY"] == [{"id": 20, "reason": "중요한 소식입니다"}]


def test_missing_category_key_is_an_error_for_that_category_only():
    parsed_output = {"AI": [], "ECONOMY": [{"id": 10, "reason": "중요한 소식입니다"}]}  # SOCIETY missing
    valid, errors = validate_all_categories(parsed_output, CANDIDATES)
    assert "SOCIETY" in errors
    assert valid["AI"] == []
    assert valid["ECONOMY"] == [{"id": 10, "reason": "중요한 소식입니다"}]


def test_empty_selection_list_is_valid():
    valid, errors = validate_all_categories({"AI": [], "ECONOMY": [], "SOCIETY": []}, CANDIDATES)
    assert errors == {}
    assert valid == {"AI": [], "ECONOMY": [], "SOCIETY": []}


# ---- validate_producer_insights: evidence-ref grounding ---------------------

VALID_REFS = {"E1", "E2", "E3"}


def _insight(**overrides):
    base = {
        "what_is_moving": "트랙 X의 순위가 상승하고 있다",
        "why_it_matters": "이 흐름이 근거로 뒷받침된다",
        "what_to_watch": "다음 관측에서도 상승세가 이어지는지 지켜본다",
        "what_could_i_make_now": "훅 중심 인트로를 다음 데모에서 실험해볼 수 있다",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }
    base.update(overrides)
    return base


def test_valid_insight_passes():
    result = validate_producer_insights({"insights": [_insight()]}, VALID_REFS)
    assert result == [_insight()]


def test_hallucinated_evidence_ref_rejected():
    try:
        validate_producer_insights({"insights": [_insight(evidence_refs=["E99"])]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError as exc:
        assert "E99" in exc.reason


def test_empty_evidence_refs_rejected():
    try:
        validate_producer_insights({"insights": [_insight(evidence_refs=[])]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_empty_what_is_moving_rejected():
    try:
        validate_producer_insights({"insights": [_insight(what_is_moving="")]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_empty_why_it_matters_rejected():
    try:
        validate_producer_insights({"insights": [_insight(why_it_matters="  ")]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_empty_what_to_watch_rejected():
    try:
        validate_producer_insights({"insights": [_insight(what_to_watch="")]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_empty_what_could_i_make_now_rejected():
    try:
        validate_producer_insights({"insights": [_insight(what_could_i_make_now="  ")]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_invalid_confidence_rejected():
    try:
        validate_producer_insights({"insights": [_insight(confidence="EXTREME")]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_over_max_producer_insights_truncated_not_rejected():
    # A real model occasionally overshoots MAX_PRODUCER_INSIGHTS despite the
    # prompt's explicit cap -- truncating to the limit (keeping the
    # strongest-first ordering the prompt asks for) still enforces the hard
    # cap without discarding an otherwise-valid day's intelligence.
    insights = [_insight() for _ in range(MAX_PRODUCER_INSIGHTS + 3)]
    result = validate_producer_insights({"insights": insights}, VALID_REFS)
    assert len(result) == MAX_PRODUCER_INSIGHTS


def test_exactly_max_producer_insights_allowed():
    insights = [_insight() for _ in range(MAX_PRODUCER_INSIGHTS)]
    result = validate_producer_insights({"insights": insights}, VALID_REFS)
    assert len(result) == MAX_PRODUCER_INSIGHTS


def test_empty_insights_list_is_valid():
    assert validate_producer_insights({"insights": []}, VALID_REFS) == []


def test_missing_insights_key_rejected():
    try:
        validate_producer_insights({}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_root_not_object_rejected():
    try:
        validate_producer_insights(["not", "an", "object"], VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_missing_required_field_rejected():
    malformed = {"what_is_moving": "x", "why_it_matters": "y", "what_to_watch": "z",
                 "what_could_i_make_now": "w", "confidence": "LOW"}  # missing evidence_refs
    try:
        validate_producer_insights({"insights": [malformed]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


# ---- validate_music_trend_signals: evidence-ref grounding, 4 independent lists --


def _trend_item(**overrides):
    base = {
        "observed": "기사에서 해당 장르가 명시적으로 언급되었다",
        "interpretation": "실제 청취자 관심을 반영하는 것으로 보인다",
        "evidence_refs": ["E1"], "confidence": "MEDIUM",
    }
    base.update(overrides)
    return base


def _all_empty_lists():
    return {"genre_signals": [], "production_notes": [], "producer_references": [], "kpop_ar_notes": []}


def test_all_empty_lists_is_valid():
    result = validate_music_trend_signals(_all_empty_lists(), VALID_REFS)
    assert result == _all_empty_lists()


def test_valid_genre_signal_passes():
    payload = _all_empty_lists()
    payload["genre_signals"] = [_trend_item()]
    result = validate_music_trend_signals(payload, VALID_REFS)
    assert result["genre_signals"] == [_trend_item()]


def test_hallucinated_evidence_ref_rejected_in_any_list():
    for field in ("genre_signals", "production_notes", "producer_references", "kpop_ar_notes"):
        payload = _all_empty_lists()
        payload[field] = [_trend_item(evidence_refs=["E99"])]
        try:
            validate_music_trend_signals(payload, VALID_REFS)
            assert False, f"expected MusicTrendValidationError for {field}"
        except MusicTrendValidationError as exc:
            assert "E99" in exc.reason


def test_empty_evidence_refs_rejected():
    payload = _all_empty_lists()
    payload["production_notes"] = [_trend_item(evidence_refs=[])]
    try:
        validate_music_trend_signals(payload, VALID_REFS)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError:
        pass


def test_empty_observed_rejected():
    payload = _all_empty_lists()
    payload["producer_references"] = [_trend_item(observed="  ")]
    try:
        validate_music_trend_signals(payload, VALID_REFS)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError:
        pass


def test_empty_interpretation_rejected():
    payload = _all_empty_lists()
    payload["kpop_ar_notes"] = [_trend_item(interpretation="")]
    try:
        validate_music_trend_signals(payload, VALID_REFS)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError:
        pass


def test_invalid_confidence_rejected():
    payload = _all_empty_lists()
    payload["genre_signals"] = [_trend_item(confidence="EXTREME")]
    try:
        validate_music_trend_signals(payload, VALID_REFS)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError:
        pass


def test_over_max_items_per_list_truncated_not_rejected():
    # Same truncate-rather-than-reject discipline as
    # test_over_max_producer_insights_truncated_not_rejected above.
    payload = _all_empty_lists()
    payload["genre_signals"] = [_trend_item() for _ in range(MAX_MUSIC_TREND_ITEMS_PER_LIST + 3)]
    result = validate_music_trend_signals(payload, VALID_REFS)
    assert len(result["genre_signals"]) == MAX_MUSIC_TREND_ITEMS_PER_LIST


def test_one_bad_list_never_invalidates_a_well_grounded_item_check_is_atomic():
    """The whole payload is validated as one unit (matching validate_
    producer_insights's own all-or-nothing contract) -- a bad item in one
    list still rejects the whole call, since report.music_trend_
    orchestrator persists all 4 lists together or not at all."""
    payload = _all_empty_lists()
    payload["genre_signals"] = [_trend_item()]
    payload["production_notes"] = [_trend_item(evidence_refs=["E99"])]
    try:
        validate_music_trend_signals(payload, VALID_REFS)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError:
        pass


def test_missing_list_key_rejected():
    payload = _all_empty_lists()
    del payload["kpop_ar_notes"]
    try:
        validate_music_trend_signals(payload, VALID_REFS)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError:
        pass


def test_music_trend_root_not_object_rejected():
    try:
        validate_music_trend_signals(["not", "an", "object"], VALID_REFS)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError:
        pass


# ---- NATIVE KOREAN TEXT QUALITY + FACT GROUNDING (quality-hardening phase) --
# All three validators' new checks are OFF by default (title_by_id/
# evidence_by_ref=None, exercised by every test above) so they never affect
# a caller that doesn't opt in; these tests exercise the opt-in path
# directly against real Korean synthesis text.


def test_reason_malformed_gibberish_rejected_when_title_by_id_given():
    selections = [{"id": 1, "reason": "I appreciate you setting up the task, but I cannot help."}]
    try:
        validate_category_selection("AI", selections, CANDIDATE_IDS["AI"], title_by_id={1: "실적 발표"})
        assert False, "expected CategoryValidationError"
    except CategoryValidationError as exc:
        assert "malformed" in exc.reason


def test_reason_real_korean_passes_when_title_by_id_given():
    selections = [{"id": 1, "reason": "실적 발표가 시장에 미칠 영향이 크다"}]
    result = validate_category_selection("AI", selections, CANDIDATE_IDS["AI"], title_by_id={1: "실적 발표"})
    assert result == selections


def test_reason_unsupported_fact_token_rejected():
    # The candidate's own title has no "2030" anywhere -- an invented year
    # is exactly the "wrong dates" fail condition this gate exists to catch.
    selections = [{"id": 1, "reason": "2030년까지 이어질 전망이다"}]
    try:
        validate_category_selection("AI", selections, CANDIDATE_IDS["AI"], title_by_id={1: "실적 발표"})
        assert False, "expected CategoryValidationError"
    except CategoryValidationError as exc:
        assert "unsupported" in exc.reason


def test_reason_grounded_fact_token_passes():
    selections = [{"id": 1, "reason": "2026년 실적이 핵심 변수다"}]
    result = validate_category_selection(
        "AI", selections, CANDIDATE_IDS["AI"], title_by_id={1: "2026년 실적 발표"}
    )
    assert result == selections


EVIDENCE_BY_REF = {"E1": "Artist - Title reached #3 on the chart."}


def test_producer_insight_malformed_text_rejected_with_evidence():
    insight = _insight(what_is_moving="I'm sorry, but I cannot summarize this.")
    try:
        validate_producer_insights({"insights": [insight]}, VALID_REFS, EVIDENCE_BY_REF)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError as exc:
        assert "malformed" in exc.reason


def test_producer_insight_unsupported_currency_rejected_with_evidence():
    insight = _insight(why_it_matters="이 거래 규모는 42억 달러에 달한다")
    try:
        validate_producer_insights({"insights": [insight]}, VALID_REFS, EVIDENCE_BY_REF)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError as exc:
        assert "unsupported" in exc.reason


def test_producer_insight_real_korean_no_facts_passes_with_evidence():
    insight = _insight(
        what_is_moving="해당 트랙의 순위가 상승하고 있다",
        why_it_matters="이 흐름이 중요한 이유다",
        what_to_watch="다음 관측에서 이어지는지 지켜본다",
        what_could_i_make_now="비슷한 훅을 실험해볼 수 있다",
    )
    result = validate_producer_insights({"insights": [insight]}, VALID_REFS, EVIDENCE_BY_REF)
    assert result == [insight]


def test_producer_insight_grounded_currency_passes_with_evidence():
    evidence = {"E1": "The deal is valued at $420 million."}
    insight = _insight(why_it_matters="이 거래 규모는 4억 2천만 달러에 달한다")
    result = validate_producer_insights({"insights": [insight]}, VALID_REFS, evidence)
    assert result == [insight]


def test_music_trend_item_malformed_text_rejected_with_evidence():
    payload = _all_empty_lists()
    payload["genre_signals"] = [_trend_item(observed="I cannot provide that information.")]
    try:
        validate_music_trend_signals(payload, VALID_REFS, EVIDENCE_BY_REF)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError as exc:
        assert "malformed" in exc.reason


def test_music_trend_item_unsupported_year_rejected_with_evidence():
    payload = _all_empty_lists()
    payload["genre_signals"] = [_trend_item(interpretation="2030년 트렌드를 예고한다")]
    try:
        validate_music_trend_signals(payload, VALID_REFS, EVIDENCE_BY_REF)
        assert False, "expected MusicTrendValidationError"
    except MusicTrendValidationError as exc:
        assert "unsupported" in exc.reason


def test_music_trend_item_grounded_text_passes_with_evidence():
    payload = _all_empty_lists()
    payload["genre_signals"] = [_trend_item(
        observed="해당 장르가 기사에서 명시적으로 언급되었다",
        interpretation="실제 청취자 관심을 반영하는 것으로 보인다",
    )]
    result = validate_music_trend_signals(payload, VALID_REFS, EVIDENCE_BY_REF)
    assert result["genre_signals"][0]["observed"] == "해당 장르가 기사에서 명시적으로 언급되었다"
