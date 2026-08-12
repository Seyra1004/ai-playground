"""report.validation: ID-grounding, non-empty reason, max-5 enforcement,
partial-category isolation, malformed-output handling."""

from report.validation import (
    MAX_SELECTIONS_PER_CATEGORY,
    CategoryValidationError,
    validate_all_categories,
    validate_category_selection,
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
        "ECONOMY": [{"id": 10, "reason": "valid"}],
        "SOCIETY": [{"id": 20, "reason": "valid"}],
    }
    valid, errors = validate_all_categories(parsed_output, CANDIDATES)
    assert "AI" in errors
    assert "AI" not in valid
    assert valid["ECONOMY"] == [{"id": 10, "reason": "valid"}]
    assert valid["SOCIETY"] == [{"id": 20, "reason": "valid"}]


def test_missing_category_key_is_an_error_for_that_category_only():
    parsed_output = {"AI": [], "ECONOMY": [{"id": 10, "reason": "valid"}]}  # SOCIETY missing
    valid, errors = validate_all_categories(parsed_output, CANDIDATES)
    assert "SOCIETY" in errors
    assert valid["AI"] == []
    assert valid["ECONOMY"] == [{"id": 10, "reason": "valid"}]


def test_empty_selection_list_is_valid():
    valid, errors = validate_all_categories({"AI": [], "ECONOMY": [], "SOCIETY": []}, CANDIDATES)
    assert errors == {}
    assert valid == {"AI": [], "ECONOMY": [], "SOCIETY": []}
