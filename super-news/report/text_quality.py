"""Shared deterministic Korean-text plausibility / malformed-output
detection and factual-token extraction, extracted from
report/translation_validation.py (CONTENT INTEGRITY FINALIZATION phase)
so the same real, confirmed defect classes -- a raw LLM refusal/meta-
response or an empty/non-Korean result cached and displayed as if it were
real content, and a numeric/date/version/currency fact silently mutated --
can be reused for LLM-NATIVE-generated Korean synthesis text (News/
Producer/Music Trend Intelligence, and the news-selection `reason` field),
not only for translated text.

Deliberately narrow and deterministic, matching report/translation_
validation.py's own stated scope: this does NOT attempt semantic/policy
fact-checking. It only catches what is mechanically checkable without a
language model: is the text plausible non-gibberish Korean, and does every
explicit YEAR/PERCENTAGE/VERSION/CURRENCY-MAGNITUDE token in one piece of
text have an equivalent-magnitude counterpart somewhere in a given
evidence text.
"""

import re

# ---- Factual-token extraction (moved from translation_validation.py
# unchanged -- same regexes, same semantics; see that module's original
# docstrings, preserved below, for the exact defects each guards against). ----

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}(?!\d)")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_VERSION_RE = re.compile(r"\b([A-Z][A-Za-z]*)[\s-](\d+(?:\.\d+)+)(?!\.?\d)")

_EN_CURRENCY_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|thousand|T|B|M|K)?\b", re.IGNORECASE
)
_EN_MAGNITUDE = {
    "t": 1e12, "trillion": 1e12,
    "b": 1e9, "billion": 1e9,
    "m": 1e6, "million": 1e6,
    "k": 1e3, "thousand": 1e3,
}

_KO_MAGNITUDE = {"조": 1e12, "억": 1e8, "천만": 1e7, "만": 1e4, "천": 1e3}
_KO_CURRENCY_WORDS = "달러|원|위안|엔|파운드|유로"
_KO_UNIT_PATTERN = "|".join(re.escape(k) for k in _KO_MAGNITUDE)
_KO_SEGMENT_RE = re.compile(rf"([\d,]+(?:\.\d+)?)\s*({_KO_UNIT_PATTERN})")
_KO_COMPOUND_CURRENCY_RE = re.compile(rf"(?:{_KO_SEGMENT_RE.pattern}\s*)+(?:{_KO_CURRENCY_WORDS})")

# CONTEXTUAL BARE-UNIT CURRENCY FALLBACK (2026-08-17, confirmed real
# false-positive: a real ECONOMY headline "가계빚 사상 첫 2000조…" omits
# the trailing currency word entirely, as Korean financial headlines
# routinely do when the amount is obviously KRW from context -- the
# compound regex above requires that word, so it never counted this real
# figure as evidence). Restricted to ONLY 조/억 (never 천만/만/천, which
# are commonly non-currency counts -- a population, a date, a plain
# quantity) since those two units are used almost exclusively for money in
# Korean economic/financial reporting. Excludes any match immediately
# preceded by "제" -- Article/clause numbering ("제3조" = "Article 3") is
# never a currency amount, and this is the one common bare "숫자+조/억"
# pattern that genuinely isn't money.
_KO_BARE_LARGE_UNIT_PATTERN = "|".join(re.escape(k) for k in ("조", "억"))
_KO_BARE_CURRENCY_RE = re.compile(rf"(?<!제)([\d,]+(?:\.\d+)?)\s*({_KO_BARE_LARGE_UNIT_PATTERN})")

_MAGNITUDE_RELATIVE_TOLERANCE = 0.02

_HANGUL_RE = re.compile(r"[가-힣]")
_MIN_HANGUL_CHARS_FOR_PLAUSIBLE_OUTPUT = 2

