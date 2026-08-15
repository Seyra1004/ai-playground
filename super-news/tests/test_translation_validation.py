"""report.translation_validation: deterministic fact-preservation checks,
built from real observed production defects (CONTENT INTEGRITY
FINALIZATION phase, 2026-08-15 -- see SUPER_NEWS_HANDOFF.md).

Covers the task's own required matrix (A-F) plus the two real defects
that motivated this module: a $190B valuation mistranslated as "190억
달러" (a tenfold Korean 억-unit error), and a malformed 1-character
source snippet ("(") that produced a raw LLM refusal message which was
then cached and displayed as if it were a real translation."""

from report.translation_validation import is_plausibly_korean_output, validate_translation_facts


# ---- Required matrix A-F ------------------------------------------------


def test_A_equivalent_currency_magnitude_passes():
    result = validate_translation_facts(
        "The deal closed at a $190B valuation.",
        "이번 거래는 1,900억 달러 가치 평가로 마무리됐다.",
    )
    assert result.ok


def test_B_190B_to_190eok_fails():
    """The real production defect this module exists to catch: a tenfold
    Korean 억-unit conversion error ($190B -> 190억 = $19B)."""
    result = validate_translation_facts(
        "The deal closed at a $190B valuation.",
        "이번 거래는 190억 달러 가치 평가로 마무리됐다.",
    )
    assert not result.ok
    assert any("currency" in r for r in result.reasons)


def test_C_5_percent_to_50_percent_fails():
    result = validate_translation_facts("Prices rose 5% this quarter.", "이번 분기 가격이 50% 상승했다.")
    assert not result.ok
    assert any("percentage" in r for r in result.reasons)


def test_D_2026_to_2025_fails():
    result = validate_translation_facts("The summit is scheduled for 2026.", "정상회담은 2025년으로 예정되어 있다.")
    assert not result.ok
    assert any("year" in r for r in result.reasons)


def test_E_GPT56_to_GPT55_fails():
    result = validate_translation_facts("GPT-5.6 was released this week.", "이번 주 GPT-5.5가 출시되었다.")
    assert not result.ok
    assert any("version" in r for r in result.reasons)


def test_F_legitimate_reformatting_without_meaning_change_passes():
    result = validate_translation_facts(
        "Apple reported $4.2B in revenue this quarter.",
        "애플은 이번 분기 42억 달러의 매출을 기록했다고 밝혔다.",
    )
    assert result.ok


# ---- Real defect: LLM refusal cached as a "translation" -----------------


def test_llm_refusal_response_is_rejected():
    """Real production defect: a malformed 1-character source snippet
    ("(") sent to the real translation provider produced a conversational
    refusal, which was cached and served to real users as the article's
    translated snippet."""
    result = validate_translation_facts(
        "(",
        'I appreciate you setting up the translation task, but I notice the text to '
        'translate is just an opening parenthesis "(". Please provide the actual text.',
    )
    assert not result.ok
    assert not is_plausibly_korean_output(
        'I appreciate you setting up the translation task, but I notice the text to '
        'translate is just an opening parenthesis "(".'
    )


def test_empty_translated_output_is_rejected():
    assert not is_plausibly_korean_output("")
    assert not is_plausibly_korean_output("   ")


# ---- Korean grammar attaches particles directly to numbers/versions -----
# (regression guard: an earlier draft of this module used a bare `\b`
# after years/versions, which never matches when a Korean particle is
# attached with no space -- "2026년", "GPT-5.6이다" -- since Python's `\b`
# is Unicode-aware and Hangul counts as a word character.)


def test_year_with_directly_attached_korean_particle_is_recognized():
    result = validate_translation_facts("The event happens in 2026.", "해당 행사는 2026년에 열린다.")
    assert result.ok


def test_version_with_directly_attached_korean_particle_is_recognized():
    result = validate_translation_facts("GPT-5.6 is the newest model.", "최신 모델은 GPT-5.6이다.")
    assert result.ok


# ---- No false positives on ordinary text with no protected facts --------


def test_text_with_no_numbers_or_versions_always_passes():
    result = validate_translation_facts(
        "OpenAI launches new model.",
        "OpenAI가 새로운 모델을 출시했다.",
    )
    assert result.ok


# ---- Multiple currency figures in one text (real Databricks example) ----


def test_real_databricks_example_with_multiple_figures_passes_when_corrected():
    result = validate_translation_facts(
        "Databricks wanted to raise $1B, investors wanted $15B. "
        "It settled on $5B at a $190B valuation.",
        "Databricks는 10억 달러를 조성하려고 했고, 투자자들은 150억 달러를 원했다. "
        "결국 1,900억 달러의 기업 가치 평가에서 50억 달러로 합의했다.",
    )
    assert result.ok


