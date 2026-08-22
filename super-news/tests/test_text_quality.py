"""report.text_quality: shared Korean-plausibility / malformed-output
detection and evidence-grounding token checks, reused across translated
text (report.translation_validation) and LLM-native synthesis text
(report.validation, report.news_intelligence_synthesis)."""

from report.text_quality import (
    _ko_currency_values,
    has_internal_id_leak,
    has_refusal_marker,
    is_malformed_synthesis_text,
    is_plausibly_korean_output,
    unsupported_fact_tokens,
)


def test_is_plausibly_korean_output_real_korean():
    assert is_plausibly_korean_output("오늘 발표된 실적은 예상을 상회했다.")


def test_is_plausibly_korean_output_empty_is_false():
    assert not is_plausibly_korean_output("")
    assert not is_plausibly_korean_output("   ")


def test_is_plausibly_korean_output_english_only_is_false():
    assert not is_plausibly_korean_output("I appreciate you setting up the task, but I cannot help with that.")


def test_has_refusal_marker_true_for_known_phrase():
    assert has_refusal_marker("I appreciate you setting up the translation task, but I cannot proceed.")


def test_has_refusal_marker_false_for_real_korean():
    assert not has_refusal_marker("삼성전자가 신제품을 공개했다.")


def test_has_refusal_marker_true_for_meta_response_with_embedded_korean():
    """PRODUCTION INCIDENT FIX (2026-08-22, confirmed real defect): a
    meta-response/task-commentary result can embed the real Korean source
    verbatim, so is_plausibly_korean_output's Hangul floor alone is not
    enough -- the refusal-marker check must still catch this class."""
    text = (
        "No headline text was provided to translate — if you intended to "
        "provide an English headline, please provide it. Here is the "
        "translation of the Korean text unchanged: 애플뮤직에서는 순위가 상승했다."
    )
    assert has_refusal_marker(text)


def test_is_malformed_synthesis_text_rejects_empty():
    assert is_malformed_synthesis_text("")
    assert is_malformed_synthesis_text("   ")


def test_is_malformed_synthesis_text_rejects_refusal_even_with_some_hangul():
    # A refusal that happens to quote a Korean term must still be rejected.
    assert is_malformed_synthesis_text("I'm sorry, but I cannot summarize this 뉴스 item.")


def test_is_malformed_synthesis_text_accepts_real_korean_synthesis():
    assert not is_malformed_synthesis_text(
        "이번 발표는 업계 전반의 투자 심리에 영향을 줄 것으로 보인다."
    )


def test_has_internal_id_leak_true_for_bare_ref():
    assert has_internal_id_leak("E11은 Tinashe의 신곡과 관련된 근거다.")


def test_has_internal_id_leak_true_for_multiple_refs():
    assert has_internal_id_leak("근거 E12/E15/E16을 종합하면 이렇게 해석된다.")


def test_has_internal_id_leak_false_for_real_korean_without_ref_label():
    assert not has_internal_id_leak("이번 발매는 스트리밍 지표에 뚜렷한 변화를 보였다.")


def test_has_internal_id_leak_false_for_empty():
    assert not has_internal_id_leak("")
    assert not has_internal_id_leak(None)


def test_is_malformed_synthesis_text_rejects_internal_id_leak_even_with_real_korean():
    assert is_malformed_synthesis_text("E11은 Tinashe의 신곡 확산과 관련된 근거다.")


def test_unsupported_fact_tokens_year_grounded():
    text = "2026년 실적 발표가 있었다."
    evidence = "Company reports 2026 results."
    assert unsupported_fact_tokens(text, evidence) == []


def test_unsupported_fact_tokens_year_not_grounded():
    text = "2030년 목표를 제시했다."
    evidence = "The company outlined its 2026 roadmap."
    reasons = unsupported_fact_tokens(text, evidence)
    assert any("year" in r for r in reasons)


def test_unsupported_fact_tokens_percentage_not_grounded():
    text = "매출이 90% 증가했다."
    evidence = "Revenue grew significantly this quarter."
    reasons = unsupported_fact_tokens(text, evidence)
    assert any("percentage" in r for r in reasons)


def test_unsupported_fact_tokens_percentage_grounded():
    text = "매출이 12% 증가했다."
    evidence = "Revenue grew 12% year over year."
    assert unsupported_fact_tokens(text, evidence) == []


def test_unsupported_fact_tokens_currency_grounded_across_languages():
    text = "이번 계약 규모는 4억 2천만 달러다."
    evidence = "The deal is valued at $420 million."
    assert unsupported_fact_tokens(text, evidence) == []


def test_unsupported_fact_tokens_currency_mutated_is_unsupported():
    text = "이번 계약 규모는 42억 달러다."
    evidence = "The deal is valued at $420 million."
    reasons = unsupported_fact_tokens(text, evidence)
    assert any("currency" in r for r in reasons)


def test_unsupported_fact_tokens_no_checkable_tokens_is_never_a_defect():
    assert unsupported_fact_tokens("이 소식은 업계에서 주목받고 있다.", "Industry watchers noted the news.") == []


def test_unsupported_fact_tokens_bare_korean_currency_grounded():
    text = "가계빚이 사상 첫 2000조원을 넘어섰다."
    evidence = "영끌·빚투에 가계빚 사상 첫 2000조…기준금리 인상땐 치명타"
    assert unsupported_fact_tokens(text, evidence) == []


def test_ko_currency_values_ignores_article_number_reference():
    assert _ko_currency_values("제3조에 따르면 계약은 무효다.") == []


def test_ko_currency_values_never_crashes_on_punctuation_only_number():
    """EMERGENCY QUALITY RECOVERY PASS (2026-08-17, confirmed real defect):
    a comma-only run with no actual digit (e.g. a stray ",조" from unusual
    real text) satisfies [\\d,]+ but crashes float("") -- this must be
    skipped, never raise, exactly like _en_currency_values already does
    for its own equivalent case."""
    assert _ko_currency_values(",조 이야기입니다") == []
    assert _ko_currency_values("정상 문장에 ,,,억이 섞여 있어도 죽지 않아야 한다") == []


def test_unsupported_fact_tokens_empty_evidence_flags_any_real_token():
    reasons = unsupported_fact_tokens("2026년 발표", "")
    assert any("year" in r for r in reasons)