# Known LLM refusal/meta-response phrase fragments -- a Korean synthesis
# field that leaks a raw English refusal/meta-commentary sentence instead
# of real Korean content is the malformed-output class this catches,
# distinct from (but overlapping) the Hangul-floor check below: a refusal
# can legitimately contain a few Hangul characters (e.g. quoting a Korean
# term) while still being, in substance, not real synthesis content.
_REFUSAL_MARKERS = (
    "i appreciate", "i cannot", "i can't", "i'm sorry", "as an ai",
    "i apologize", "let me know if", "i'm not able to", "i am not able to",
)

# PROFESSIONAL EDITORIAL QUALITY PASS: report/music_trend_synthesis.py and
# report/producer_synthesis.py both hand the LLM an evidence catalog whose
# entries are internally labeled "E1", "E2", ... (see build_evidence_
# catalog) -- the model sometimes echoes those literal internal ref labels
# into its own `observed`/`interpretation`/`why_it_matters` prose (e.g.
# "E11은 Tinashe의 신곡..."), a real, confirmed defect: an internal
# citation identifier is never user-facing content, only `evidence_refs`
# (a separate, real, structured field the renderer resolves to a human-
# readable summary) is meant to carry it. Deliberately REJECTS rather than
# attempts to surgically rewrite the sentence around a removed token --
# matching this module's own "reject, never partially degrade" contract;
# a real day with too little clean evidence simply shows fewer items,
# never a silently mangled sentence.
_INTERNAL_ID_RE = re.compile(r"(?<![A-Za-z가-힣])E\d{1,3}(?:[/,]\s?E\d{1,3})*(?![A-Za-z0-9])")


def has_internal_id_leak(text):
    """True when `text` contains a literal internal evidence-ref label
    (e.g. "E11", "E12/E15/E16") -- these must never appear in user-facing
    prose."""
    if not text:
        return False
    return bool(_INTERNAL_ID_RE.search(text))


def _en_currency_values(text):
    """A missing magnitude suffix (a bare "$5" with no B/M/K) is real
    too -- it just contributes its literal numeric value."""
    values = []
    for match in _EN_CURRENCY_RE.finditer(text):
        try:
            number = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        suffix = match.group(2)
        multiplier = _EN_MAGNITUDE.get(suffix.lower(), 1.0) if suffix else 1.0
        values.append(number * multiplier)
    return values


def _ko_currency_values(text):
    """Sums every unit segment in a compound Korean magnitude expression
    ("6억 6,800만" -> 6억 + 6,800만), not just the last one. ALSO counts a
    bare "숫자+조/억" with no trailing currency word (see
    _KO_BARE_CURRENCY_RE's own docstring) -- never a bare 만/천, and never
    one immediately preceded by "제" (article/clause numbering).

    The bare-unit scan runs ONLY over text left after masking out every
    span the compound regex already matched -- otherwise "4억 2천만
    달러" (a single real compound value, 420000000) would ALSO bare-match
    its own inner "4억" segment as an unrelated second value (400000000),
    a real double-count defect that made an already-grounded currency
    figure look unsupported."""
    values = []
    remaining = list(text)
    for match in _KO_COMPOUND_CURRENCY_RE.finditer(text):
        total = 0.0
        for number_str, unit in _KO_SEGMENT_RE.findall(match.group(0)):
            total += float(number_str.replace(",", "")) * _KO_MAGNITUDE[unit]
        values.append(total)
        start, end = match.span()
        for i in range(start, end):
            remaining[i] = " "
    for number_str, unit in _KO_BARE_CURRENCY_RE.findall("".join(remaining)):
        values.append(float(number_str.replace(",", "")) * _KO_MAGNITUDE[unit])
    return values


def _extract_currency_values(text):
    return _en_currency_values(text) + _ko_currency_values(text)


def _values_roughly_match(a, b):
    if a == 0 or b == 0:
        return a == b
    return abs(a - b) / max(abs(a), abs(b)) <= _MAGNITUDE_RELATIVE_TOLERANCE


def _missing_values(reference_values, other_values):
    """Every value in `reference_values` must have SOME matching value in
    `other_values` -- order-independent, never assumed positional. Returns
    the subset of `reference_values` with no match (never mutates either
    input list)."""
    remaining = list(other_values)
    missing = []
    for value in reference_values:
        match_index = next((i for i, v in enumerate(remaining) if _values_roughly_match(value, v)), None)
        if match_index is None:
            missing.append(value)
        else:
            remaining.pop(match_index)
    return missing


