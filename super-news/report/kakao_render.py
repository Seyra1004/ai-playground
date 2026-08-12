"""Deterministic persisted-report -> Kakao message rendering + minimum-
message-count splitting. No LLM call, no re-synthesis: this module only
assembles a digest header/section structure around reports.content exactly
as persisted by report/persistence.py (news) and report/music_diff.py
(music), then splits the result to fit Kakao's per-message character limit
(kakao/client.py's MAX_TEXT_LENGTH)."""

SECTION_ORDER = ("AI", "ECONOMY", "SOCIETY", "MUSIC")


def render_digest_text(report_date_kst, reports_by_category):
    """reports_by_category: dict category -> persisted content string. Only
    categories that actually have a persisted report this run should be
    passed in -- callers omit missing ones; this function never fabricates
    placeholder content for a category it wasn't given. Returns ONE logical
    digest string (before splitting), with a header line and one [SECTION]
    block per available category, in SECTION_ORDER."""
    y, m, d = report_date_kst.split("-")
    lines = [f"SUPER NEWS — {y}.{m}.{d}"]
    for category in SECTION_ORDER:
        content = reports_by_category.get(category)
        if content is None:
            continue
        lines.append("")
        lines.append(f"[{category}]")
        lines.append(content)
    return "\n".join(lines)


def split_message(text, max_length):
    """Deterministic split into the minimum number of chunks this greedy
    line-packing approach can produce, each len() <= max_length. Never
    splits mid-word when a line fits within max_length on its own; a line
    longer than max_length is broken at word (space) boundaries, and only a
    single "word" longer than max_length by itself is hard-split by
    character count (last resort, for scripts/strings with no spaces).
    Returns chunks in send order -- callers send them in exactly this order
    and nothing else."""
    if max_length <= 0:
        raise ValueError("max_length must be positive.")

    units = []
    for line in text.split("\n"):
        if len(line) <= max_length:
            units.append(line)
        else:
            units.extend(_split_long_line(line, max_length))

    chunks = []
    current = ""
    for unit in units:
        candidate = unit if not current else current + "\n" + unit
        if len(candidate) <= max_length:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = unit  # guaranteed <= max_length by construction above
    if current:
        chunks.append(current)
    return chunks


def _split_long_line(line, max_length):
    words = line.split(" ")
    pieces = []
    current = ""
    for word in words:
        if len(word) > max_length:
            if current:
                pieces.append(current)
                current = ""
            for i in range(0, len(word), max_length):
                pieces.append(word[i:i + max_length])
            continue
        candidate = word if not current else current + " " + word
        if len(candidate) <= max_length:
            current = candidate
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces
