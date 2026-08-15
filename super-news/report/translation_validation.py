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
"""

import re
from dataclasses import dataclass, field

# A trailing `\b` would silently never match real Korean date text --
# "2026년" attaches the year unit directly to the digits with no space,
# and Python's `\b` is Unicode-aware (Hangul counts as a word character),
# so `\d{4}\b` never matches there. A leading `\b` is kept (safe: no
# language routinely prefixes a year with a word character).
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}(?!\d)")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# "GPT-5.6", "Gemini 3.7", "iOS-17.2" -- a name followed by a dotted
# version number. Requires at least one '.' so a bare year-like "GPT-5"
# doesn't collide with the year check, and so ordinary sentence numbers
# ("3 people") are never mistaken for a version. The trailing boundary is
# a negative lookahead for a CONTINUING digit/dot, not a bare `\b` --
# Korean grammar routinely attaches a particle directly to a version
# number with no space ("GPT-5.6이다", "GPT-5.6은"), and Python's `\b` is
# Unicode-aware (Hangul counts as a word character), so a plain `\b`
# would never match there at all. The leading word must start with an
# uppercase letter -- a real-cache audit found the un-anchored version
# caught "the 1.38" out of "...lower than the 1.38 trillion won..." as if
# "the" were a product name and "1.38" its version; real product/model
# names ("GPT", "Gemini", "iOS") are capitalized, ordinary English
# function words never are.
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

# Compound Korean magnitude support (real defect: "6억 6,800만 달러" was
# incorrectly rejected because the original single-segment pattern only
# ever captured the LAST unit in a compound expression -- "6,800만" --
# and silently dropped the "6억" prefix, undercounting a real, correct
# translation by exactly 600,000,000). Real Korean amounts routinely
# chain multiple units together (조/억/만/천만/천), each a genuine
# multiplicative term to be SUMMED, not just the final one: "1조 2,000억"
# = 1조 + 2,000억, "3억 5천만" = 3억 + 5천만. "천만" must be tried before
# the bare "천"/"만" alternatives below it, or a chain like "5천만" would
# match only "5천" and strand the trailing "만" as an unmatched,
# silently-dropped fragment -- dict insertion order is what the
# alternation pattern is built from, so ordering here is load-bearing.
_KO_MAGNITUDE = {"조": 1e12, "억": 1e8, "천만": 1e7, "만": 1e4, "천": 1e3}
_KO_CURRENCY_WORDS = "달러|원|위안|엔|파운드|유로"
_KO_UNIT_PATTERN = "|".join(re.escape(k) for k in _KO_MAGNITUDE)
_KO_SEGMENT_RE = re.compile(rf"([\d,]+(?:\.\d+)?)\s*({_KO_UNIT_PATTERN})")
# One or more adjacent NUMBER+UNIT segments (whitespace-separated only --
# never spanning an intervening word/particle, so two genuinely separate
# amounts in the same sentence are never accidentally summed together),
# immediately followed by a currency word.
_KO_COMPOUND_CURRENCY_RE = re.compile(rf"(?:{_KO_SEGMENT_RE.pattern}\s*)+(?:{_KO_CURRENCY_WORDS})")

# Real-world currency magnitudes only ever need approximate-equality
# (translated numbers are legitimately reformatted -- "4.2 billion" vs
# "42억" -- never exact string equality); this tolerance is generous
# enough for rounding but tight enough that the real 10x 190억/1900억
# defect always fails it.
_MAGNITUDE_RELATIVE_TOLERANCE = 0.02

_HANGUL_RE = re.compile(r"[가-힣]")
# An ABSOLUTE Hangul character count, not a ratio. A ratio threshold was
# tried first and produced real false positives on a real-cache audit
# (CONTENT INTEGRITY FINALIZATION phase, 2026-08-15): short, correct
# translations dominated by legitimately-preserved proper nouns --
# "Gemini 3.7 Flash 소개" (2 Hangul characters, correct) and "The
# Mandalorian과 Grogu는 9월 2일 Disney+에서 공개된다" (10 Hangul
# characters, correct) -- both failed a 0.3 ratio gate. The real LLM-
# refusal defect this check exists to catch has exactly ZERO Hangul
# characters, so a small absolute floor separates the two classes
# cleanly without the ratio's sensitivity to how much of a short title is
# legitimately-preserved Latin brand/product text.
_MIN_HANGUL_CHARS_FOR_PLAUSIBLE_OUTPUT = 2


@dataclass
class ValidationResult:
    ok: bool
    reasons: list = field(default_factory=list)


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
    ("6억 6,800만" -> 6억 + 6,800만), not just the last one -- see the
    module-level comment on _KO_MAGNITUDE for the real defect this fixes.
    Each full compound-expression match is re-scanned with _KO_SEGMENT_RE
    to recover every (number, unit) pair within it, since a repeated
    non-capturing group in Python's `re` only ever retains the LAST
    iteration's captured groups -- `finditer` over the outer pattern only
    gives the right SPAN to then extract from, not the individual terms."""
    values = []
    for match in _KO_COMPOUND_CURRENCY_RE.finditer(text):
        total = 0.0
        for number_str, unit in _KO_SEGMENT_RE.findall(match.group(0)):
            total += float(number_str.replace(",", "")) * _KO_MAGNITUDE[unit]
        values.append(total)
    return values


def _extract_currency_values(text):
    return _en_currency_values(text) + _ko_currency_values(text)


def _values_roughly_match(a, b):
    if a == 0 or b == 0:
        return a == b
    return abs(a - b) / max(abs(a), abs(b)) <= _MAGNITUDE_RELATIVE_TOLERANCE


def _missing_currency_values(original_values, translated_values):
    """Every original magnitude must have SOME matching translated
    magnitude -- order-independent (a translation may legitimately
    reorder clauses), never assumed positional."""
    remaining = list(translated_values)
    missing = []
    for value in original_values:
        match_index = next((i for i, t in enumerate(remaining) if _values_roughly_match(value, t)), None)
        if match_index is None:
            missing.append(value)
        else:
            remaining.pop(match_index)
    return missing


def _extract_versions(text):
    return {(name.lower(), version) for name, version in _VERSION_RE.findall(text)}


def is_plausibly_korean_output(text):
    """A real translation INTO Korean must contain at least a small
    amount of real Hangul -- catches the confirmed real defect where a
    raw LLM refusal/meta-response ("I appreciate you setting up the
    translation task, but...") was cached and displayed as if it were
    translated content. An empty/whitespace-only result is never
    plausible either."""
    if not text or not text.strip():
        return False
    return len(_HANGUL_RE.findall(text)) >= _MIN_HANGUL_CHARS_FOR_PLAUSIBLE_OUTPUT


def validate_translation_facts(original_text, translated_text):
    """Deterministic only -- see module docstring for exactly what this
    does and does not check. Returns a ValidationResult; `ok=False` means
    the caller must never trust `translated_text` as a real translation
    of `original_text`."""
    reasons = []

    if not is_plausibly_korean_output(translated_text):
        reasons.append("translated output is not plausibly Korean (possible non-translation response)")
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
    missing_currency = _missing_currency_values(original_currency, translated_currency)
    if missing_currency:
        reasons.append(f"currency magnitude(s) not preserved: {sorted(missing_currency)}")

    return ValidationResult(ok=not reasons, reasons=reasons)
