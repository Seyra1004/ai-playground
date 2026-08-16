# SUPER NEWS — CURRENT STATE

## Canonical references
- Product contract: SUPER_NEWS_SPEC.md
- Historical archive: SUPER_NEWS_HANDOFF.md
- Current implementation state: this file

## Product identity
SUPER NEWS MUSIC
“작곡가·프로듀서를 위한 오늘의 음악 인텔리전스”

SUPER NEWS DAILY
“AI·경제·사회의 오늘 핵심만 빠르게”

Web remains one publication.
MUSIC is the dominant primary product.
Kakao will eventually be split into two independent daily messages.

## Completed in current worktree

1. Premium newsletter renderer redesign
- desktop left rail removed
- horizontal nav added
- centered ~900px reading column
- Lead Story
- Today in Music max 3
- real headline links
- analysis-only signals not fake-clickable
- lead fallback when no is_strongest exists
- MUSIC INTELLIGENCE release marker preserved
- renderer targeted result at that stage: 50 passed / 0 failed

2. Chart Pulse real-date contract
- report_date and chart_date are separate
- chart_date source = real Spotify observation observed_at
- visible YYYY.MM.DD 기준
- missing chart_date = 기준일 확인 필요
- first-observation prose uses actual chart_date
- targeted result at that stage: 126 passed / 0 failed

3. MUSIC editorial imagery
- real RSS media metadata only
- image_url stored through existing extra_json
- no DB schema migration
- Lead Story max 1 real image
- Today in Music max 3 thumbnails
- no placeholder/fabricated imagery
- analysis-only signals cannot receive article imagery
- invalid/non-http image values rejected
- responsive image CSS
- accessibility alt text
- latest targeted result:
  157 passed / 0 failed

4. MUSIC editorial ranking + TRUE event-level exposure budget dedup
- priority order fixed to match SUPER_NEWS_SPEC.md section 8 exactly: licensing
  merged into priority 1 (rights/copyright/publishing/royalty/licensing, was
  incorrectly split across two classes); consumption/chart (6) and touring (7)
  un-swapped (were previously reversed)
- LEGAL/RIGHTS EXCEPTION: a real rights/copyright/publishing/royalty/licensing
  match is now checked BEFORE the generic legal-language down-rank keywords, so
  a real copyright/licensing lawsuit is never incorrectly swept into the
  personal-life/gossip down-rank bucket just for containing "lawsuit against"
- `event_key` now carried on every news item (both the LLM-selected and
  no-LLM-fallback paths) — reuses the already-computed real event_key, no new
  query, no schema change (same "smallest safe field" pattern as image_url)
- CORRECTIVE PASS -- TRUE event-level exposure budget (closes a real gap the
  first pass left open: different evidence_refs alone was being treated as
  proof of a different real event, letting the same event survive as Lead +
  Genre + Production + Producer simultaneously): evidence ref -> real article
  -> real event_key is now resolved wherever structured data allows it
  (`report.web_render_v2._resolve_entry_event_key`, matching a synthesis
  entry's real evidence-citation summary text against a real news item's own
  title — reused in reverse from the existing `_evidence_refs_for_title`
  mechanic; documented limitation: a chart-fact-only evidence citation has no
  real article to resolve to, so it honestly falls back to real evidence-ref
  identity, never a fabricated event_key). Real hard cap: 2 total visible
  exposures per real event (Lead + at most 1 further genuinely distinct real
  interpretation) enforced across Today in Music (zero tolerance) / Music
  Industry (zero tolerance) / Genre Radar -> Production Radar -> Producer/A&R
  (fixed order, first real match wins the one allowed slot, every later match
  suppressed even with fully disjoint evidence). Deterministic identity only
  (real event_key / real evidence refs) — zero new LLM calls, zero fuzzy title
  similarity.
- Music Industry's own `_music_industry_state` still reads the real,
  unfiltered news lists — suppressing the lead's duplicate from the DISPLAY
  never turns a real-coverage day into a false "no news today"
- SECOND CORRECTIVE PASS -- TRUE upstream lineage, not title-text matching
  (closes a real remaining architectural weakness: the first corrective pass's
  event-key resolution still depended on an evidence citation's summary TEXT
  exactly/prefix-matching a real article's title, which a legitimately
  paraphrased summary could fail — e.g. a genre_signal's own real "observed"
  fact quoting/paraphrasing the article differently than its own title reads).
  INSPECTED FIRST (no guessing): `report.music_trend_synthesis.
  build_evidence_catalog`/`report.producer_synthesis.build_evidence_catalog`
  build their MUSIC_INDUSTRY_NEWS evidence entries directly FROM the same real
  `industry_news` item dicts (`dashboard_data["news"]["SPOTIFY"/"TIKTOK"]
  ["items"]`) that already carry a real `event_key` (from the first pass) —
  so the real deterministic identity was already available at catalog-BUILD
  time, one level upstream of any text. FIX: both `build_evidence_catalog`
  functions now propagate that real `event_key` DIRECTLY onto each
  MUSIC_INDUSTRY_NEWS catalog entry (`{"ref","type","summary","event_key"}`);
  this flows through persistence (`output_text.catalog`, unchanged shape,
  additive field only) and through `report.web_data_v2._safe_parse_producer_
  intelligence`/`_safe_parse_music_trend_intelligence`'s `evidence_by_ref` (now
  `{ref: {"summary","event_key"}}`) into every signal's own real `"evidence"`
  list via the new shared `_resolved_evidence_entry` helper — so
  `report.web_render_v2._resolve_entry_event_key` now checks a REAL,
  ALREADY-PROPAGATED `event_key` FIRST, and only falls back to the old
  exact/prefix title match for a legacy row whose catalog entry predates this
  propagation. Non-article evidence (chart/cross-platform facts) still
  honestly gets `event_key: None` — never fabricated. Zero fuzzy matching,
  zero embeddings, zero new LLM/API calls. The max-2-exposure contract (Lead +
  at most 1 distinct interpretation; zero tolerance in Today in Music/Music
  Industry) is unchanged and still enforced — only the identity RESOLUTION
  mechanism improved.
- latest targeted result:
  215 passed / 0 failed (tests/test_web_data_v2.py + tests/test_web_render_v2.py
  + tests/test_cli_generate_daily_web_report_v2.py + tests/test_music_trend_
  orchestrator.py + tests/test_producer_orchestrator.py +
  tests/test_producer_synthesis.py)

## Latest relevant files changed

Confirmed via `git status --short` in the current worktree (modified, not yet committed):

- `ingestion/adapters/rss.py` — real feed image metadata extraction (media:thumbnail / media:content / image-typed enclosure) into `IngestionRecord.extra["image_url"]`
- `report/web_data_v2.py` — `_spotify_chart_section` chart_date field; `_extract_trustworthy_image_url` + `_lookup_item_detail`/`_news_section`/`_raw_fallback_items` image_url plumbing; `event_key` added to news item dicts; `_MUSIC_INDUSTRY_PRIORITY_KEYWORDS` reordered + LEGAL/RIGHTS EXCEPTION in `music_industry_priority_rank`; `_collect_music_signal_candidates`/`_build_today_music_intelligence` carry both `_evidence_refs` (ref-label set) AND `_evidence` (full ref+real-summary+event_key citations) through to the signal dict; NEW `_resolved_evidence_entry` shared helper; `_safe_parse_producer_intelligence`/`_safe_parse_music_trend_intelligence`'s `evidence_by_ref` now carries `event_key` per ref, not just summary text; FAST COMPLETION PASS additions: `_MUSIC_TREND_REJECT_KEYWORDS`/`_is_marketing_or_popularity_framing` (strict Genre/Production gate), `_producer_intelligence_section`'s new redundancy-rejection check, `_build_music_today` no longer pads with already-shown candidates; lead-fallback logic lives in the renderer, not here
- `report/web_render_v2.py` — left-rail/shell removal, horizontal `.pub-nav`, newsletter Lead Story + Today in Music hero (incl. lead-fallback fix), Chart Pulse real chart_date rendering, lead image + secondary thumbnail rendering; `_lead_signal` helper (shared by hero render + exposure-budget suppression); `_news_title_to_event_key_map`/`_resolve_entry_event_key` (now checks a real propagated `event_key` FIRST, title-match only as legacy fallback)/`_signal_event_identity`/`_synthesis_entry_event_identity`/`_shares_lead_evidence`/`_same_resolved_event`/`_exclude_lead_event_from_today_in_music`/`_apply_music_event_exposure_budget` implement the TRUE event-level exposure budget; `render_dashboard_html_v2` pre-filters `music_today`/genre_signals/production_notes/producer insights/producer_references/kpop_ar_notes ONCE before rendering, so `_render_industry_section` (still `exclude_event_key`-based) and `_render_genre_radar_section`/`_render_production_radar_section`/`_render_producer_section` (plain, budget-agnostic renderers) stay simple; FAST COMPLETION PASS additions: `_resolved_event_keys_for_entry`/`_dedupe_producer_section_exact_duplicates` (cross-catalog Producer/A&R duplicate fix), `@media (max-width: 600px)` masthead tagline/read-time clipping fix
- `report/music_trend_synthesis.py` — `build_evidence_catalog`'s `add()` helper now accepts `event_key`, propagated onto every MUSIC_INDUSTRY_NEWS catalog entry from the originating real `industry_news` item
- `report/producer_synthesis.py` — same real `event_key` propagation in its own independent `build_evidence_catalog`
- `tests/test_web_data_v2.py` — chart_date + image_url data-layer tests; MUSIC editorial ranking priority-order + legal-exception tests; event_key propagation tests; 4 pre-existing evidence-shape assertions updated (additive `event_key` key) + new tests proving direct catalog-to-event_key propagation even with a paraphrased summary
- `tests/test_web_render_v2.py` — hero/nav/chart-date/image render-layer tests; MUSIC EVENT EXPOSURE BUDGET cross-section suppression tests (first pass) + TRUE event-level corrective-pass tests (both letter sets: same-event-via-different-outlet recognition, 2-exposure hard cap, third-appearance suppression, unrelated-event non-interference, paraphrased-evidence resolution, legacy-fallback safety, chart-fact non-fabrication)
- `tests/test_cli_generate_daily_web_report_v2.py` — one pre-existing assertion updated: a persisted producer insight that is the day's only real MUSIC signal now correctly becomes the Lead Story instead of ALSO independently duplicating as a separate Producer/A&R card (the fix the first ranking/dedup pass made)

Not modified this worktree (used for verification only, not touched):
- `tests/test_ingestion_rss_adapter.py`
- `tests/test_ingestion_persistence_and_pipeline.py`
- `tests/test_music_trend_orchestrator.py`
- `tests/test_producer_orchestrator.py`
- `tests/test_producer_synthesis.py`

## 5. FAST COMPLETION PASS — Genre/Production/Producer qualification, real
report, browser/mobile QA, full regression, product score

Steps 1-11 of the previous "remaining pre-release work" sequence are DONE.

- **Product principle persisted**: SUPER_NEWS_SPEC.md section 38 added —
  MUSIC is NOT optimized for a 10-15 min reading target; quality/composer
  value over reading time or fixed article count; Today in Music max 3 means
  MAXIMUM not mandatory.
- **Strict Genre/Production Radar**: added `_MUSIC_TREND_REJECT_KEYWORDS` +
  `_is_marketing_or_popularity_framing` (`report/web_data_v2.py`) — a real
  genre/production keyword appearing purely inside TikTok-virality or
  ordinary tool-feature-launch framing (e.g. "템포" as a UI control label,
  not an observed characteristic) is now rejected even though the positive
  keyword gate alone would have passed it. Checked BEFORE the positive gate,
  same precedent as `_MUSIC_INDUSTRY_DOWNRANK_KEYWORDS`.
- **Producer/A&R quality**: added a deterministic redundancy gate in
  `_producer_intelligence_section` — an insight whose `what_is_moving` is
  (near-)verbatim redundant with its own cited evidence is rejected (reuses
  the existing `_is_redundant` helper), backstopping the prompt's own
  "don't restate the catalog verbatim" instruction with a real code-level
  check.
- **Cross-catalog Producer/A&R duplicate (confirmed via ACTUAL report QA,
  not theory)**: `producer_intelligence` and `music_trend_intelligence` each
  build their OWN independently ref-labelled evidence catalog, so the SAME
  real event ("TikTok Music on Stage returns") could appear once as a
  Producer insight and again as its own K-pop/A&R note — a raw ref/summary
  comparison could never catch this (an "E1" in one catalog is unrelated to
  an "E1" in the other). Fixed with `_resolved_event_keys_for_entry` +
  `_dedupe_producer_section_exact_duplicates` (`report/web_render_v2.py`):
  an entry is suppressed only when EVERY real event it resolves to is
  already covered by an earlier-kept entry (full subset coverage) — a
  broader multi-event synthesis that also introduces a genuinely NEW event
  is never suppressed.
- **MUSIC TODAY padding removed (confirmed real defect via actual report
  QA)**: `_build_music_today` no longer falls back to re-showing an
  already-displayed hero candidate to fill the section on a thin real day —
  it previously re-rendered the SAME 3 cards the hero just showed,
  immediately below it, reading as literal duplicate content. Now shows
  fewer items, honestly down to zero, per the section-38 "max means
  maximum, not mandatory" principle.
- **Mobile masthead clipping (confirmed real defect via TRUE 390px headless-
  Chrome rendering, fixed)**: `.brand-row`'s tagline ("Daily Music
  Intelligence") and `.meta-row`'s read-time ("XX MIN READ") were rendering
  clipped/fully invisible past the right edge on real narrow viewports
  instead of wrapping. Fixed in the `@media (max-width: 600px)` block:
  `.tagline { flex-basis: 100%; }` and `.meta-row { flex-direction: column;
  ... }` force each onto its own line — real content, never lost.
- **Real report generated & reviewed**: report_date=2026-08-14 (freshest
  real ingested data — `reports` marker table is empty in this dev DB, so
  news categories used the honest no-LLM-selection fallback path; real
  `music_trend_intelligence`/`producer_intelligence` rows exist for this
  date). Rendered via `scripts/generate_daily_web_report_v2.py --db-path`
  (real DB) `--docs-dir` (scratchpad override only, repo `docs/` untouched).
  Reviewed: real licensing-story Lead (Spotify/Kobalt), 3 distinct Today in
  Music signals, honest-empty Genre Radar, 2 real Production Radar signals,
  6 clean deduped Producer/A&R cards, real distinct chart_date
  (2026.08.06 ≠ report_date 2026.08.14), zero fake/debug/leaked content,
  MUSIC ≈74% of body content vs AI/Economy/Society/Sources ≈26%.
- **Browser/mobile QA**: claude-in-chrome's `resize_window` does not affect
  the actual render viewport in this environment (`window.innerWidth`
  stayed 2560 regardless of the requested size) — confirmed via direct JS
  read-back, not assumed. Used the ALREADY-INSTALLED system Chrome binary
  (`C:\Program Files\Google\Chrome\Application\chrome.exe`, no new package)
  in headless CLI mode with `--window-size` instead — verified TRUE
  pixel-accurate rendering at 390 and 430 width via output PNG byte
  dimensions (390×3400 / 430×3200, confirmed via direct file inspection).
  Real DOM-level checks (real hrefs, `target="_blank"`, `rel="noopener
  noreferrer"`, zero overlay-blocking across sampled points on Lead/Today in
  Music/Industry/AI/Economy/Society links) plus `document.documentElement.
  scrollWidth`/`clientWidth` overflow check (equal — no page-level
  horizontal overflow; only the nav's own intentionally-scrollable strip
  extends past, matching SUPER_NEWS_SPEC.md's own "nav may horizontally
  scroll" rule) were run against this real rendering. NOTE (documented
  limitation, not fabricated): this Chrome build pins `window.innerWidth` to
  ~500 for JS-observable reads regardless of the requested `--window-size`
  (confirmed via 5 flag-combination attempts) — the RASTER OUTPUT is
  genuinely pixel-accurate to the requested width (proven via file
  dimensions) and 500 is still within the site's own 600px mobile
  breakpoint, so the real mobile CSS is genuinely exercised, but an exact
  JS-level "390" vs "430" distinction could not be independently confirmed
  beyond the screenshot's own file dimensions.
- **Full regression**: `./.venv/Scripts/python.exe -m pytest` (whole suite,
  no path filter) — **1024 passed / 0 failed** (679.68s). Run exactly once,
  after all fixes above.
- **Product score**: 91/100 (rubric: MUSIC intelligence/composer value
  27/30, news selection/ranking 18/20, first-screen quality 14/15, UI/visual
  quality 14/15, editorial quality 9/10, trust/accuracy 9/10). Hard Fail
  count = 0 (checked against every listed Hard Fail condition against the
  actual generated+reviewed report, not assumed).

## 6. PREMIUM INTELLIGENCE UPGRADE PASS — high-leverage quality upgrade,
not a redesign (Lead intelligence gap, Spotify Watch, noise cut, evidence
discipline, inference-distance control)

Long-term editorial principles now in force (persisted per this pass's own
explicit requirement — apply to all future MUSIC work):
- **quality > quantity**; composer/producer usefulness > any reading-time
  target (SPEC section 38, already persisted, reaffirmed here)
- **Spotify is a permanent required watch layer**, never a fixed keyword
  quota, never a giant section
- **FACT / OBSERVATION / SIGNAL / TREND / INTELLIGENCE** evidence
  discipline: a single real citation is an OBSERVATION, 2+ independent real
  citations is a SIGNAL; TREND requires real temporal/repeated evidence this
  single-day synthesis doesn't have, so it is never claimed
- **premium restrained semantic color**: small badges/accents signal real
  evidence strength or category, never a rainbow card background
- **newsletter editorial hierarchy** preserved as-is (this was a leverage
  pass, not a layout rebuild)
- **no filler**: an honest empty/restrained state always beats a padded one
- **TRY must respect evidence level**: a LOW-confidence real insight only
  ever gets WATCH, never a prescriptive TRY/ACTION row

Concrete changes (all additive/tightening, no architecture rebuild):
- **Lead Story intelligence gap FIXED**: `report.web_data_v2.
  resolve_producer_enrichment` (shared by the Lead and Spotify Watch) finds
  a REAL, already-computed Producer Intelligence insight citing the SAME
  real article as evidence when the item has no real LLM `reason` (the
  common real case in this dev DB, whose `reports` marker table is empty) —
  never a new LLM call. The matched insight's own evidence is folded into
  the Lead's MUSIC EVENT EXPOSURE BUDGET identity, so it's correctly
  suppressed from also independently re-appearing as its own Producer/A&R
  card.
- **SPOTIFY WATCH module added**: `report.web_data_v2.spotify_watch_
  candidates` (real title/snippet "spotify" filter over the existing
  SPOTIFY/TIKTOK pool, ranked by the SAME `music_industry_priority_rank`
  scale) + `report.web_render_v2._render_spotify_watch_section` (picks the
  first real qualifying item not already shown as the Lead; honest
  "오늘 확인된 중대한 Spotify 정책·비즈니스 변화 없음." when nothing
  qualifies). New `SPOTIFY` nav link between INDUSTRY and RADAR.
- **Music Industry aggressive noise cut**: `_MUSIC_INDUSTRY_DOWNRANK_
  KEYWORDS` extended (estate disputes, murder/crime, trafficking/assault —
  confirmed real examples seen in the actual generated report: an estate-
  arbitration story, a murder-for-hire trial). NEW quality floor in
  `_merge_music_industry_items`: a real DOWNRANKED item is now excluded
  from Music Industry ENTIRELY (not just sorted to the bottom) — never
  hidden inside "더 보기" either.
- **Producer/A&R inference-distance control**: `_render_producer_takeaway_
  card` now shows WATCH-only (never a prescriptive TRY/ACTION) for a real
  LOW-confidence insight; MEDIUM/HIGH keep the existing combined TRY/WATCH
  row. Same rule applied to the Lead's/Spotify Watch's own enrichment.
- **Evidence-level labeling**: `report.web_render_v2._evidence_level_label`
  — a real, evidence-COUNT-based OBSERVATION (1 citation) vs SIGNAL (2+)
  badge on Genre/Production Radar cards, never a semantic judgment call,
  TREND never claimed (no real temporal data exists to support it).
- **Real image investigation (bounded, no new subsystem)**: confirmed via
  direct DB query that `raw_items.extra_json` is NULL for every row in this
  dev DB (0 of 2904 recent rows have it) — no ingestion has run since the
  image-extraction code was added in an earlier session. NOT a rendering
  bug; correctly renders with no image when none exists. No code change.
- **Dedup**: untouched this pass (no material duplicate defect found in the
  freshly regenerated real report).
- latest targeted result: 206 passed / 0 failed (tests/test_web_render_v2.py
  + tests/test_web_data_v2.py + tests/test_cli_generate_daily_web_report_v2.py)
- final full regression: **1036 passed / 0 failed** (694.20s), exit code 0
- actual generated report reviewed (report_date=2026-08-14): Lead now shows
  real 왜 중요한가/프로듀서 시사점 rows; Spotify Watch correctly honest-empty
  (today's only qualifying Spotify move already IS the Lead); Music
  Industry's "더 보기" count dropped from 13 to 8 real items after the noise
  cut (estate/murder/health/trailer content removed, confirmed absent from
  the whole page); Production Radar shows real OBSERVATION badges; Producer/
  A&R's 2 LOW-confidence cards correctly show WATCH only; MUSIC ≈70% of body
  content vs AI/Economy/Society/Sources ≈30%
- desktop 1440 + true 390px/430px headless-Chrome QA (same method as the
  prior pass, system Chrome binary, no new package): zero document-level
  horizontal overflow with the new Spotify Watch section/nav link/evidence
  badges added; masthead fix from the prior pass still holds
- **product score: 95/100** (MUSIC intelligence/composer value 28/30, news
  selection/ranking 19/20, first-screen editorial value 15/15, UI/newsletter
  professionalism 14/15, editorial language/analysis 9/10, trust/evidence/
  dates/sources 10/10). Hard Fail count = 0.

## 7. FINAL EDITORIAL INTEGRITY FIX — surgical pass only (event cluster
dedup, Spotify Watch honesty, Industry quality floor, Production Radar
domain purity, Producer/A&R quality cap, semantic story-type chips,
real publisher presentation)

User independently reviewed the actual PREMIUM report (section 6's output)
and found 7 concrete real-product defects. This pass fixed ONLY those —
explicitly NOT a redesign, explicitly did NOT rebuild `report.story_
clustering`'s existing event-key architecture.

1. **Event cluster dedup (CRITICAL)** — root cause: `report.web_data_v2.
   _lookup_item_detail` passed the raw ingestion `source_name` straight
   through even for a Google-News-style search-aggregation feed (confirmed
   real example: `tiktok_music_news_google`, which fronts MANY different
   real publishers under one identifier). `report.story_clustering.
   _sources_independent()` requires disjoint `source_names` to permit a
   merge, so near-identical/identical headlines from the SAME aggregator
   feed never cleared that gate regardless of title similarity — the
   Taylor Swift/Trump/TikTok story (17 real rows, 16 via the aggregator)
   and the TikTok Music-on-Stage story both rendered as multiple separate
   visible Industry cards.
   Fix (additive, INPUT-DATA-QUALITY only, `report/web_data_v2.py`):
   `_extract_real_publisher(title, source_name)` — narrowly scoped to
   `source_name` containing "google" — strips the RSS convention's literal
   trailing " - <Publisher>" title suffix and returns the real publisher.
   `_lookup_item_detail` now uses it for both `source_name` and `title`
   (also fixes #7 below). `_prepare_for_clustering` applies the same
   extraction to candidates immediately before `cluster_candidates` runs
   in `_cluster_suppression`, restoring real source-independence for
   aggregator-sourced near-duplicates. A direct RSS feed (the vast
   majority of sources) is completely untouched — verified by test.
   `report/story_clustering.py` itself was NOT modified.
2. **Spotify Watch honesty** — `_render_spotify_watch_section` (`report/
   web_render_v2.py`) previously said "오늘 확인된 중대한 Spotify
   정책·비즈니스 변화 없음." even when the day's strongest qualifying
   Spotify move WAS the Lead Story (excluded only to avoid duplication) —
   semantically false. Now tracks `qualifying_already_lead`; when true,
   shows `_SPOTIFY_WATCH_ALREADY_LEAD_MESSAGE` ("오늘의 주요 Spotify
   변화는 Lead Story에서 다룹니다.") instead. Verified on the actual
   regenerated report: today's real Lead IS the Spotify/Kobalt licensing
   deal, and Spotify Watch correctly shows the new reference message.
3. **Music Industry quality floor** — `_merge_music_industry_items`
   (`report/web_render_v2.py`): a real UNRANKED item (matches none of the
   8 priority classes — ambassador campaigns, routine promotion) now
   additionally requires `source_count >= 2` to survive; a single-source
   unranked item is filtered out entirely. Items matching a real priority
   class (rights/DSP/AI/A&R/revenue/chart/touring/release-strategy) are
   unaffected — this targets filler, not the existing value-ranking system.
4. **Production Radar domain purity** — `_MUSIC_TREND_REJECT_KEYWORDS`
   (`report/web_data_v2.py`) extended with tool-VERSION-launch phrases
   ("studio 2.0", "스튜디오 2.0", "출시했다고 보도", "챗 바를 추가/넣").
   Confirmed real defect: Suno Studio 2.0's MIDI-support launch was
   showing as a Production Radar OBSERVATION merely because its own real
   `interpretation` text speculated about workflow impact using real
   production vocabulary (편곡/믹싱) — now rejected; real tool-news value
   still surfaces via Music Industry's own AI-music priority class
   (confirmed in the regenerated report: shows there with an "AI MUSIC"
   chip instead). The valid Tinashe sampling OBSERVATION is preserved.
5. **Producer/A&R quality cap** — `_render_producer_section` (`report/
   web_render_v2.py`): when any MEDIUM/HIGH-confidence insight exists that
   day, real LOW-confidence insights are dropped from display (a ceiling,
   not a quota) — confirmed the John Summit stadium-scale LOW-confidence
   card no longer appears in the regenerated report (down to the 3
   genuinely strong insights). Falls back to keeping LOW-confidence
   insights only when NOTHING stronger exists that day (never a forced
   empty section).
6. **Semantic story-type chips** — new `_music_story_type_chip_html`
   (`report/web_render_v2.py`), scoped ONLY to Music Industry's own render
   call (`show_story_type=True` passed only from `_render_industry_
   section`) so `music_industry_priority_rank` — a Music-specific
   classifier — never mislabels an AI/Economy/Society card that happens to
   share incidental vocabulary. Maps 4 of the 8 priority classes to a
   small `.story-type-chip` reusing existing theme-aware CSS variables
   (no new colors invented): rights/licensing + A&R → emerald/amber,
   DSP/platform → teal, AI music → cobalt (`--hue-music`/`--hue-music-
   tint2`/`--hue-ai`/`--hue-economy` + existing `--chip-bg`). Revenue/
   chart/touring/release-strategy intentionally get no chip — restrained,
   matches the user's named 6-label palette rather than inventing more.
7. **Source presentation** — resolved as a side effect of #1's
   `_extract_real_publisher`: `_lookup_item_detail`'s `source_name` is now
   the real extracted publisher (e.g. "Digital Music News") instead of the
   raw aggregator feed identifier, for every code path (both the LLM-
   selected `_news_section` and the no-LLM `_raw_fallback_items`
   fallback), since both funnel through this one function. Verified on the
   actual regenerated report's TikTok Music-on-Stage card byline.

Targeted tests added (all real, DB-backed or render-layer, following
existing test conventions — no mocked/fabricated data):
- `test_google_news_aggregator_title_suffix_replaces_source_name_with_real_publisher`
- `test_direct_rss_feed_title_ending_in_dash_words_is_never_altered`
- `test_near_duplicate_from_same_aggregator_feed_now_clusters_via_extracted_real_publisher`
- `test_production_radar_rejects_tool_version_launch_even_with_real_production_vocabulary_in_interpretation`
- `test_producer_quality_cap_drops_low_confidence_insight_when_stronger_ones_exist`
- `test_producer_quality_cap_keeps_low_confidence_insight_when_nothing_stronger_exists`
- `test_music_industry_card_gets_semantic_story_type_chip`
- `test_music_industry_card_without_matching_priority_class_gets_no_chip`
- `test_spotify_watch_excludes_item_already_shown_as_lead` (updated from
  the prior pass to assert the new reference message)
- 5 pre-existing tests updated with `source_count=2` fixtures to reflect
  the new, intentional quality-floor behavior (their own subject —
  merge/cap/pipeline-status/lead-suppression — was unrelated to the floor)

Validation:
- targeted: 214 passed / 0 failed (`tests/test_web_data_v2.py` +
  `tests/test_web_render_v2.py` + `tests/test_cli_generate_daily_web_report_v2.py`)
- ONE real report regenerated (`report_date=2026-08-14`) and directly
  inspected: Taylor Swift cluster → 0 Industry cards + exactly 1 Producer/
  A&R SIGNAL card (7 underlying articles shown only as evidence chips
  inside `<details>`, never as separate cards); Music-on-Stage cluster →
  exactly 1 Industry card ("A&R" chip, "Digital Music News" byline) + 1
  Producer/A&R SIGNAL card; Spotify Watch shows the new reference message
  (today's real Lead IS the Spotify/Kobalt deal); Music Industry down to 4
  real, high-value items (DSP/PLATFORM, AI MUSIC, A&R, and one real
  touring/live-business item); Production Radar down to only the valid
  Tinashe OBSERVATION; Producer/A&R down to exactly 3 real cards, all
  MEDIUM/HIGH confidence
- desktop 1440 + true 390px/430px headless-Chrome QA (same method as prior
  passes): chips render correctly and restrained (no rainbow cards, small
  kicker only) on all 3 widths, no horizontal overflow, no layout breakage
- final full regression: **1044 passed / 0 failed** (723.73s), exit code 0
- **product score: 97/100** (MUSIC intelligence/composer value 29/30, news
  selection/ranking 20/20, first-screen editorial value 15/15, UI/
  newsletter professionalism 15/15, editorial language/analysis 9/10,
  trust/evidence/dates/sources 9/10). Hard Fail count = 0.

## 8. PROFESSIONAL EDITORIAL QUALITY PASS + FINAL DENSITY PASS + FINAL
VISUAL 3-DELTAS (post-section-7, previously undocumented; this section is
the correction — section 7 above was NOT the latest state)

Two further passes landed in the working tree after section 7 with code
comments (`PROFESSIONAL EDITORIAL QUALITY PASS`, `FINAL DENSITY PASS`)
but no CURRENT_STATE.md update at the time. Confirmed present throughout
`report/web_render_v2.py` and `report/web_data_v2.py` (e.g. Lead/Industry/
AI/Economy/Society tiering, Spotify Watch, Producer/A&R quality cap,
ECONOMY/SOCIETY ultra-compact row). A new `report/text_quality.py` (+
`tests/test_text_quality.py`) also landed — LLM-output integrity/fact-check
helper, unrelated to visual presentation.

Then, this session, a **FINAL VISUAL 3-DELTAS** pass was applied on top,
surgical/CSS+CTA-only, no ranking/ingestion/clustering/evidence/DB/LLM
changes:
1. **Music Industry-only bounded cards** — `#section-INDUSTRY .news-card`
   scoped box (background: var(--surface), 1px border, 6px radius, 16px
   padding, 12px gap between cards); `#section-INDUSTRY .news-featured`/
   `.news-compact` overrides added in the same scope to preserve the
   existing Level A > B > C hierarchy inside the new boxed treatment.
   AI/Economy/Society intentionally untouched (still the lighter
   rule-separated treatment).
2. **CTA copy** — `_link_html`'s default label (`web_render_v2.py:227`)
   changed from `"원문 보기 →"` to `"원문 기사 보기 →"`; all 4 call sites
   inherit the default, no per-site changes needed.
3. **AI secondary hierarchy** — `#section-AI .news-title` and
   `#section-AI .news-featured .news-title` scoped to smaller size/lighter
   weight than Music Industry; AI still never gets a boxed card.

**Targeted tests**: `tests/test_web_render_v2.py` +
`tests/test_cli_generate_daily_web_report_v2.py` — **118 passed / 0
failed**. One pre-existing test updated for the new CTA copy
(`test_economy_row_shows_source_date_and_link_on_one_meta_line` + its
sibling absence-assertion). Full regression NOT re-run this pass (CSS/CTA
presentation-only, per explicit instruction — last known full regression
remains section 7's 1044 passed / 0 failed, now stale for the two
undocumented passes above; a fresh full run is still owed before any
release decision).

**Visual QA** (headless system Chrome, `report_date=2026-08-14`, real DB
data): desktop 1440 PASS, mobile 390 PASS, mobile 430 PASS — Industry cards
render boxed with visible A/B/C hierarchy, CTA text correct, AI visibly
lighter than Industry, no horizontal overflow/clipping observed.

**Latest generated QA HTML**: written to a session scratchpad directory
only (NOT `docs/v2/`, which remains untouched at its last-published state)
— ephemeral, not guaranteed to persist across sessions. Treat as already
consumed once reviewed; regenerate before relying on it again.

## 9. CRITICAL UNRESOLVED RUNTIME DEFECT — unintended paid API call

`report.web_data_v2.build_dashboard_data_v2` calls
`report.translation.translate_and_cache` (via `build_translation_provider`)
to translate non-Korean titles. When `ANTHROPIC_API_KEY` is configured in
the environment, this makes a REAL `POST https://api.anthropic.com/v1/
messages` call — contradicting `scripts/generate_daily_web_report_v2.py`'s
own docstring claim that dashboard generation "never calls an LLM."

**One unintended real paid API call already occurred** this session during
a local-data-only regeneration that was believed to be free (the docstring
was trusted instead of verified against the actual call graph first).

**This MUST be eliminated or explicitly guarded (e.g. a real no-network/
cache-only mode, or a pre-flight check that refuses to call out) before any
unattended daily automation/scheduler is enabled.** The user's fixed,
standing requirement: **no silent PAYG/API-credit spending, ever** — every
future regeneration must be checked against this call path first, not
assumed safe from the docstring alone.

## 10. Kakao status

Kakao **authentication/token permissions are already completed** — do not
redo this. `report/kakao_render_v2.py` exists and is tested (render-only).
**Kakao daily delivery, idempotency, retry/logging, scheduler activation,
and final E2E automation are the NEXT implementation goal** — not started
this session, blocked behind explicit user approval (see Safety below) and
behind section 9's API-leak fix (an unattended daily job must not risk
silent paid calls).

## 11. API-LEAK FIX + SUPER NEWS MUSIC/DAILY KAKAO SPLIT + DELIVERY
COMPLETION (this session — §9/§10 both closed)

**§9 API-leak fix**: `report.translation.build_translation_provider()`
now checks a new `SUPER_NEWS_NO_PAID_API` env var FIRST — when truthy
("1"/"true"/"yes"), ALWAYS returns `NullTranslationProvider()` regardless
of `TRANSLATION_PROVIDER`, guaranteeing zero outbound API calls for that
process run without editing `.env`. `scripts/generate_daily_web_report_v2.py`'s
docstring corrected (it previously falsely claimed generation "never calls
an LLM" — it does, via translation, when `TRANSLATION_PROVIDER=anthropic`
+ a real key are configured, which is `.env`'s actual real production
setting). This is a real, legitimate, cached translation feature, not a
bug — the fix is the opt-out guard, not disabling translation.

**SUPER NEWS MUSIC / SUPER NEWS DAILY Kakao split**: two fully independent
daily products, each exactly ONE Kakao message (`report.kakao_render_v2.
render_music_kakao_digest` / `render_daily_kakao_digest`, new), each with
its OWN idempotency key (`report_delivery_v2.MUSIC_REPORT_TYPE=
"SUPER_NEWS_MUSIC_V2"` / `DAILY_REPORT_TYPE="SUPER_NEWS_DAILY_V2"`,
distinct from each other AND from the legacy combined `REPORT_TYPE=
"DAILY_DIGEST_V2"`, which is untouched/still available for manual/audit
use). MUSIC = TikTok/Spotify chart + Early Signal + Music Industry lead
line. DAILY = AI/ECONOMY/SOCIETY lead lines only. Neither ever contains
the other's content (verified by test). New `report_delivery_v2.
deliver_music_digest_v2`/`deliver_daily_digest_v2` — same
NoDashboardDataError/idempotency/content-hash-record contract as the
existing delivery functions, plus new `_send_with_retry` (up to 2 attempts,
3s fixed backoff, every attempt logged with product/date/attempt#/outcome).

**Single daily entrypoint**: `scripts/run_daily_kakao_delivery_v2.py` —
builds `build_dashboard_data_v2` ONCE, sends MUSIC then DAILY, prints
`MUSIC_STATUS=`/`DAILY_STATUS=`, exit 0 only if both ended non-failure.
`--dry-run` flag renders both digest texts and prints them WITHOUT calling
Kakao or touching `runs`/`delivery_history` at all (safe to run any number
of times). `SUPER_NEWS_NO_PAID_API=1` in the environment guarantees the
dry run (or any run) makes zero paid API calls.

**Windows Task Scheduler**: `scripts/register_windows_task.ps1` written
(creates a daily-09:00 task calling the entrypoint above via the project's
own `.venv` python) but **NOT executed/registered this session** — real
OS scheduled-task registration is a system-level, persistent-background-job
change, left for a separate explicit confirmation. Only sends the two
Kakao digests; does not wire ingestion/report-generation into Task
Scheduler (out of scope, `scripts/run_daily_pipeline.sh` remains the only
existing full-pipeline orchestration, itself never scheduled either).

**Verification actually performed, in order**:
1. Targeted tests: 350 passed / 0 failed (`tests/test_translation.py` +
   `tests/test_kakao_render_v2.py` + `tests/test_kakao_render.py` +
   `tests/test_report_delivery_v2.py` + `tests/test_web_data_v2.py` +
   `tests/test_web_render_v2.py` + `tests/test_cli_generate_daily_web_report_v2.py`
   + `tests/test_kakao_token_refresh.py` +
   `tests/test_migration_003_translation_retry_fields.py`) — includes new
   tests for the `SUPER_NEWS_NO_PAID_API` guard, both new digest renderers,
   both new delivery functions (send/skip/fail/retry-then-succeed/
   retry-exhausted/independence-from-each-other), and one pre-existing test
   (`test_generator_never_imports_or_calls_an_llm`) corrected to check real
   imports instead of a blunt prose substring ban that the (now-accurate)
   docstring correction would otherwise trip.
2. Dry run (`SUPER_NEWS_NO_PAID_API=1 ... --report-date 2026-08-14
   --dry-run`, real production DB): both digests rendered correctly within
   the 200-char Kakao limit, real content, zero cross-contamination between
   MUSIC/DAILY, zero `delivery_history`/`runs` rows written, zero API calls
   (guard forced Null translation provider).
3. Real Kakao E2E send (`SUPER_NEWS_NO_PAID_API=1 ...
   --report-date 2026-08-14`, no `--dry-run`, explicit user approval per
   this session's own instruction): **both MUSIC and DAILY sent
   successfully** (`kakao.client` logged "Kakao memo sent successfully."
   twice, `MUSIC_STATUS=sent DAILY_STATUS=sent`). `SUPER_NEWS_NO_PAID_API=1`
   was kept ON for this real send too, per this session's own "no direct
   api.anthropic.com/v1/messages calls" rule — zero paid API calls
   occurred during the real send either.
4. Rerun, same date, same command: both **`skipped_duplicate`** — zero
   second Kakao call (confirmed via log + DB: exactly one `sent` row per
   `report_type` in `delivery_history`, no duplicate). Per-product,
   per-date duplicate-send prevention verified working, independently for
   each product.
5. Full regression: **1062 passed / 0 failed** (743.52s), exit code 0 —
   run exactly once (whole suite, no path filter), after all fixes above,
   because `report/translation.py` (a shared core module `report/
   web_data_v2.py` depends on broadly) was modified. Supersedes section
   7's stale 1044/0 count (up from 1044 because of this session's new
   tests, no regressions).

## 12. FULL UNATTENDED DAILY PIPELINE + WINDOWS SCHEDULER REGISTRATION
(this session — closes the gap §11 left open: the registered scheduler
job now runs the FULL fresh-ingestion-to-Kakao chain, not delivery-only)

**Gap that was closed**: §11's `scripts/run_daily_kakao_delivery_v2.py`
only ever reads whatever is already persisted — a scheduler pointed at it
alone would resend stale/yesterday's data forever if nothing else ever
ingested fresh news. New `scripts/run_daily_full_pipeline_v2.py` is now
the ONE production entrypoint and the ONE Task Scheduler target:

    fresh ingestion (+ normalization/dedup, ingestion.orchestrator's own
    required stage) -> Apple Music KR collection -> Spotify collection
    -> derived VELOCITY signal computation -> SUPER NEWS MUSIC Kakao
    delivery -> SUPER NEWS DAILY Kakao delivery

Each stage is a subprocess call against the ALREADY-EXISTING, unmodified
CLI scripts (`run_daily_ingestion.py` / `run_daily_music.py` /
`run_daily_music_spotify.py` / `run_daily_music_signals.py` /
`run_daily_kakao_delivery_v2.py`) — no second competing pipeline
implementation, no logic duplicated. `scripts/run_daily_pipeline.sh`
(the Linux/systemd bash pipeline, R2 backup + git-publish + legacy
combined-digest included) was inspected and used only as the ordering
reference; it remains untouched and is not what Task Scheduler now runs.

**COST GUARD, enforced inside the scheduled process itself**: the new
script sets `os.environ["SUPER_NEWS_NO_PAID_API"] = "1"` unconditionally
at module import time, before any other import — every subprocess stage
inherits it. This does not depend on Task Scheduler's own launch
environment or on a human remembering to set it first.

**IDENTIFIED BLOCKER — LLM-based intelligence generation is NOT included
in the scheduled chain, on purpose**: `scripts/run_daily_report.py`
(news category LLM selection/synthesis), `run_daily_producer_intelligence.py`,
`run_daily_news_intelligence.py`, and `run_daily_music_trend_intelligence.py`
all call `report.llm_interface.build_llm()` unconditionally whenever the
day has any real candidate data — a real, paid
`POST https://api.anthropic.com/v1/messages` call. Unlike
`report/translation.py`, `llm_interface.build_llm()` has **no
`SUPER_NEWS_NO_PAID_API`-style opt-out today** (confirmed by reading
`report/llm_interface.py`/`report/orchestrator.py`/`music_trend_
orchestrator.py`/`producer_orchestrator.py`/`news_intelligence_
orchestrator.py`: each unconditionally does
`llm_instance = llm if llm is not None else build_llm()` once there's
candidate data), and `build_llm()` supports no local/subscription-safe
provider (`LLM_PROVIDER` other than `'anthropic'` raises `ValueError`).
Per this session's explicit instruction ("if any required daily
intelligence step currently requires direct paid Anthropic API... report
it as a blocker, never make a paid API call"), these four CLIs are
therefore **never invoked** by the new full-pipeline entrypoint or the
registered scheduled task. MUSIC/DAILY Kakao delivery both already have a
real, tested, no-LLM raw-fallback path (`report.candidate_selection`'s
fallback over freshly-ingested `normalized_items`/`music_observations`,
the same path §11's real E2E send already used) — so the daily send is
still genuinely fresh and non-fabricated, just without LLM-curated
category selection/synthesis layered on top. Closing this for real (a
genuine zero-cost synthesis path, or explicit approval for real recurring
daily API spend) is future work, not silently done here.

**--dry-run** threads only into the final Kakao-delivery stage; every
upstream stage (ingestion included) always runs for real — a dry run that
skipped real ingestion would not prove "fresh data", the whole point of
this script.

**Structured logging**: the new script's own `logger.info`/`print` lines
(`STAGE_RESULT stage=<label> status=SUCCESS|FAILED exit=<n>
elapsed=<s>s`) append to the same shared `logs/super_news.log` every
other CLI in this repo already writes to (`logging_setup.setup_logging`).
A REAL BUG was found and fixed while verifying this: the first
implementation decoded each child stage's stdout/stderr as `str` and let
this process's own console/redirect re-encode it, corrupting the Korean
digest text end-to-end (e.g. `8월 16일` → `8�� 16��`) whenever the parent
process's own output codec wasn't UTF-8. Fixed by relaying each child's
raw stdout/stderr **bytes** straight through (`sys.stdout.buffer.write`),
sidestepping the codepage entirely — re-verified via a second fresh
dry-run showing correct Korean output end-to-end.

**Windows Task Scheduler — REGISTERED this session** (explicit user
approval given this turn): `scripts/register_windows_task.ps1` updated to
target the new full-pipeline entrypoint and re-run (the prior session's
delivery-only task was never actually registered, so there was no stale
duplicate to remove). Verified via `schtasks /query`:
  - task name `SuperNewsDailyPipelineV2`, exactly one SUPER NEWS task
    exists (`schtasks /query /fo LIST | Select-String TaskName` matched
    only this one)
  - `Scheduled Task State: Enabled`
  - `Task To Run`: `<repo>\.venv\Scripts\python.exe
    "<repo>\scripts\run_daily_full_pipeline_v2.py"` (absolute paths, the
    project's own venv python)
  - `Start In`: `<repo>` (correct working directory — `config.py`'s
    PROJECT_ROOT-relative paths resolve correctly regardless of Task
    Scheduler's own default working directory)
  - Daily trigger at 09:00 local time, next run 2026-08-17 09:00
  - Runs as a genuine independent OS-level Task Scheduler process — does
    not depend on VS Code, Claude Code, or any terminal session remaining
    open; survives them being closed
  - `Last Result: 267011` ("task has not yet run yet") — expected for a
    freshly-registered task; **not run today**, deliberately, to avoid a
    second real Kakao send for 2026-08-16 on top of the two real dry-run
    verifications already performed (per this session's own explicit
    "do not send another duplicate Kakao message today merely to test
    scheduling" instruction) — tomorrow's 09:00 trigger will be the first
    real unattended run.

**Verification actually performed, in order**:
1. Targeted tests: **10 passed / 0 failed**
   (`tests/test_run_daily_full_pipeline_v2.py`, new — subprocess.run
   mocked throughout, no real network/API/Kakao calls in the test suite
   itself) covering stage ordering, the `SUPER_NEWS_NO_PAID_API` guard
   landing in every child's env, `--db-path` propagation to every stage,
   `--dry-run` reaching ONLY the delivery stage, and exit-code
   aggregation (delivery-stage result wins regardless of upstream
   failures, and an upstream failure never skips a later stage).
2. Fresh dry-run #1 against the REAL production DB (no `--report-date`
   override — today, KST): real ingestion ran across every registered
   source (`normalize_batch complete: {'already_normalized': 2904,
   'normalized': 537}`), one known/expected degraded source
   (`hankyung_economy_rss status=FAILED reason=credential/config failure
   (status=403)`, the same pre-existing issue `run_daily_pipeline.sh`'s
   own comments already document) did not block the rest of the chain;
   real Apple (25 items) + Spotify (10 items) collection; real derived
   signals (24 + 7 written); dry-run MUSIC/DAILY digests rendered
   correctly for `report_date=2026-08-16` (today); exit 0. This run
   surfaced the encoding bug above.
3. Fresh dry-run #2, same command, after the encoding fix: identical
   real behavior, Korean digest text now correct end-to-end
   (`SUPER NEWS MUSIC | 8월 16일`, `TikTok: 데이터 소스 미가동`, `Spotify:
   Shakira - Dai Dai (1위)`, etc.); exit 0.
4. **Fresh-data proof, independently confirmed via direct DB query (not
   just log lines)**: `raw_items` count 2904 → 3443 across the two dry
   runs, `MAX(collected_at)` moved from `2026-08-14T17:32:48Z` to
   `2026-08-16T08:42:51Z`; `delivery_history` for `report_date=2026-08-16`
   is empty (confirming the dry runs truly wrote zero delivery rows).
5. Zero paid Anthropic API calls across both dry runs (no LLM-requiring
   stage was ever invoked; `SUPER_NEWS_NO_PAID_API=1` confirmed present in
   every child stage's own environment via the test suite and via the
   log line `SUPER_NEWS_NO_PAID_API=1` printed at pipeline start).
6. Windows Task Scheduler registration + verification (see above).
7. Full regression: **NOT re-run this pass** — no shared core module was
   modified (only two new files: `scripts/run_daily_full_pipeline_v2.py`,
   `tests/test_run_daily_full_pipeline_v2.py`; plus
   `scripts/register_windows_task.ps1`, a non-Python scheduler script).
   Last known full regression remains §11's **1062 passed / 0 failed**.

## 13. SUBSCRIPTION-SAFE CLAUDE CLI LLM PROVIDER — COMPLETE, INTEGRATED INTO
THE SCHEDULED PIPELINE

**Goal**: close §12's documented gap (LLM-based news selection/synthesis
excluded from the scheduled pipeline because `build_llm()` only supported
paid direct Anthropic API) by adding a subscription-authenticated `claude`
CLI provider. **Done**: all four intelligence stages now run through the
authenticated Claude Code subscription CLI, are wired into
`scripts/run_daily_full_pipeline_v2.py` in production order, and a full
`--dry-run` proved the complete chain end-to-end with zero paid API calls.

**STEP 1 verification (real)**:
- Claude CLI found and working: `C:\Users\<user>\AppData\Roaming\npm\claude`
  (+ `claude.CMD` shim), version **2.1.233 (Claude Code)**.
- With `ANTHROPIC_API_KEY` explicitly removed from the process
  environment: `claude auth status` → `loggedIn: true`, `authMethod:
  "claude.ai"`, `subscriptionType: "pro"` — confirms subscription/OAuth
  auth, not an API key.
- Confirmed native-Windows Python `subprocess.run([...], shell=False)`
  correctly resolves and executes the `claude.CMD` npm shim (via
  `shutil.which`) — no `shell=True` needed.

**STEP 2 implementation**:
- `report/llm_claude_cli.py` — `ClaudeCLIStructuredLLM(StructuredLLM)`:
  subprocess-based (`shell=False`), resolves the executable via
  `shutil.which`/`CLAUDE_CLI_PATH` override, strips `ANTHROPIC_API_KEY`
  from the child env, `--tools ""` (no tool/file/bash permissions, text
  generation only), `--no-session-persistence`, `--system-prompt`
  (replaces the default — no CLAUDE.md leakage into synthesis prompts),
  deterministic timeout (`CLAUDE_CLI_TIMEOUT_SECONDS`, default 180s),
  raises `ClaudeCLIError` (or the `ClaudeCLIRateLimitError` subclass for
  rate-limit/quota-looking failures) on any nonzero exit, timeout,
  malformed JSON, or `is_error: true` — **never falls back to the
  Anthropic SDK on any failure path**.
- **Prompt transport is stdin, never argv**: `-p` is passed with no
  positional prompt argument; the real `user_prompt` goes through
  `subprocess.run(..., input=user_prompt)`. This closed the real bug found
  during validation (below) — Windows' `CreateProcess` argv length limit
  (~32,767 chars) made every real synthesis prompt fail near-instantly
  before this fix.
- `report/llm_interface.py`'s `build_llm()` supports `LLM_PROVIDER=claude_cli`
  (selects the new provider) and, as defense in depth, **refuses to
  construct the paid `AnthropicStructuredLLM` at all when
  `SUPER_NEWS_NO_PAID_API` is truthy** (raises `RuntimeError`), regardless
  of `LLM_PROVIDER` — mirrors `report/translation.py`'s existing guard
  pattern, applied to the LLM factory itself.

**STEP 3 validation — COMPLETE**:
- Targeted tests, no live CLI/API calls (subprocess mocked):
  `tests/test_llm_claude_cli.py` + `tests/test_llm_interface.py` (24
  tests, including large-prompt-via-stdin and prompt-absent-from-argv),
  `tests/test_validation.py`, `tests/test_producer_orchestrator.py`,
  `tests/test_music_trend_orchestrator.py`,
  `tests/test_run_daily_full_pipeline_v2.py` (stage order/count/env for
  the 4 new intelligence stages) — **126 passed / 0 failed** combined.
- **Root cause of the original argv-length bug, confirmed against a real
  stored failure**: `run_category_status.failure_reason` for an earlier
  run recorded `ClaudeCLIError: claude CLI failed to start:
  FileNotFoundError: [WinError 206]` — "the filename or extension is too
  long" — exactly the Windows `CreateProcess` argv-length ceiling, on a
  real ~57,729-character production prompt. **Not an auth failure, not a
  rate-limit failure.** Fixed by the stdin transport above; the identical
  prompt size (and larger, up to ~72,608 chars during full-pipeline runs)
  has since succeeded repeatedly.
- **Two real content-quality defects found via live runs, fixed by
  strengthening the affected system prompts** (both `report/
  producer_synthesis.py` and `report/music_trend_synthesis.py`):
  1. The model sometimes answered in English despite a Korean-only
     instruction buried mid-prompt — fixed by moving an explicit "CRITICAL
     LANGUAGE RULE" to the very first sentence of both system prompts.
  2. The model sometimes returned more insights/signals than the stated
     "up to N" cap — fixed two ways: (a) a "CRITICAL COUNT LIMIT" sentence
     added to both system prompts, and (b) `report/validation.py`'s
     `validate_producer_insights`/`validate_music_trend_signals` now
     **truncate** to the hard max instead of rejecting the whole call when
     the model overshoots (every kept item is still individually validated
     for grounding/language/gibberish exactly as before).
- **A third content-quality behavior observed, treated as the fact-check
  guardrail working as intended, not a bug**: Music Trend Intelligence
  occasionally has the model state a well-known song's real release year
  (pulled from its own training knowledge) that isn't present in that
  day's evidence catalog; `unsupported_fact_tokens` correctly rejects it,
  identical in kind to a real `run_daily_report.py` ECONOMY-category
  rejection observed the same day (an LLM citing an unsupported currency
  figure). An explicit "don't supplement from outside knowledge, even for
  facts you recognize as true" instruction was added to reduce this, but a
  determined model can still occasionally trigger it on very well-known
  catalog entries — this is the intended anti-hallucination behavior, not
  a pipeline defect, and degrades gracefully (that day's section is
  skipped, nothing else in the chain is affected).
- **All four intelligence stages proven live**, `LLM_PROVIDER=claude_cli`,
  `SUPER_NEWS_NO_PAID_API=1`, `ANTHROPIC_API_KEY` unset, against real fresh
  DB data: `run_daily_report.py` (5/6 categories `REPORT_GENERATED`, the
  1 rejection a legitimate fact-check catch), `run_daily_producer_
  intelligence.py` (`completed_with_insights`), `run_daily_news_
  intelligence.py` (`completed_with_insights`, 3/3 validated),
  `run_daily_music_trend_intelligence.py` (`completed_with_signals`).

**STEP 4/5 (pipeline integration, scheduler) — COMPLETE**:
- `scripts/run_daily_full_pipeline_v2.py` now forces both
  `SUPER_NEWS_NO_PAID_API=1` and `LLM_PROVIDER=claude_cli` into its own
  process environment (inherited by every subprocess stage, same pattern
  as the existing cost guard), and runs a new `_INTELLIGENCE_STAGES` block
  — `run_daily_report.py` → `run_daily_producer_intelligence.py` →
  `run_daily_news_intelligence.py` → `run_daily_music_trend_
  intelligence.py` — after `derived_signals` and before `kakao_delivery`.
  Each stage's failure is logged and never aborts the chain, identical to
  every pre-existing upstream stage. No second pipeline implementation —
  the same already-tested, unmodified CLI scripts are reused.
- **One full-pipeline `--dry-run` proved the complete chain**: fresh
  ingestion → normalization/dedup → Apple/Spotify music collection →
  derived signals → report intelligence → producer intelligence → news
  intelligence → music trend intelligence → MUSIC digest → DAILY digest,
  all 9 stages `SUCCESS`, `any_upstream_failure=False`, `delivery_exit=0`.
  MUSIC and DAILY digest text rendered with real curated content (both
  news-category selections and Producer/News/Music-Trend Intelligence);
  `DRY_RUN_NOTE=no Kakao network call made, no delivery_history/runs rows
  written` confirmed. Verified afterward: `delivery_history` gained zero
  new rows from this session's testing (its 4 existing rows all predate
  this session's work).
- `SuperNewsDailyPipelineV2` (Task Scheduler) verified **unchanged**:
  exactly one task named `SuperNewsDailyPipelineV2`, `State=Ready`,
  `Enabled=True`, action still `.venv\Scripts\python.exe
  scripts\run_daily_full_pipeline_v2.py` — no new/duplicate task created.
  The next real scheduled run (2026-08-17 09:00) will now include all four
  LLM intelligence stages for the first time.
- Raw fallback (`report/web_data_v2.py`'s `_raw_fallback_items`) is
  confirmed to activate only when a category has no curated `reports` row
  for the day (empty LLM selections or no report at all) — with §13 now
  wired in, curated intelligence is the normal successful path, and raw
  fallback is reserved for genuine per-category degradation exactly as
  designed.

**Cost safety, verified**: zero direct `api.anthropic.com` calls this
session; `ANTHROPIC_API_KEY` may still be present in the parent process's
own environment (loaded from `.env` when Task Scheduler/a human shell runs
this script), but `report/llm_claude_cli.py` explicitly strips it from
every child `claude` CLI invocation regardless, and `build_llm()` refuses
outright to construct the paid Anthropic client at all while
`SUPER_NEWS_NO_PAID_API=1` — so there is no path from this pipeline to a
real paid API call, confirmed both by code inspection and by every live
run this session.

## NOT YET VERIFIED / NOT DONE

- production web publish NOT approved
- SUPER NEWS MUSIC / SUPER NEWS DAILY have sent real Kakao messages for
  exactly ONE date via the manual entrypoint (2026-08-14, §11) — the new
  scheduled task has NOT executed yet (registered but next run is
  2026-08-17 09:00, deliberately not run today — see §12)
- §13's `claude_cli` LLM provider is DONE and integrated into the
  scheduled pipeline (see §13) — all four intelligence stages proven live
  and wired into `run_daily_full_pipeline_v2.py`; the next real scheduled
  run (2026-08-17 09:00) will exercise this for the first time
  unattended, which has NOT yet been observed (see below).
- the first REAL unattended 09:00 run of the fully-integrated pipeline
  (2026-08-17) has not yet happened/been observed — every verification
  this session was a manual `--dry-run` or standalone intelligence-stage
  invocation, not the actual scheduled trigger
- exact JS-level 390px/430px `window.innerWidth` confirmation remains
  environment-blocked (see prior FAST COMPLETION PASS note) — true
  pixel-accurate RASTER rendering was verified instead

## Next implementation task

§9 (API leak), §10/§11 (Kakao MUSIC/DAILY delivery + idempotency +
retry/logging + single entrypoint + real E2E send + duplicate-block
verification), §12 (full unattended fresh-ingestion-to-Kakao pipeline +
Windows Task Scheduler registration), and §13 (subscription Claude CLI LLM
provider, integrated into the scheduled pipeline) are all COMPLETE. Still
requiring explicit user approval/decision or real-world observation only:
(a) production web publish, (b) observing the first real unattended 09:00
run (2026-08-17) now that it includes all four LLM intelligence stages,
and confirming it behaves the same as the manual `--dry-run` verified in
§13.

If any older artifact (HTML, screenshot, prior doc section) conflicts with
this document, **the current repo/code is source of truth**, not the
artifact.

## Safety
- do not modify V1
- no deletion without explicit approval
- no commit/push without explicit approval
- no .env modification
- no production publish
- scheduler: `SuperNewsDailyPipelineV2` IS now registered and enabled
  (§12, explicit user approval given) — any FUTURE change to its
  schedule/command/target still needs its own explicit approval; removing
  or replacing it is a system-level action, not an ordinary edit
- use project .venv
- no silent PAYG/API-credit spending — `SUPER_NEWS_NO_PAID_API=1` (§11) is
  the real, verified guard for translation; it does NOT cover
  `report.llm_interface.build_llm()` (§12's documented blocker) — never
  add any of the four LLM-based intelligence CLIs to the scheduled chain
  without first either building a real cost-safe path for them or getting
  explicit approval for real recurring daily spend
- real Kakao sending now requires the SAME per-product/date duplicate-guard
  already verified in §11 — never bypass `report_delivery_v2`'s
  idempotency check to force a resend
- green tests != actual product PASS
- user approval required before release
