# SWIPE_INFO SOCIAL AUTOMATION — PROJECT RULES

## 1. Product

SWIPE_INFO is a Korean practical-information magazine for Instagram and Threads.

Core promise:
"지금 알아야 돈과 시간을 지키는 정보"

Content must provide at least one concrete value:
- save money
- prevent financial loss, scams, or consumer harm
- save time
- prevent users from missing benefits

Do not produce:
- generic news summaries
- obvious/common-sense tips
- unverified information
- clickbait without practical value
- content that does not tell the user what to do

Primary audience spans roughly ages 20–60.
Writing must be simple enough for a broad non-expert audience while preserving important details.

## 2. One Source, Multiple Platforms

Instagram and Threads MUST share one verified content source.

Pipeline:

topic discovery
→ scoring
→ research
→ fact sheet
→ claim verification
→ canonical content object
→ Instagram adapter
→ Threads adapter

Never independently research the same topic twice for Instagram and Threads.

Platform-specific copy may differ, but facts must come from the same verified fact sheet.

## 3. Development Principles

Priority:
1. Python / deterministic code
2. existing verified scripts and cached outputs
3. Shell / PowerShell / Git / SQL
4. LLM only when semantic judgment is genuinely necessary

Rules:
- Prefer Python.
- Minimize LLM calls.
- Never use an LLM for deterministic work that code can perform reliably.
- Reuse verified cached outputs when inputs/config have not changed.
- Do not reread large files unnecessarily.
- Prefer targeted search, grep/rg, git diff, line ranges, SQL and Python inspection.
- Batch related inspections where safe.
- Make small, reversible, isolated changes.
- Do not modify unrelated code.
- If one pipeline stage fails, rerun only that stage and required downstream stages.
- Never restart the whole pipeline unnecessarily.

## 4. Testing

FULL TEST SUITE IS FORBIDDEN BY DEFAULT.

- Test only changed components and directly affected behavior.
- Do not rerun already-passed tests unless relevant code changed.
- Broader tests are allowed only when shared/core code changed or there is evidence of wider regression risk.
- Actual observable output overrides internal PASS flags.

## 5. Topic Discovery

Collect at least 5 viable Korean topic candidates per production cycle.

Weekly categories are editorial guidance, NOT hard scheduling:

- Monday: benefits users may miss this week
- Tuesday: unnecessary recurring expenses
- Wednesday: hospital / health insurance / insurance claims
- Thursday: government policy / benefits / deadlines
- Friday: finance / telecom / subscriptions
- Saturday: scams / consumer harm prevention
- Sunday: upcoming changes / faster life procedures

Urgent high-value topics override the normal editorial guide.

Examples:
- imminent application deadline
- major refund/support program
- important policy change
- widespread consumer harm
- new scam affecting many people

## 6. Topic Scoring

Score candidates out of 100:

- timeliness / urgency: 25
- practical money, loss, time or benefit value: 25
- population reach: 15
- ability to verify with authoritative sources: 15
- expected save/share value: 10
- duplication and content-axis balance: 10

Use deterministic scoring wherever possible.

Do not select a topic merely because it has the highest relative score if evidence quality is insufficient.

## 7. Source Priority

Prioritize primary and authoritative sources:

- Korean government ministries
- 복지로
- 정부24
- 국민건강보험공단
- 금융감독원
- 금융위원회
- 한국소비자원
- 경찰청
- 검찰청
- 국세청
- local-government official websites
- relevant public institutions
- official laws
- official notices
- official press releases
- official service/business operators when applicable

News/media may be used for:
- topic discovery
- context
- finding original sources

News articles must not normally be the sole evidence for critical claims when an authoritative primary source exists.

## 8. Fact Sheet

For the selected topic create a structured fact sheet containing:

- what the policy/event/system is
- who is eligible/affected
- how much users can save/receive
- application/action deadline
- requirements
- exclusions
- application/action method
- required documents
- exceptions and warnings
- official sources
- source publication dates
- last verification timestamp
- information likely to change
- image usage basis

Volatile information must be rechecked on the production date.

## 9. Claim-Level Verification

Important claims must be linked to evidence.

Critical claims include:
- eligibility
- amount
- benefit
- deadline
- requirements
- exclusions
- required documents
- application/action method

Store:

CLAIM
→ SOURCE_ID
→ SOURCE_TYPE
→ PUBLISHED_AT
→ VERIFIED_AT
→ VERIFICATION_STATUS

Whenever possible, cross-check using 2 or more independent authoritative sources.

Do not count duplicated copies of the same underlying announcement as independent confirmation.

If an important claim cannot be officially verified:
→ reject the topic
→ move to the next candidate

If authoritative sources materially conflict and the conflict cannot be resolved:
→ NEEDS_REVIEW
→ do not automatically publish the disputed claim

## 10. Carousel Page Count

Instagram carousel length is NOT fixed.

Allowed range:
4–8 pages.

Choose page count based on actual information density.

General guidance:
- 4P: simple topic
- 5P: normal topic
- 6P: multiple conditions or exceptions
- 7P: substantial comparison/process/eligibility information
- 8P: complex policy, insurance, tax, benefits or similarly dense topic

