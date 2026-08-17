# SUPER NEWS Golden Baseline — 2026-08-18

```
GOLDEN_COMMIT=1cf68c6559ec8b72f4e661096ff9ce77c4b3adcb
GOLDEN_TAG=super-news-golden-2026-08-18
DATE=2026-08-18
```

## PURPOSE

Emergency recovery point for SUPER NEWS. If future development breaks
DAILY/MUSIC, this tag/commit is the last known-good, personally-reviewed
production state to fall back to.

This baseline is **not** declared editorially perfect (see Known
Imperfections below) — it is declared **structurally working**: every
major pipeline stage (ingestion → synthesis → intelligence → translation
→ image enrichment → render → publish) produces a real, non-fabricated,
Korean-language, image-bearing DAILY/MUSIC page, deployed and reachable
at the real public URLs.

## CONFIRMED WORKING AT THIS BASELINE

- SUPER NEWS MUSIC public page loads (`https://seyra1004.github.io/ai-playground/v2/music.html`)
- Korean titles/summaries/analysis visible throughout DAILY and MUSIC
- MUSIC Lead story populated
- MUSIC Industry section populated, with real article images
- MUSIC Today section populated (deterministic across repeated generation)
- Producer/A&R section populated with real, non-generic, non-meta content
- Chart Pulse populated (see Known Imperfections — weekly-cadence source)
- Current approved UI/layout (masthead, card structure, section order) preserved unchanged
- Claude CLI (subscription, non-paid) translation path working, with cache reuse and a validated one-retry repair path
- Article-page `og:image`/`twitter:image` fallback working for cards whose RSS feed carries no image
- GitHub Pages deployment path working (git commit + push → live within ~1–2 min)
- Kakao delivery architecture preserved unchanged (idempotency via `delivery_history`, manual-resend fallback path both intact) — **not exercised as part of creating this baseline**

## KNOWN IMPERFECTIONS (accepted — do not block using this as a rollback target)

- Editorial quality is "genuinely useful," not polished — still needs iteration
- Some stories can still repeat across sections on a thin evidence day
- Production Radar may legitimately render empty (no fabrication when evidence is absent)
- Some analysis/why-it-matters text remains generic rather than deeply specific
- A Google News/aggregator-sourced image may occasionally appear where a direct-publisher image would be preferable
- Spotify chart data source is genuinely **weekly**, not daily (confirmed via DB trace: only 2 observation timestamps ever recorded, 7 days apart) — currently dated 2026-08-13, honestly labeled as "기준" (as-of), never implying it's today's chart
- Some AI/MUSIC articles have no image at all when their source RSS + article page both lack `og:image`/`twitter:image` metadata — correctly shown with no image rather than a placeholder

## FILES THAT DEFINE THIS BASELINE (all present in `1cf68c6`)

Production code:
- `super-news/report/translation_claude_cli.py` — Claude CLI title translation provider
- `super-news/report/image_enrichment.py` — article-page og:image/twitter:image fallback
- `super-news/report/translation.py` — provider selection wiring
- `super-news/report/web_data_v2.py` — dashboard data assembly, Industry quality floor, deterministic Music Today backfill
- `super-news/report/web_render_v2.py` — DAILY/MUSIC HTML rendering, gossip/meta filters
- `super-news/report/validation.py` — content-creation-advice, gossip, no-evidence-meta-statement filters
- `super-news/report/producer_orchestrator.py` — Producer Intelligence, evidence-catalog exclusion fix
- `super-news/report/music_trend_orchestrator.py` — Music Trend Intelligence, evidence-catalog exclusion fix

Generated output (already committed, not regenerated for this baseline):
- `docs/v2/daily.html`, `docs/v2/music.html`, `docs/v2/index.html`
- `docs/v2/reports/2026-08-18.html`

---

## RECOVERY PROCEDURE

### A. Inspect the baseline (read-only, safe at any time)

```
git show super-news-golden-2026-08-18
git show super-news-golden-2026-08-18 --stat
```

### B. Compare current state against the baseline (read-only, safe at any time)

```
git diff super-news-golden-2026-08-18..HEAD -- super-news docs/v2
git diff super-news-golden-2026-08-18..HEAD --stat -- super-news docs/v2
```

Use this BEFORE any rollback decision to see exactly what changed since
the known-good point — often the diff alone reveals the regression
without needing a rollback at all.

### C. Emergency rollback strategy (DO NOT execute pre-emptively — only when a real regression is confirmed and the user has approved a rollback)

**Do not** blindly `git reset --hard` the whole repository — this repo
contains unrelated work outside `super-news/`/`docs/v2/` that must never
be discarded.

**Preferred method — restore only the SUPER NEWS-related paths from the
golden tag, as a new commit (never rewrites history):**

