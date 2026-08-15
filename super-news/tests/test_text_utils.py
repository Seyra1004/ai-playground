"""report.text_utils: pure text-composition helpers shared by producer
synthesis (LLM grounding) and web_data_v2 (display)."""

from report.text_utils import dedupe_join, is_redundant


def test_dedupe_join_drops_exact_duplicate():
    assert dedupe_join("Same text", "Same text") == "Same text"


def test_dedupe_join_drops_substring_fragment():
    assert dedupe_join("Spotify launches new tool", "Spotify launches new") == "Spotify launches new tool"


def test_dedupe_join_keeps_distinct_fragments():
    result = dedupe_join("Title", "Genuinely different context")
    assert "Title" in result
    assert "Genuinely different context" in result


def test_dedupe_join_skips_empty_and_none():
    assert dedupe_join("Only this", None, "", "   ") == "Only this"


def test_dedupe_join_case_insensitive():
    assert dedupe_join("Hello World", "hello world") == "Hello World"


def test_dedupe_join_order_preserving_earlier_wins():
    result = dedupe_join("A", "A extended text")
    # "A" is kept first; "A extended text" is NOT a substring of "A", so it's also kept.
    assert result == "A — A extended text"


def test_is_redundant_true_for_substring():
    assert is_redundant("Title", "Title with more context") is True


def test_is_redundant_true_for_reverse_substring():
    assert is_redundant("Title with more context", "Title") is True


def test_is_redundant_false_for_distinct_text():
    assert is_redundant("Completely different sentence", "Original reason") is False


def test_is_redundant_true_when_candidate_empty():
    assert is_redundant("", "Original reason") is True
    assert is_redundant(None, "Original reason") is True


def test_is_redundant_false_when_reference_empty():
    assert is_redundant("Some candidate text", None) is False
