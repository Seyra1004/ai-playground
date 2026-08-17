"""report.validation: ID-grounding, non-empty reason, max-5 enforcement,
partial-category isolation, malformed-output handling."""

from report.validation import (
    MAX_MUSIC_TREND_ITEMS_PER_LIST,
    MAX_PRODUCER_INSIGHTS,
    MAX_SELECTIONS_PER_CATEGORY,
    CategoryValidationError,
    MusicTrendValidationError,
    ProducerValidationError,
    is_incomplete_summary,
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


# ---- FINAL 90+ QUALITY CORRECTION PASS: snippet_by_id fixes a real
# false-positive currency-magnitude rejection (title-only evidence text
# missed a currency figure that WAS present in the item's own real
# snippet) --------------------------------------------------------------


def test_currency_fact_false_positive_without_snippet_evidence():
    """Reproduces the exact real defect: a title without the currency-unit
    word ("2000조", no "원") fails to support a reason that correctly says
    "2000조원", even though the fact is real and present in the item's own
    snippet -- BEFORE this pass's fix, evidence text was title-only."""
    candidates = {"ECONOMY": [{"id": 10, "normalized_title": "선 넘은 가계빚, 사상첫 2000조"}]}
    parsed_output = {"ECONOMY": [{
        "id": 10, "reason": "가계부채가 사상 처음 2000조원을 돌파한 것은 한국 경제의 구조적 위험을 보여주는 중대 지표다.",
    }]}
    valid, errors = validate_all_categories(parsed_output, candidates)
    assert "ECONOMY" in errors  # false-positive reproduced with no snippet evidence


def test_currency_fact_passes_with_snippet_evidence():
    candidates = {"ECONOMY": [{"id": 10, "normalized_title": "선 넘은 가계빚, 사상첫 2000조"}]}
    parsed_output = {"ECONOMY": [{
        "id": 10, "reason": "가계부채가 사상 처음 2000조원을 돌파한 것은 한국 경제의 구조적 위험을 보여주는 중대 지표다.",
    }]}
    snippet_by_id = {10: "우리나라 가계 빚이 사상 처음으로 2000조원대에 진입한 것이 확실시된다."}
    valid, errors = validate_all_categories(parsed_output, candidates, snippet_by_id=snippet_by_id)
    assert errors == {}
    assert valid["ECONOMY"] == parsed_output["ECONOMY"]


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


# ---- FINAL 90+ QUALITY CORRECTION PASS: reject editorial-content-creation
# advice masquerading as producer/A&R action -----------------------------


def test_newsletter_advice_rejected_not_real_producer_action():
    insight = _insight(what_could_i_make_now="이번 주 차트 무브를 요약한 짧은 뉴스레터 섹션을 바로 만들 수 있다")
    try:
        validate_producer_insights({"insights": [insight]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError as exc:
        assert "editorial content" in exc.reason.lower() or "music-making" in exc.reason.lower()


def test_article_explainer_advice_rejected():
    insight = _insight(what_could_i_make_now="이 이슈를 타임라인 형태로 정리한 짧은 explainer 기사를 작성할 수 있다")
    try:
        validate_producer_insights({"insights": [insight]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError:
        pass


def test_analysis_memo_advice_rejected():
    """Confirmed real leak (2026-08-17): a cached what_could_i_make_now
    recommending a short analysis memo about the news is editorial-content
    creation, not a real music-making/A&R action -- same rejection as
    newsletter/article/explainer advice."""
    insight = _insight(what_could_i_make_now="TikTok의 음악 산업 내 역할 축소가 마케팅에 미치는 영향을 짚는 짧은 분석 메모를 작성할 수 있다")
    try:
        validate_producer_insights({"insights": [insight]}, VALID_REFS)
        assert False, "expected ProducerValidationError"
    except ProducerValidationError as exc:
        assert "editorial content" in exc.reason.lower() or "music-making" in exc.reason.lower()


def test_real_music_making_advice_still_passes():
    insight = _insight(what_could_i_make_now="훅 중심의 신스팝 인트로를 다음 데모 세션에서 시도해볼 수 있다")
    result = validate_producer_insights({"insights": [insight]}, VALID_REFS)
    assert len(result) == 1


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


# ---- is_incomplete_summary (content-quality hardening pass, 2026-08-17) ----


def test_incomplete_summary_flagged_when_snippet_has_death_and_summary_omits_it():
    snippet = "The woman claimed her stepfather used AI tools; he died by suicide two days later."
    generated = "한 여성이 새아버지가 AI 이미지 생성 도구를 이용해 사진을 변조했다고 주장했다."
    assert is_incomplete_summary(snippet, generated) is True


def test_not_incomplete_when_generated_text_mentions_the_same_fact():
    snippet = "The woman claimed her stepfather used AI tools; he died by suicide two days later."
    generated = "새아버지는 적발 이틀 후 사망했다(died by suicide)고 보도됐다."
    assert is_incomplete_summary(snippet, generated) is False


def test_not_incomplete_when_snippet_has_no_high_severity_fact():
    """An ordinary summary shorter than its source is never flagged --
    only a real high-severity fact PRESENT in the available snippet and
    absent from the generated text triggers this."""
    snippet = "Anthropic released more details about how its new watermark works."
    generated = "Anthropic이 워터마크 기술에 대한 세부 정보를 공개했다."
    assert is_incomplete_summary(snippet, generated) is False


def test_incomplete_summary_never_flags_when_snippet_or_generated_is_empty():
    assert is_incomplete_summary("", "무언가") is False
    assert is_incomplete_summary("something died", "") is False
    assert is_incomplete_summary(None, None) is False


def test_incomplete_summary_scoped_to_available_snippet_not_full_article():
    """Documents the honest scope limitation: a fact that exists ONLY in
    the full original article (never ingested into the snippet this
    pipeline actually has) cannot be detected -- this is an ingestion-
    scope limitation, not something is_incomplete_summary can catch."""
    snippet_without_the_fact = "The woman claimed her stepfather used AI tools to alter photos."
    generated = "한 여성이 새아버지가 AI 도구를 이용해 사진을 변조했다고 주장했다."
    # The real full article mentions a death, but the ingested snippet
    # (the only text available to this check) never did -- correctly not
    # flagged, since there is nothing here to have caught it from.
    assert is_incomplete_summary(snippet_without_the_fact, generated) is False