```
# 1. Safety net first: back up the current (broken) state before touching anything
git branch backup/pre-golden-rollback-<date> HEAD

# 2. Restore ONLY the production paths from the golden tag into the working tree
git checkout super-news-golden-2026-08-18 -- super-news/report/translation_claude_cli.py
git checkout super-news-golden-2026-08-18 -- super-news/report/image_enrichment.py
git checkout super-news-golden-2026-08-18 -- super-news/report/translation.py
git checkout super-news-golden-2026-08-18 -- super-news/report/web_data_v2.py
git checkout super-news-golden-2026-08-18 -- super-news/report/web_render_v2.py
git checkout super-news-golden-2026-08-18 -- super-news/report/validation.py
git checkout super-news-golden-2026-08-18 -- super-news/report/producer_orchestrator.py
git checkout super-news-golden-2026-08-18 -- super-news/report/music_trend_orchestrator.py

# 3. Review exactly what this staged (must be ONLY the 8 files above)
git status --short -- super-news/

# 4. Commit as a new, forward-moving commit (never amend/rewrite)
git commit -m "Roll back SUPER NEWS production logic to golden baseline 2026-08-18"

# 5. Push (normal push, not force — this is a new commit, not a history rewrite)
git push origin main
```

This restores ONLY the 8 production files above from the known-good
point, as a new commit on top of current history — nothing else in the
repository (including any unrelated work, or SUPER NEWS test files, or
docs unrelated to these 8 files) is touched, and nothing is force-pushed
or destructively reset.

If the regression is broader than these 8 files (e.g. it also affects
`report/producer_synthesis.py` or `report/music_trend_synthesis.py`
prompt text, which are NOT part of this specific baseline's file list),
diff those files individually against the tag first (step B) before
deciding whether to include them in the restore.

### D. Regenerate and publish from the restored baseline

After restoring code (step C) — or if no code rollback was needed and
only a fresh regeneration is required:

```
cd super-news
export SUPER_NEWS_NO_PAID_API=1
export LLM_PROVIDER=claude_cli

# Only if today's data hasn't been ingested yet:
.venv/Scripts/python.exe scripts/run_daily_ingestion.py
.venv/Scripts/python.exe scripts/run_daily_music.py
.venv/Scripts/python.exe scripts/run_daily_music_spotify.py
.venv/Scripts/python.exe scripts/run_daily_music_signals.py

# Synthesis + intelligence (each once):
.venv/Scripts/python.exe scripts/run_daily_report.py
.venv/Scripts/python.exe scripts/run_daily_producer_intelligence.py
.venv/Scripts/python.exe scripts/run_daily_music_trend_intelligence.py
.venv/Scripts/python.exe scripts/run_daily_news_intelligence.py

# Generate the HTML (writes docs/v2/{index,daily,music}.html + docs/v2/reports/<date>.html):
.venv/Scripts/python.exe scripts/generate_daily_web_report_v2.py --report-date <YYYY-MM-DD>

# INSPECT THE ACTUAL GENERATED HTML BEFORE PUBLISHING — per the standing
# /remember workflow: tests passing / files existing / HTTP 200 is never
# sufficient. Read docs/v2/daily.html and docs/v2/music.html directly.

# Publish (from repo root):
cd ..
git add docs/v2/daily.html docs/v2/index.html docs/v2/music.html docs/v2/reports/<YYYY-MM-DD>.html
git commit -m "Publish SUPER NEWS V2 dashboard (<YYYY-MM-DD>) to docs/v2/"
git push origin main

# Verify the live public pages actually reflect the new commit (poll, don't assume):
curl -s -o /dev/null -w "%{http_code}" https://seyra1004.github.io/ai-playground/v2/daily.html
curl -s -o /dev/null -w "%{http_code}" https://seyra1004.github.io/ai-playground/v2/music.html
```

### E. How to send DAILY + MUSIC via Kakao using the established production path (DOCUMENTATION ONLY — do not execute without explicit user approval each time)

Normal (idempotent) path — used by the real scheduled automation:

```
.venv/Scripts/python.exe scripts/run_daily_kakao_delivery_v2.py
```

This calls `report_delivery_v2.deliver_daily_summary_v2`/
`deliver_music_digest_v2`, each independently gated by
`delivery_history` (`build_idempotency_key(report_date_kst, report_type,
destination)` → `decide_delivery_action` → skips if already sent that
date). It will silently no-op (correctly) if today's digest was already
sent.

If a legitimate manual resend is needed AND `delivery_history` already
has a `sent` row for today (the idempotency guard would otherwise skip
it), use the established manual-resend pattern — direct, one-off,
**never modifies `delivery_history`**:

```python
# Reuses existing render/link functions directly; calls kakao.client.send_memo()
# once per product; deliberately never calls record_delivery().
from report.web_data_v2 import build_dashboard_data_v2
from report.kakao_render_v2 import render_music_kakao_digest, render_daily_kakao_digest
from report_delivery_v2 import _resolve_v2_link_url, _MUSIC_CTA_BUTTON_TITLE, _DAILY_CTA_BUTTON_TITLE
from kakao.client import send_memo
```

**Both paths require the user's explicit, per-send approval before
execution — this document does not authorize sending anything.**

---

## VERIFICATION PERFORMED WHEN THIS BASELINE WAS CREATED

- `git rev-parse HEAD` = `1cf68c6559ec8b72f4e661096ff9ce77c4b3adcb`
- Confirmed all 8 core production files + `docs/v2/{daily,music}.html` present in the commit tree
- Annotated tag `super-news-golden-2026-08-18` created pointing exactly at `1cf68c6`; `git rev-parse super-news-golden-2026-08-18^{commit}` confirmed matching
- Tag pushed to `origin`
- No content/UI/translation/image/scheduler code touched while creating this baseline
- No news regenerated, no Kakao sent
