SUPER NEWS — FINAL PRODUCT SPECIFICATION
Version: Premium Newsletter + Music Intelligence

==================================================
0. PRODUCT IDENTITY
==================================================

SUPER NEWS is NOT a generic RSS reader.
SUPER NEWS is NOT a SaaS dashboard.
SUPER NEWS is NOT a simple AI-generated news list.

It is a premium daily editorial newsletter + intelligence product.

PRIMARY PRODUCT:

SUPER NEWS MUSIC
“작곡가·프로듀서를 위한 오늘의 음악 인텔리전스”

SECONDARY PRODUCT:

SUPER NEWS DAILY
“AI·경제·사회의 오늘 핵심만 빠르게”

The web product remains one SUPER NEWS publication,
but MUSIC must clearly dominate the information hierarchy.

The desired reader impression is:

“오늘 편집된 전문 음악 브리핑이 도착했다.”

NOT:

“AI가 뉴스를 긁어서 자동으로 나열했다.”

Design/editorial reference direction:
- premium financial/newsletter hierarchy
- professional music-business intelligence
- editorial judgment
- compact data intelligence
- calm, sophisticated publication feel

Never imitate a specific publication directly.

==================================================
1. HARD PRODUCT PRIORITY
==================================================

Priority:

1. MUSIC
2. AI
3. ECONOMY
4. SOCIETY

MUSIC is the core competitive product.

AI / Economy / Society are supporting daily briefings.

The page must never visually imply that all four categories
have equal importance.

==================================================
2. DESKTOP / MOBILE INFORMATION ARCHITECTURE
==================================================

REMOVE the permanent desktop left sidebar completely.

REMOVE:
- railnav
- shell two-column reading layout
- dashboard-style permanent navigation rail

Use a centered editorial reading column.

Preferred main reading width:
approximately 860–920px.

Use a thin horizontal publication navigation near the masthead:

MUSIC | CHARTS | INDUSTRY | RADAR | PRODUCER | AI | 경제 | 사회

Navigation may horizontally scroll on narrow mobile screens,
but must not create page-level horizontal overflow.

IMPORTANT:
Preserve the literal rendered substring:

MUSIC INTELLIGENCE

because report/release_v2.py uses it as a release marker.

Do not weaken or bypass that release gate.

==================================================
3. MASTHEAD
==================================================

Publication masthead should feel like a real daily briefing.

Example hierarchy:

SUPER NEWS
Daily Music Intelligence
2026.08.16
XX MIN READ

Keep the reading-time estimator honest and deterministic.

Do not artificially reduce reading time.

==================================================
4. FIRST SCREEN / HERO
==================================================

The first screen is extremely important.

REMOVE:
- 1.6fr / 1fr dashboard-style split hero
- large blank-space defect
- equal-weight card grid
- repeated headline and meaning

Use:

TODAY'S MUSIC INTELLIGENCE

LEAD STORY

The lead story contains:

- music category
- large headline
- prominent editorial image when a valid article image exists
- concise factual summary
- WHY IT MATTERS
- PRODUCER IMPACT or WATCH
- source
- article date
- original article link

If the lead is backed by a real article source_url:
the headline must be clickable.

target="_blank"
rel="noopener noreferrer"

If it is synthesis/analysis only and has no real URL:
do NOT make it fake-clickable.

If signals exist but none has is_strongest=True,
the first valid signal must safely become the lead.

Never render an empty lead while valid music signals exist.

==================================================
5. TODAY IN MUSIC
==================================================

Immediately after the lead story:

TODAY IN MUSIC

Maximum:
3 secondary signals.

Each signal:

- category
- headline
- optional small thumbnail
- one concise meaning line ONLY when it genuinely adds information

Do not display meaning when it merely repeats the headline.

If real source_url exists:
headline is clickable.

If analysis-only:
plain text.

Do not show a 4th/5th secondary item in the hero.

==================================================
6. MUSIC EDITORIAL IMAGES
==================================================

