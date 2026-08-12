"""LLM structured-output validation: ID-grounding + non-empty reason +
max-5 enforcement. Every category is validated INDEPENDENTLY -- a
hallucinated id or malformed selection in one category never blocks the
other categories' reports (partial-category validation isolation). This is
the only backstop between raw model output and the DB; nothing downstream
re-checks these invariants.
"""

MAX_SELECTIONS_PER_CATEGORY = 5


class CategoryValidationError(Exception):
    def __init__(self, category, reason):
        self.category = category
        self.reason = reason
        super().__init__(f"{category}: {reason}")


def validate_category_selection(category, selections, candidate_ids):
    """selections: the raw value the LLM returned for this category (should
    be a list of {"id": int, "reason": str} dicts). candidate_ids: the set
    of ids that were actually offered to the LLM for this category. Returns
    `selections` unchanged if valid; raises CategoryValidationError
    otherwise -- never silently drops or truncates an invalid selection."""
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
    return selections


def validate_all_categories(parsed_output, candidates_by_category):
    """Returns (valid, errors): valid is dict[category -> selections] for
    categories that passed; errors is dict[category -> CategoryValidationError]
    for categories that didn't. Every category in candidates_by_category
    appears in exactly one of the two dicts."""
    valid = {}
    errors = {}

    if not isinstance(parsed_output, dict):
        for category in candidates_by_category:
            errors[category] = CategoryValidationError(
                category, f"malformed structured output: root is {type(parsed_output).__name__}, not an object"
            )
        return valid, errors

    for category, candidates in candidates_by_category.items():
        candidate_ids = {c["id"] for c in candidates}
        selections = parsed_output.get(category)
        try:
            if selections is None and category not in parsed_output:
                raise CategoryValidationError(category, "missing category key in structured output")
            valid[category] = validate_category_selection(category, selections, candidate_ids)
        except CategoryValidationError as exc:
            errors[category] = exc

    return valid, errors