Never expand weak information just to create more pages.
Never compress useful information merely to reduce page count.

## 11. Carousel Story Structure

Page 1:
Concrete benefit/loss-focused hook.

Page 2:
Why the reader needs to know this now.

Middle pages:
Use only the sections required by the topic:
- eligibility
- amount
- conditions
- comparison
- exclusions
- procedure
- warnings
- examples

Final page:
Clear action:
- check
- apply
- prepare
- save
- share with family
- take another concrete next step

Every page must have a distinct role.

Reading only the page headlines in sequence should communicate the overall story.

Do not use the formal policy/program name as the primary hook when the user's practical gain/loss can be stated instead.

## 12. Visual Rules

Canvas:
1080×1350 px
4:5 vertical

Use:
config/brand.yaml

The renderer must treat brand.yaml as the design source of truth.

Every page must contain:
- readable text
- a visual directly relevant to that page's information

Possible visuals:
- relevant photo
- official screenshot where legally/appropriately usable
- chart
- diagram
- comparison
- iconographic information
- generated supporting visual

Do not repeatedly use decorative stock images with no informational relevance.

AI image generation and Korean typography must be separated.

AI:
→ image/background/illustration only

Renderer:
→ Korean typography/layout

Never rely on image generation models to render final Korean text.

## 13. Brand Consistency

Use the SWIPE_INFO brand system defined in config/brand.yaml.

Core palette:
- #7848D8
- #A848F0
- #F04890
- #F06078
- #F09060
- text #241B31

Backgrounds:
- white
- light lavender
- light pink
- light peach

Rules:
- maximum 2 strong colors per page
- gradients only in narrow accents such as underline, page number or accent line
- do not randomly redesign the brand every day
- daily variation may use photo, layout variant and accent color
- maintain recognizable SWIPE_INFO identity

Typography:
Pretendard.

## 14. Readability

Design for broad 20–60s readability.

- avoid tiny text
- avoid unnecessarily long paragraphs
- emphasize important amounts, dates, deadlines and eligibility
- maintain strong hierarchy
- maintain safe margins
- verify mobile-size readability
- zero text clipping
- zero overflow
- zero broken Korean typography

## 15. QA

Before completion run targeted QA for:

FACT QA
- claim/source linkage
- eligibility
- amounts
- dates
- deadlines
- exclusions
- volatile information freshness

READABILITY QA
- text size
- paragraph density
- clipping
- overflow
- mobile readability

DESIGN QA
- brand consistency
- spacing
- alignment
- image relevance
- visual hierarchy
- page-to-page consistency

ACTION QA
- reader understands why it matters
- reader understands whether it applies to them
- reader understands what to do next

EDITORIAL QA
- no unsupported exaggeration
- no misleading certainty
- no empty clickbait
- no unnecessary jargon

## 16. Automatic Repair

When QA fails:

1. identify the exact failing stage/page
2. repair only the failing scope
3. rerun only the relevant QA
4. maximum 2 automatic repair attempts

After 2 failed repair attempts:
→ NEEDS_REVIEW

Never force a low-quality result into COMPLETE status.

Report the reason for delay/failure instead.

## 17. Quality Baseline

`병원비_환급금_완성본_v2` is the current minimum reference quality.

Do not mark a carousel COMPLETE when it is clearly inferior in:
- readability
- information density
- visual polish
- information-to-image relevance
- editorial usefulness

Where possible, enforce measurable properties deterministically.
Use human/LLM judgment only for genuinely subjective visual/editorial evaluation.

## 18. Instagram + Threads Output

One verified fact sheet feeds both platforms.

Instagram output:
- 4–8 page carousel
- caption
- relevant assets
- QA report

Threads output:
- platform-appropriate concise text/thread
- same verified facts
- appropriate CTA

Do not simply copy Instagram carousel text into Threads.

## 19. Publishing

Primary production integration:
Meta official APIs.

Browser automation:
FALLBACK ONLY.

Use Playwright/Selenium/UI automation only when the required operation cannot be performed reliably through the official API.

Do not enable automatic public posting during early MVP development without explicit approval.

## 20. Production Safety

- Never expose or commit secrets.
- Never enable paid APIs without explicit approval.
- Protect .env, tokens, credentials, databases and backups.
- Preserve rollback points.
- Stage only intended Git files.
- Never include unrelated changes in commits.

## 21. Definition of Done

Code written != task complete.

A content package is COMPLETE only when the actual affected path has been verified with appropriate evidence.

Expected production flow:

official Korean sources
→ 5+ candidates
→ scoring
→ best viable topic
→ fact sheet
→ claim-level verification
→ 4–8P structure
→ Instagram + Threads adaptation
→ 1080×1350 rendering
→ targeted QA
→ maximum 2 scoped repairs
→ final review package

Final review package must contain:
- completed carousel
- Instagram caption
- Threads copy
- fact sheet
- internal source/evidence report
- QA result

Do not claim success based solely on internal PASS flags.
Actual observable results are the final authority.