Images are allowed primarily to improve MUSIC comprehension
and importance signaling.

This is editorial imagery, not decoration.

IMAGE BUDGET:

Lead story:
maximum 1 large image.

Today in Music:
up to 3 small thumbnails.

Recommended total visible MUSIC editorial images near the top:
approximately 3–4 maximum.

Rules:

- Only use images associated with real article/source items.
- Never fabricate an article image.
- Never show generic placeholder imagery.
- Never attach fake imagery to synthesis-only intelligence cards.
- If no valid image exists, render elegantly without an image.
- Never stretch or distort images.
- Use consistent aspect ratios and object-fit.
- Lazy-load non-critical thumbnails where appropriate.
- Lead image must not overwhelm the actual briefing.
- Mobile imagery must remain compact.
- Never create a cluttered portal/news-aggregator appearance.
- Avoid showing the same image repeatedly for the same event.
- Images must increase comprehension or importance signaling.

AI / Economy / Society remain primarily text-first.
Do not flood those sections with images.

==================================================
7. MUSIC CONTENT ORDER
==================================================

Keep the MUSIC block contiguous.

Recommended order:

TODAY'S MUSIC INTELLIGENCE
↓
TODAY IN MUSIC
↓
CHART PULSE
↓
MUSIC INDUSTRY
↓
GENRE RADAR
↓
PRODUCTION RADAR
↓
PRODUCER / A&R TAKEAWAYS
↓
CROSS-PLATFORM SIGNALS
↓
3–6 MONTH OUTLOOK

Then:

AI
↓
ECONOMY
↓
SOCIETY
↓
SOURCES

==================================================
8. MUSIC NEWS RANKING
==================================================

Music news must be editorially ranked by professional importance.

Priority order:

1. Rights / copyright / publishing / royalties / licensing
2. DSP / streaming platform policy or business model
3. AI music / creator workflow / music technology
4. Label / distribution / A&R / artist-development business
5. Revenue / monetization / streaming economics
6. Consumption / chart / audience behavior changes
7. Touring / ticketing / live-business economics
8. Ordinary artist/event/promotional news

Down-rank:
- celebrity gossip
- minor crime
- personal disputes
- estate trivia
- trailers
- ordinary event announcements
- weak local promotional stories

EXCEPTION:
Do NOT down-rank legal stories merely because they contain
court/lawsuit/legal language.

Copyright, licensing, royalty, contract and business litigation
can be highly important.

==================================================
9. EVENT EXPOSURE BUDGET / CROSS-SECTION DEDUP
==================================================

One underlying event must not dominate the whole product.

Default rule:

one primary exposure
+
at most one genuinely distinct intelligence interpretation.

Examples of unacceptable duplication:

same Suno Studio story appearing independently in:
- Hero
- Today in Music
- Industry
- Production Radar
- Producer
- AI

Same event repeated in 4–6 sections is a HARD FAIL.

Deduplication must be semantic/event-level,
not just exact-title matching.

If an event is used as the lead:
ordinary duplicate news cards should be suppressed or demoted.

A second appearance is permitted only when it provides
a genuinely different producer/business intelligence layer.

==================================================
10. CHART PULSE
==================================================

Chart Pulse is a DATA module and must visually differ from news.

Example:

CHART PULSE

Spotify Global TOP 10 · 2026.08.15 기준

RANK | TRACK | Δ | STATUS

Critical requirement:

REPORT DATE and CHART DATE are separate fields.

Example:

SUPER NEWS 발행일:
2026.08.16

Spotify Global chart date:
2026.08.15

Never assume the chart date from the report date.

Use the real source chart_date / observation date.

If no reliable chart date exists:

show an explicit unavailable/verification state.

Never fabricate a chart date.

First observation:

FIRST OBS.
Δ = —

Description:

“2026년 8월 15일 Spotify Global Daily Chart 첫 관측입니다.
비교 가능한 이전 관측 데이터가 없어 오늘을 기준선으로 설정합니다.
다음 관측부터 순위 변동(Δ)을 표시합니다.”