def test_real_databricks_example_fails_with_the_original_uncorrected_error():
    result = validate_translation_facts(
        "Databricks wanted to raise $1B, investors wanted $15B. "
        "It settled on $5B at a $190B valuation.",
        "Databricks는 10억 달러를 조성하려고 했고, 투자자들은 150억 달러를 원했다. "
        "결국 190억 달러의 기업 가치 평가에서 50억 달러로 합의했다.",
    )
    assert not result.ok


# ---- Compound Korean magnitude support (2026-08-15, second-pass fix) --------
# Real defect: the previous single-segment _KO_CURRENCY_RE only ever
# captured the LAST unit in a compound expression like "6억 6,800만" --
# "6,800만" -- and silently dropped the "6억" prefix, so a REAL,
# CORRECT cache_id 250 fix ("6억 6,800만 달러" = $668,000,000) was
# incorrectly rejected. Real Korean amounts routinely chain units
# (조/억/천만/만/천) together, each a term to be summed, not just the
# final one. Required matrix A-F from the task itself.


def test_compound_A_668million_equals_6eok_6800man_passes():
    result = validate_translation_facts(
        "The court ordered a payment of $668 million in property division.",
        "법원은 재산분할로 6억 6,800만 달러 지급을 명령했다.",
    )
    assert result.ok


def test_compound_B_668million_vs_bare_668man_fails():
    """The real cache_id 250 defect before correction: 668만 = $6.68
    million, a 100x understatement of the real $668 million figure."""
    result = validate_translation_facts(
        "The court ordered a payment of $668 million in property division.",
        "법원은 재산분할로 668만 달러 지급을 명령했다.",
    )
    assert not result.ok
    assert any("currency" in r for r in result.reasons)


def test_compound_C_350million_equals_3eok_5cheonman_passes():
    """'천만' (thousand-times-ten-thousand = 10,000,000) must be
    recognized as its own compound unit, not misparsed as a bare '천'
    (1,000) that strands a following '만' unmatched."""
    result = validate_translation_facts(
        "The company raised $350 million in its latest funding round.",
        "이 회사는 최근 투자 라운드에서 3억 5천만 달러를 조달했다.",
    )
    assert result.ok


def test_compound_D_1point2trillion_equals_1jo_2000eok_passes():
    result = validate_translation_facts(
        "The fund now manages $1.2 trillion in assets.",
        "이 펀드는 현재 1조 2,000억 달러의 자산을 운용하고 있다.",
    )
    assert result.ok


def test_compound_E_wrong_compound_magnitude_fails():
    """Same defect class as B, expressed as a dropped 억-term rather than
    a bare single-unit figure: '6,680만' (=$66.8M) is still off by 10x
    from the real $668 million."""
    result = validate_translation_facts(
        "The court ordered a payment of $668 million in property division.",
        "법원은 재산분할로 6,680만 달러 지급을 명령했다.",
    )
    assert not result.ok
    assert any("currency" in r for r in result.reasons)


def test_compound_F_existing_year_percent_version_checks_still_pass():
    """Regression guard: the compound-currency rewrite must not disturb
    the other, unrelated fact classes."""
    assert validate_translation_facts("The event happens in 2026.", "해당 행사는 2026년에 열린다.").ok
    assert not validate_translation_facts("The event happens in 2026.", "해당 행사는 2025년에 열린다.").ok
    assert validate_translation_facts("Prices rose 5%", "가격이 5% 올랐다").ok
    assert not validate_translation_facts("Prices rose 5%", "가격이 50% 올랐다").ok
    assert validate_translation_facts("GPT-5.6 is the newest model.", "최신 모델은 GPT-5.6이다.").ok
    assert not validate_translation_facts("GPT-5.6 is the newest model.", "최신 모델은 GPT-5.5이다.").ok


def test_compound_single_segment_still_works_unchanged():
    """A plain, non-compound Korean magnitude ('190억', no chained
    prefix) must still be extracted correctly -- the rewrite must not
    have narrowed support down to compound-only expressions."""
    result = validate_translation_facts(
        "The deal closed at a $190B valuation.",
        "이번 거래는 190억 달러 가치 평가로 마무리됐다.",
    )
    assert not result.ok  # $190B != 190억 ($19B) -- same real defect, single segment this time


def test_compound_real_cache_id_250_corrected_text_passes():
    """The exact real production text from cache_id 250, post-correction
    (SUPER_NEWS_HANDOFF.md) -- this is the specific case that surfaced
    the compound-magnitude gap in the first place."""
    result = validate_translation_facts(
        "SK Group Chairman Chey Tae-won has appealed the latest court ruling "
        "ordering him to pay 944 billion won ($668 million) in property "
        "division to his former wife, Roh Soh-yeong, sources said Saturday.",
        "SK그룹 회장 최태원이 전처 노소영과의 재산분할금 944억 원(6억 6,800만 달러) "
        "지급을 명령한 최근 법원 판결에 항소했다고 소식통이 토요일 밝혔다.",
    )
    assert result.ok, result.reasons
