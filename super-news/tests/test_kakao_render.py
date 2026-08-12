"""report.kakao_render: deterministic digest assembly + minimum-message-count
splitting (no LLM call)."""

import pytest

from report.kakao_render import render_digest_text, split_message


# ---- rendering --------------------------------------------------------------


def test_render_digest_includes_header_and_available_sections():
    text = render_digest_text("2026-08-12", {"AI": "AI content", "MUSIC": "MUSIC content"})
    assert "SUPER NEWS — 2026.08.12" in text
    assert "[AI]" in text
    assert "AI content" in text
    assert "[MUSIC]" in text
    assert "MUSIC content" in text


def test_render_digest_omits_missing_categories_never_fabricates():
    text = render_digest_text("2026-08-12", {"AI": "AI content"})
    assert "[ECONOMY]" not in text
    assert "[SOCIETY]" not in text
    assert "[MUSIC]" not in text


def test_render_digest_section_order_is_fixed():
    text = render_digest_text(
        "2026-08-12",
        {"MUSIC": "m", "AI": "a", "SOCIETY": "s", "ECONOMY": "e"},
    )
    assert text.index("[AI]") < text.index("[ECONOMY]") < text.index("[SOCIETY]") < text.index("[MUSIC]")


def test_render_digest_deterministic():
    reports = {"AI": "a", "ECONOMY": "e"}
    assert render_digest_text("2026-08-12", reports) == render_digest_text("2026-08-12", reports)


# ---- message length / splitting ---------------------------------------------


def test_short_message_is_a_single_chunk():
    chunks = split_message("hello world", 200)
    assert chunks == ["hello world"]


def test_long_message_splits_into_multiple_chunks_each_within_limit():
    text = "\n".join(f"line {i} with some extra padding text" for i in range(20))
    chunks = split_message(text, 50)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 50


def test_split_never_drops_content_rejoining_reconstructs_all_lines():
    lines = [f"item {i}" for i in range(30)]
    text = "\n".join(lines)
    chunks = split_message(text, 20)
    reconstructed_lines = "\n".join(chunks).split("\n")
    assert reconstructed_lines == lines


def test_split_is_minimum_chunk_count_for_trivially_packable_input():
    # 4 lines of 10 chars each ("0123456789") joined by "\n" (1 char) = 43
    # chars total; max_length=50 means everything should fit in ONE chunk.
    lines = ["0123456789"] * 4
    text = "\n".join(lines)
    chunks = split_message(text, 50)
    assert len(chunks) == 1


def test_split_deterministic_across_calls():
    text = "\n".join(f"line {i}" for i in range(15))
    assert split_message(text, 15) == split_message(text, 15)


def test_split_handles_a_single_line_longer_than_max_length():
    long_line = " ".join(f"word{i}" for i in range(50))
    chunks = split_message(long_line, 30)
    for chunk in chunks:
        assert len(chunk) <= 30
    # No word content lost -- every word appears somewhere across the chunks.
    rejoined = " ".join(chunks).split()
    for i in range(50):
        assert f"word{i}" in rejoined


def test_split_hard_splits_a_single_unspaceable_token_longer_than_max_length():
    token = "가" * 500  # a single "word" with no spaces, far past max_length
    chunks = split_message(token, 200)
    for chunk in chunks:
        assert len(chunk) <= 200
    assert "".join(chunks) == token


def test_split_rejects_non_positive_max_length():
    with pytest.raises(ValueError):
        split_message("hello", 0)