def _extract_versions(text):
    return {(name.lower(), version) for name, version in _VERSION_RE.findall(text)}


def is_plausibly_korean_output(text):
    """A real Korean-audience output must contain at least a small amount
    of real Hangul -- catches the confirmed real defect where a raw LLM
    refusal/meta-response was cached and displayed as if it were real
    content. An empty/whitespace-only result is never plausible either."""
    if not text or not text.strip():
        return False
    return len(_HANGUL_RE.findall(text)) >= _MIN_HANGUL_CHARS_FOR_PLAUSIBLE_OUTPUT


def has_refusal_marker(text):
    """True when `text` contains a known raw LLM refusal/meta-response
    phrase fragment -- a real, confirmed defect class distinct from (but
    overlapping) the Hangul-plausibility check: a refusal can legitimately
    quote a few Hangul characters while still being, in substance, not
    real synthesis content."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def is_malformed_synthesis_text(text):
    """True (malformed -- reject or safely degrade, never persist/display
    as valid intelligence) when `text` is empty/whitespace, contains a
    known raw refusal/meta-response marker, leaks a literal internal
    evidence-ref label (see has_internal_id_leak), or is not plausibly
    Korean. Reused across every LLM-native Korean synthesis field (News/
    Producer/Music Trend Intelligence, news-selection `reason`) -- the
    same deterministic malformed-output class report/translation_
    validation.py already catches for translated text, applied here to
    text an LLM wrote directly rather than translated."""
    if not text or not text.strip():
        return True
    if has_refusal_marker(text):
        return True
    if has_internal_id_leak(text):
        return True
    return not is_plausibly_korean_output(text)


def unsupported_fact_tokens(text, evidence_text):
    """Returns a list of human-readable reasons for any YEAR/PERCENTAGE/
    VERSION/CURRENCY-MAGNITUDE token found in `text` with no
    equivalent-magnitude match anywhere in `evidence_text` -- i.e. a fact
    `text` asserts that its own cited evidence does not actually support.
    Empty list means every checkable fact token in `text` is grounded (or
    `text` contains no checkable fact token at all -- absence of a
    checkable token is never itself treated as a defect, only an actual
    unsupported one is). `evidence_text` may be an empty string (no real
    evidence text available) -- in that case any checkable token in `text`
    is, by construction, unsupported.

    Deliberately one-directional (text -> evidence), the mirror image of
    report.translation_validation.validate_translation_facts' own
    (original -> translated) direction: here we're asking whether a
    synthesis CLAIM is traceable to its evidence, not whether a
    TRANSLATION preserved its source."""
    evidence_text = evidence_text or ""
    reasons = []

    text_years = set(_YEAR_RE.findall(text))
    evidence_years = set(_YEAR_RE.findall(evidence_text))
    unsupported_years = text_years - evidence_years
    if unsupported_years:
        reasons.append(f"year(s) not found in cited evidence: {sorted(unsupported_years)}")

    text_percents = {float(p) for p in _PERCENT_RE.findall(text)}
    evidence_percents = {float(p) for p in _PERCENT_RE.findall(evidence_text)}
    unsupported_percents = text_percents - evidence_percents
    if unsupported_percents:
        reasons.append(f"percentage(s) not found in cited evidence: {sorted(unsupported_percents)}")

    text_versions = _extract_versions(text)
    evidence_versions = _extract_versions(evidence_text)
    unsupported_versions = text_versions - evidence_versions
    if unsupported_versions:
        reasons.append(f"version identifier(s) not found in cited evidence: {sorted(unsupported_versions)}")

    text_currency = _extract_currency_values(text)
    evidence_currency = _extract_currency_values(evidence_text)
    unsupported_currency = _missing_values(text_currency, evidence_currency)
    if unsupported_currency:
        reasons.append(f"currency magnitude(s) not found in cited evidence: {sorted(unsupported_currency)}")

    return reasons
