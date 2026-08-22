"""Deterministic translation fact-preservation validation (CONTENT
INTEGRITY FINALIZATION phase, 2026-08-15).

Motivated by real, confirmed production defects (see SUPER_NEWS_HANDOFF.md):
a $190B valuation was translated as "190억 달러" (a Korean 억=100M-unit
conversion error making the figure a tenfold understatement), and a
malformed 1-character source snippet ("(") produced a raw LLM refusal
message ("I appreciate you setting up the translation task, but...")
that was cached and displayed as if it were real translated content.

Deliberately narrow and deterministic -- this does NOT attempt to verify
semantic/policy meaning (e.g. "two-term" vs "단임제" is a real defect
class this module cannot and does not try to catch; see the module's own
docstring in report/translation.py and SUPER_NEWS_HANDOFF.md for that
class being recorded as a future semantic-validation candidate instead).
What IS checked, because it's mechanically checkable without any language
model: every explicit YEAR, PERCENTAGE, VERSION NUMBER, and CURRENCY
MAGNITUDE present in the original text must have an equivalent-magnitude
counterpart somewhere in the translated text -- not an identical string
(currency/number formatting legitimately changes across languages), an
equivalent real-world VALUE. A translated result that fails this check,
or that isn't plausibly Korean at all (the LLM-refusal case), is never
trusted as a real translation -- see report/translation.py's
translate_and_cache, which routes a validation failure through the
EXISTING STATUS_FAILED/FAILURE_KIND_TRANSIENT path (original text stays
displayed, nothing fabricated is cached, a later attempt is still
possible) -- no new status value, no schema change.

The underlying token-extraction/Hangul-plausibility primitives now live in
report/text_quality.py (extracted so the same real, confirmed defect
classes can also be checked for LLM-NATIVE-generated Korean synthesis text
-- News/Producer/Music Trend Intelligence, news-selection `reason` -- not
only for translated text; see report/validation.py). This module re-
exports is_plausibly_korean_output for existing callers/tests.
"""

from dataclasses import dataclass, field

from report.text_quality import (
    _PERCENT_RE,
    _YEAR_RE,
    _extract_currency_values,
    _extract_versions,
    _missing_values,
    has_refusal_marker,
    is_plausibly_korean_output,
)

__all__ = ["ValidationResult", "validate_translation_facts", "is_plausibly_korean_output"]


@dataclass
class ValidationResult:
    ok: bool
    reasons: list = field(default_factory=list)


def validate_translation_facts(original_text, translated_text):
    """Deterministic only -- see module docstring for exactly what this
    does and does not check. Returns a ValidationResult; `ok=False` means
    the caller must never trust `translated_text` as a real translation
    of `original_text`."""
    reasons = []

    if not is_plausibly_korean_output(translated_text):
        reasons.append("translated output is not plausibly Korean (possible non-translation response)")
        return ValidationResult(ok=False, reasons=reasons)

    # PRODUCTION INCIDENT FIX (2026-08-22, confirmed real defect): a
    # meta-response/task-commentary result ("No headline text was
    # provided to translate...") can legitimately embed the real Korean
    # source verbatim at the end, so is_plausibly_korean_output's Hangul
    # floor alone does not catch it -- the SAME deterministic refusal-
    # marker check report/text_quality.py already applies to LLM-native
    # synthesis text, reused here for translated text too.
    if has_refusal_marker(translated_text):
        reasons.append("translated output contains a known refusal/meta-response marker")
        return ValidationResult(ok=False, reasons=reasons)

    original_years = set(_YEAR_RE.findall(original_text))
    translated_years = set(_YEAR_RE.findall(translated_text))
    missing_years = original_years - translated_years
    if missing_years:
        reasons.append(f"year(s) not preserved: {sorted(missing_years)}")

    original_percents = {float(p) for p in _PERCENT_RE.findall(original_text)}
    translated_percents = {float(p) for p in _PERCENT_RE.findall(translated_text)}
    missing_percents = original_percents - translated_percents
    if missing_percents:
        reasons.append(f"percentage(s) not preserved: {sorted(missing_percents)}")

    original_versions = _extract_versions(original_text)
    translated_versions = _extract_versions(translated_text)
    missing_versions = original_versions - translated_versions
    if missing_versions:
        reasons.append(f"version identifier(s) not preserved: {sorted(missing_versions)}")

    original_currency = _extract_currency_values(original_text)
    translated_currency = _extract_currency_values(translated_text)
    missing_currency = _missing_values(original_currency, translated_currency)
    if missing_currency:
        reasons.append(f"currency magnitude(s) not preserved: {sorted(missing_currency)}")

    return ValidationResult(ok=not reasons, reasons=reasons)