Future observations must compare against actual prior observation.

==================================================
11. GENRE RADAR
==================================================

Genre Radar is NOT a platform-marketing section.

A Genre signal requires actual evidence of:

- genre
- subgenre
- genre hybrid
- rhythm movement
- sonic movement
- meaningful stylistic movement

Do NOT classify something as a genre trend merely because:

- it is popular on TikTok
- a platform launched a marketing program
- a creator campaign exists
- an artist promoted it

“TikTok Pop” must not be automatically invented from TikTok coverage.

Maximum:
3 strong signals.

If evidence is insufficient:

오늘 검증 가능한 장르 변화 신호 없음

Showing zero is better than fabricating a weak trend.

==================================================
12. PRODUCTION RADAR
==================================================

Production Radar requires real production evidence.

Qualifying evidence includes:

- BPM / tempo
- groove
- rhythm
- drum pattern
- bass
- harmony
- chord movement
- melody
- vocal production
- arrangement
- structure
- intro
- hook
- sample technique
- sound palette
- dynamics
- mix characteristics

A creator-tool announcement by itself does NOT qualify.

Example:

“Suno Studio 2.0 supports MIDI”

is creator-tool/product news.

It is NOT automatically a production-characteristic signal.

Show 0 or 1 item if that is all the evidence supports.

Never fabricate detailed production analysis beyond source evidence.

==================================================
13. PRODUCER / A&R
==================================================

This is one of SUPER NEWS MUSIC's highest-value sections.

Maximum primary visible cards:
3.

Preferred structure:

SIGNAL
SO WHAT
TRY or WATCH

Prioritize direct songwriter/producer value:

- music workflow
- rights / royalties
- monetization
- pitching
- A&R
- release strategy
- production strategy
- platform opportunity
- creator-tool implications

Control inference distance.

Do NOT infer detailed arrangement/mixing instructions
from weak evidence such as venue size or general popularity.

==================================================
14. MUSIC INDUSTRY
==================================================

Music Industry can contain more stories than other sections,
but ranking quality matters more than volume.

Do not bury high-value stories underneath celebrity/event stories.

For example:
licensing / publishing / DSP / AI music workflow changes
should generally rank above ordinary event promotion.

Keep source/date/original article link visible but compact.

==================================================
15. AI
==================================================

AI is important but SECONDARY to MUSIC.

AI may have somewhat richer presentation than Economy/Society.

Focus on:
- major model/platform changes
- creator workflow
- regulation/policy
- major business implications
- meaningful product changes

Avoid excessive minor product announcements.

==================================================
16. ECONOMY
==================================================

Maximum primary visible stories:
5.

Ultra-compact format:

headline
source · date · 원문 보기

No giant summaries.
No article body.
No archive-like dump.

Headline clickable when real URL exists.

==================================================
17. SOCIETY
==================================================

Maximum primary visible stories:
5.

Same compact format:

headline
source · date · 원문 보기

Prefer nationally meaningful / high-impact stories.

Down-rank:
- trivial local administration
- minor promotional corporate stories
- low-impact lifestyle filler

Never fabricate importance.

==================================================
18. KOREAN EDITORIAL QUALITY
==================================================

Natural professional Korean prose.

Preserve official names where appropriate:

Spotify
Apple Music
Suno Studio 2.0
Music on Stage
Writer
Ticketmaster
Gemini
KATSEYE
A&R
BPM
etc.

Do not awkwardly translate official product/company names.

Fix Korean particles and grammar.

Examples:

IBM가 → IBM이

Avoid awkward machine-translated fragments.

Do not expose:
- E11-style internal IDs
- internal source references
- raw RSS fragments
- debug/status strings
- internal prompts
- malformed snippets

==================================================
19. SOURCE / PUBLISHER CLEANUP
==================================================

Normalize visible source attribution.

Avoid duplicated strings such as:

Digital Music News — ... Digital Music News

If Google News is acting as an aggregator
and the real underlying publisher is reliably known,
prefer the real publisher.

Never fabricate a publisher.

If uncertain:
use the actual available source truthfully.

==================================================
20. EVIDENCE / TRUST
==================================================

Visible editorial summaries must be concise.

Detailed evidence may remain collapsed.

Trust gate order:

1. dedupe
2. source/trust check
3. date verification
4. number verification
5. proper-name verification
6. corroboration when necessary
7. translation/text cleanup
8. final UI rendering

Unknown or unverified must never silently become PASS.

==================================================
21. MOBILE LINK CONTRACT
==================================================

The user previously reported:

“모바일에서는 뉴스 안 열려”

Treat this as a real defect until interaction testing proves otherwise.

All real external article links:

target="_blank"
rel="noopener noreferrer"

Make the HEADLINE itself clickable,
not only a tiny “원문 보기” text.

Practical mobile tap area should be approximately 44px where reasonable.

No overlay or CSS layer may block tapping.

No QA iframe behavior may falsely make working links appear dead.

==================================================
22. MOBILE QA
==================================================

Actual browser interaction QA is mandatory at:

1440px desktop

390px mobile

430px mobile

At BOTH mobile sizes, actually click-test at least:

- Lead Story or Today in Music
- Music Industry
- AI
- Economy
- Society

Verify:

1. click event registers
2. new tab/window opens when expected
3. resulting URL is non-empty
4. resulting URL corresponds to the expected real article/source
5. no overlay blocks interaction
6. no horizontal page overflow
7. navigation remains usable

HTML attribute inspection alone is NOT sufficient.

==================================================
23. ACCESSIBILITY
==================================================

Maintain readable text contrast.

Avoid overly faint tiny metadata.

Ensure:
- visible keyboard focus states
- semantic heading hierarchy
- image alt text where meaningful
- sensible touch target sizes
- no horizontal page overflow
- links visually identifiable
- mobile font sizes remain readable

==================================================
24. VISUAL DESIGN
==================================================

Background:
#F7F6F2

MUSIC:
deep emerald approximately #0F6E4F
with restrained teal secondary accents

AI:
cobalt approximately #2F5AA8

ECONOMY:
muted gold / amber

SOCIETY:
muted burgundy

SOURCES:
slate

Use colors in:
- labels
- rules
- small accents
- data status
- section identifiers

Do NOT:
- fill entire sections with saturated colors
- create rainbow SaaS cards
- create heavy gradients
- overuse rounded cards
- make the page feel like an analytics dashboard

Editorial whitespace is desirable.
Large accidental blank holes are not.

==================================================
25. SUPER NEWS MUSIC KAKAO
==================================================

Create a separate Kakao daily notification product:

SUPER NEWS MUSIC
“작곡가·프로듀서를 위한 오늘의 음악 인텔리전스”

Suggested compact structure:

🎵 SUPER NEWS MUSIC — 8.16

Lead:
[오늘 가장 중요한 음악 신호]

• Music signal
• Music signal
• Chart / Producer signal

🎛 Producer Watch:
[very short actionable intelligence]

CTA:
전체 MUSIC INTELLIGENCE 보기

This is a notification/executive summary.
Do NOT send the whole newsletter body through Kakao.

==================================================
26. SUPER NEWS DAILY KAKAO
==================================================

Separate second Kakao message:

SUPER NEWS DAILY
“AI·경제·사회의 오늘 핵심만 빠르게”

Compact structure:

📰 SUPER NEWS DAILY — 8.16

AI
[1–2 key signals]

경제
[1–2 key signals]

사회
[1–2 key signals]

CTA:
전체 DAILY BRIEF 보기

Do not mix this back into MUSIC Kakao.

==================================================
27. KAKAO IDEMPOTENCY
==================================================

MUSIC and DAILY delivery states are independent.

Conceptually:

music_sent_YYYY-MM-DD
daily_sent_YYYY-MM-DD

A successful MUSIC send must not cause DAILY to be skipped.

A failed DAILY send must not cause MUSIC to resend.

Exactly one successful send per product per report_date.

Do not resend after success.

==================================================
28. WEB / KAKAO RELATIONSHIP
==================================================

Web remains ONE SUPER NEWS publication.

MUSIC is the dominant upper product.

AI/ECONOMY/SOCIETY form the DAILY supporting block.

Kakao is split into TWO entry notifications:

SUPER NEWS MUSIC
SUPER NEWS DAILY

Both should deep-link to the appropriate same-date web location
or same-date public V2 page.

==================================================
29. COST / PROVIDER SAFETY
==================================================

Before final daily scheduling:

inspect the provider strategy.

Do NOT silently create a daily workflow that incurs separate
Anthropic API PAYG charges.

Claude Pro subscription and Anthropic API billing are separate.

Do not expose API keys.

Do not modify .env without explicit approval.

If external API spend is required,
report it before scheduler activation.

==================================================
30. DO NOT TOUCH
==================================================

Do not:
- modify V1
- delete files without explicit approval
- rm QA directories
- reset/clean git
- force push
- commit without explicit approval
- push without explicit approval
- modify .env
- expose credentials
- publish production prematurely
- send real Kakao prematurely
- activate scheduler prematurely
- weaken release gates just to make tests pass
- fabricate missing data

==================================================
31. TEST DISCIPLINE
==================================================

Use the project virtual environment explicitly:

./.venv/Scripts/python.exe

Do not accidentally use:
- Hermes Python
- global Python
- another Python environment

Implementation flow:

A. targeted implementation
B. targeted tests
C. fix all failures
D. regenerate real QA artifact
E. browser QA
F. interaction/click QA
G. fix actual product defects
H. final full regression ONCE

Do not repeatedly run the entire suite during development.

Tests must reflect the intended product contract,
not obsolete sidebar/dashboard behavior.

Do not simply weaken tests to get green.

==================================================
32. ACTUAL PRODUCT QA
==================================================

A green test suite does NOT establish product PASS.

Generated real HTML must be inspected.

Use a real report date/artifact.

Inspect:

- first-screen hierarchy
- newsletter feeling
- lead quality
- image quality
- chart date
- music story ranking
- cross-section dedupe
- Genre Radar validity
- Production Radar validity
- Producer/A&R usefulness
- Korean/editorial polish
- source attribution
- mobile behavior
- link behavior
- Economy/Society caps

==================================================
33. FINAL QUALITY SCORE
==================================================

Score the ACTUAL generated product:

MUSIC Intelligence ........ 30
News selection quality .... 20
First-screen value ........ 15
UI/UX ..................... 15
Editorial quality ......... 10
Trust / accuracy .......... 10

TOTAL ..................... 100

Release target:

>= 90 / 100
AND
Hard Fail count = 0

==================================================
34. HARD FAIL CONDITIONS
==================================================

Any of the following prevents final approval:

- Economy >5 primary visible stories
- Society >5 primary visible stories
- giant raw article body
- giant related-event cluster
- MUSIC feels secondary
- Producer value effectively absent
- internal system status dominates
- repeated “AI 해석 대기”
- raw English RSS lists
- empty states dominate the page
- duplicate chart/news
- broken Korean
- incorrect proper names
- incorrect dates
- incorrect numbers
- chart date fabricated or missing when required
- desktop forced into mobile-width layout
- mobile horizontal overflow
- important mobile links do not actually open
- event duplicated across many sections
- Genre Radar using platform marketing as fake genre evidence
- Production Radar using creator-tool news as fake production evidence
- product looks like an ordinary RSS/news portal
- product looks like a SaaS dashboard
- image spam/clutter
- fake/placeholder article imagery
- publication released despite known failing tests
- code PASS claimed without inspecting actual generated output

==================================================
35. FINAL QA PASSES
==================================================

Perform three product-review passes before final approval.

PASS 1 — FUNCTIONAL
- rendering
- links
- navigation
- responsive behavior
- chart dates
- source/date display
- images
- no runtime errors

PASS 2 — EDITORIAL
- lead story quality
- ranking
- duplication
- Genre validity
- Production validity
- Producer usefulness
- Korean quality
- source quality

PASS 3 — PRODUCT
Ask:

“Does this feel like a premium daily music newsletter
and professional intelligence product?”

“Would a songwriter/producer gain something here
that an ordinary news portal does not provide?”

“Is the first screen immediately useful?”

If no:
fix it before declaring completion.

==================================================
36. FINAL RELEASE GATE
==================================================

Do not publish solely because tests are green.

Do not send Kakao solely because generation succeeded.

Do not enable daily scheduler solely because an API returned 200.

Required final sequence:

targeted tests = PASS
↓
real report generated
↓
desktop/mobile visual QA = PASS
↓
real mobile click QA = PASS
↓
content/editorial QA = PASS
↓
full regression = PASS
↓
actual product score >=90
↓
Hard Fail = 0
↓
USER APPROVAL
↓
only then production publish / Kakao / scheduler work

==================================================
37. FINAL RETURN FORMAT
==================================================

Return a concise factual completion block.

Do not claim PASS for anything not actually verified.

FILES_CHANGED=
TARGETED_TESTS=
FULL_REGRESSION=
REAL_REPORT_DATE=
REAL_REPORT_PATH=
DESKTOP_1440_QA=
MOBILE_390_QA=
MOBILE_430_QA=
MOBILE_CLICK_TESTS=
LEAD_STORY=
MUSIC_IMAGES=
CHART_DATE=
MUSIC_RANKING=
EVENT_DEDUP=
GENRE_RADAR=
PRODUCTION_RADAR=
PRODUCER_AR=
ECONOMY_CAP=
SOCIETY_CAP=
KOREAN_EDITORIAL=
SOURCE_TRUST=
SUPER_NEWS_MUSIC_KAKAO=
SUPER_NEWS_DAILY_KAKAO=
ACTUAL_PRODUCT_SCORE=
HARD_FAIL_COUNT=
READY_FOR_USER_REVIEW=

Never report READY_FOR_USER_REVIEW=true unless the actual
generated product has passed all applicable gates above.

==================================================
38. LATEST PRODUCT DECISION ADDENDUM (value over reading time)
==================================================

SUPER NEWS MUSIC is NOT optimized for a 10-15 minute reading target.

PRIMARY OBJECTIVE:

MAXIMUM PRACTICAL VALUE TO A PROFESSIONAL COMPOSER / PRODUCER.

Quality > quantity.
Usefulness > reading time.
Editorial judgment > fixed article count.

SUPER NEWS MUSIC must feel like:

PREMIUM PROFESSIONAL MUSIC-INDUSTRY NEWSLETTER
+
COMPOSER / PRODUCER INTELLIGENCE

No filler.

Today in Music max 3 means MAXIMUM, not mandatory -- a thin real day
correctly shows fewer, never padded to hit a count.

This does not contradict section 3's honest reading-time estimator
(never fake, never artificially reduced) -- the estimator still reports
the real time it takes to read whatever real content genuinely exists;
it is simply never a target content volume is padded or trimmed to hit.

Professional visual direction:
- MUSIC dominant deep emerald / teal identity
- premium editorial typography
- deliberate hierarchy
- generous but controlled whitespace
- restrained use of professional color
- real article imagery only where useful
- Lead max 1 image
- Today in Music max 3 thumbnails
- no image spam
- no generic placeholder
- no fabricated/AI news imagery

For major stories, when evidence supports it:

WHAT HAPPENED
WHY IT MATTERS
PRODUCER IMPACT
WHAT TO WATCH
ACTION / TRY

Never fabricate ACTION / TRY -- WATCH only when there is no real
evidence-based action.
