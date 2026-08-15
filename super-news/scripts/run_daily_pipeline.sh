#!/usr/bin/env bash
# SUPER NEWS daily production pipeline: ingestion -> music (Apple +
# Spotify) -> derived signals -> report generation -> Producer
# Intelligence -> News Intelligence -> Music Trend Intelligence (Genre/
# Production/Producer Reference Radar + K-pop-A&R) -> V2.1 dashboard ->
# Kakao delivery, run as ONE ordered execution (not independent timers)
# because each stage depends on the previous one's persisted output.
#
# Deliberately NOT `set -e`: an early stage's non-zero exit must not abort
# later stages. Report generation and delivery are already designed to
# degrade gracefully around missing/partial upstream data (NOT_READY
# categories, a shorter digest) -- skipping them outright on an ingestion
# hiccup would throw away real, useful work (e.g. a MUSIC-only digest can
# still be delivered even if every news source is down).
#
# Stage classification (SUCCESS / DEGRADED / FAILED) is done here by
# grepping each CLI's OWN already-printed per-item status lines -- no
# changes to any of the four Python entry points. KNOWN LIMITATION (V1,
# accepted): this couples the classifier to today's exact print strings
# ("status=FAILED", "status=PARTIAL", "status=REPORT_FAILED"). If those
# scripts' output format ever changes, DEGRADED detection silently stops
# working (falls back to SUCCESS, not FAILED -- total failures are still
# caught via exit code regardless). A machine-readable stage-status
# contract (e.g. each CLI emitting a final JSON summary line) would remove
# this coupling; not built now -- out of scope for this pass, and the
# Python CLIs are not being redesigned here.
#
# "Required" stage total-failure semantics: ALL FOUR stages count as
# required. A TOTAL failure of any one of them (not a routine single-source
# degradation, e.g. the already-known/accepted Hankyung 403 issue, which is
# DEGRADED) makes this script -- and therefore the systemd service -- exit
# non-zero, even if a later stage (e.g. delivery) still succeeds. A
# successful Kakao send must never hide a failed required upstream stage.
set -uo pipefail

# SUPER_NEWS_DIR/SUPER_NEWS_PYTHON: overridable ONLY for testing this script
# itself (Phase 3D) -- unset in every real deployment, so production
# behavior is byte-identical to before (same defaults, same hardcoded
# paths otherwise). Lets a test harness point this script at a fake python
# stub without touching the real systemd-invoked path.
cd "${SUPER_NEWS_DIR:-/opt/super-news}" || exit 1
PY="${SUPER_NEWS_PYTHON:-.venv/bin/python3}"

# Non-blocking lock: skip this invocation entirely if a previous run of
# this same pipeline (or a delivery-retry tick -- see deliver_retry.sh) is
# still in flight, rather than running two copies concurrently against the
# same SQLite DB. Belt-and-suspenders on top of systemd's own guarantee
# that starting an already-active oneshot unit is a no-op.
exec 200>/tmp/super-news-pipeline.lock
if ! flock -n 200; then
    echo "Another pipeline run is already in progress -- exiting."
    exit 0
fi

echo "=== SUPER NEWS daily pipeline: $(date -Iseconds) ==="
any_required_failure=0

# classify OUTPUT EXIT_CODE DEGRADED_GREP_PATTERN -> prints SUCCESS/DEGRADED/FAILED
classify() {
    local output="$1" exit_code="$2" degraded_pattern="$3"
    if [ "$exit_code" -ne 0 ]; then
        echo "FAILED"
    elif echo "$output" | grep -qE "$degraded_pattern"; then
        echo "DEGRADED"
    else
        echo "SUCCESS"
    fi
}

# --- Stage 0: PRE-RUN verified R2 offsite backup ---
# BLOCKING, unlike every other non-required stage below: this is the one
# real safety gate in the whole pipeline. scripts/backup_database.py
# --type pre already performs the full SQLite-consistent-snapshot ->
# local-verify -> real R2 upload -> real remote (head_object) verification
# chain (see db/backup.py/db/r2_client.py, Phase 3D-BACKUP) -- exit 0 here
# means a real, independently-verified offsite copy of TODAY's pre-run DB
# state now exists at a genuinely separate location/account. If that
# didn't happen (BACKUP_INVALID, a real R2 upload failure, or
# R2_CONFIGURATION_REQUIRED -- backup_database.py's own exit codes 1/1/3,
# all non-zero), this script refuses to start ANY DB-mutating stage at
# all and exits non-zero immediately -- unlike Stage 3b/3b2/3c/etc.'s own
# additive-failure-never-blocks precedent, a MISSING verified backup
# really does need to block real production DB mutation from starting,
# per this phase's own explicit instruction. The production DB itself is
# never touched by a failed backup attempt either way (db/backup.py only
# ever reads from it).
echo "--- Stage 0: PRE-RUN verified R2 backup ---"
echo "BACKUP_PRE_START"
backup_pre_out=$($PY scripts/backup_database.py --type pre 2>&1); backup_pre_exit=$?
echo "$backup_pre_out"
if [ "$backup_pre_exit" -eq 0 ]; then
    backup_pre_status="SUCCESS"
else
    backup_pre_status="FAILED"
fi
echo "BACKUP_PRE_RESULT=$backup_pre_status"
echo "STAGE_RESULT backup_pre=$backup_pre_status exit=$backup_pre_exit"

if [ "$backup_pre_status" = "FAILED" ]; then
    echo "CRITICAL: PRE-RUN verified R2 backup failed -- refusing to start any DB-mutating pipeline stage. Production DB was only ever read from, never touched."
    echo "=== SUMMARY backup_pre=$backup_pre_status (pipeline aborted before any DB-mutating stage; ingestion/music/signals/report/producer_intelligence/news_intelligence/music_trend_intelligence/web_v2/delivery/backup_post/capacity_check did NOT run) ==="
    exit 1
fi

echo "--- Stage 1: news ingestion ---"
ingestion_out=$($PY scripts/run_daily_ingestion.py 2>&1); ingestion_exit=$?
echo "$ingestion_out"
ingestion_status=$(classify "$ingestion_out" "$ingestion_exit" "status=FAILED|status=PARTIAL")
echo "STAGE_RESULT ingestion=$ingestion_status exit=$ingestion_exit"
[ "$ingestion_status" = "FAILED" ] && any_required_failure=1

echo "--- Stage 2: music collection (Apple) ---"
music_out=$($PY scripts/run_daily_music.py 2>&1); music_exit=$?
echo "$music_out"
music_status=$(classify "$music_out" "$music_exit" "status=PARTIAL")
echo "STAGE_RESULT music=$music_status exit=$music_exit"
[ "$music_status" = "FAILED" ] && any_required_failure=1

# --- Stage 2b/2c: Spotify collection + derived-signal computation (V2) ---
# NOT required stages: unlike Stage 2 (Apple), a failure here never sets
# any_required_failure -- V1's overall pipeline success/failure signal is
# preserved exactly as before this addition. Recorded for visibility only
# via their own STAGE_RESULT lines, same classify() helper as every other
# stage. Stage 2c must run AFTER 2b (and after Stage 2) since it only reads
# already-persisted observations, never fetches anything itself.
echo "--- Stage 2b: music collection (Spotify) ---"
spotify_out=$($PY scripts/run_daily_music_spotify.py 2>&1); spotify_exit=$?
echo "$spotify_out"
spotify_status=$(classify "$spotify_out" "$spotify_exit" "status=PARTIAL")
echo "STAGE_RESULT spotify=$spotify_status exit=$spotify_exit"

echo "--- Stage 2c: derived signal computation ---"
signals_out=$($PY scripts/run_daily_music_signals.py 2>&1); signals_exit=$?
echo "$signals_out"
signals_status=$(classify "$signals_out" "$signals_exit" "status=PARTIAL")
echo "STAGE_RESULT signals=$signals_status exit=$signals_exit"

echo "--- Stage 3: report generation ---"
report_out=$($PY scripts/run_daily_report.py 2>&1); report_exit=$?
echo "$report_out"
report_status=$(classify "$report_out" "$report_exit" "status=REPORT_FAILED")
echo "STAGE_RESULT report=$report_status exit=$report_exit"
[ "$report_status" = "FAILED" ] && any_required_failure=1

# --- Stage 3b: Producer Intelligence (V2.1) ---
# NOT a required stage, same precedent as Stage 2b/2c: a failure here
# never sets any_required_failure -- Producer Intelligence is a separate,
# best-effort daily addition on top of an already-successful report run
# (see report/producer_orchestrator.py's own docstring), not a required
# part of the daily pipeline's success signal. Must run AFTER Stage 2c
# (derived signals -- Early Signal/Catalog Revival read from there) and
# Stage 3 (report generation -- Music Industry news items read from
# there); report.web_data_v2.build_dashboard_data_v2 is what supplies its
# evidence. Exit-code classification only (SUCCESS/FAILED), matching
# Stage 4's own style below -- this CLI has no DEGRADED/PARTIAL concept
# (see scripts/run_daily_producer_intelligence.py's exit code contract):
# a quiet no-evidence day is still exit 0, only a real synthesis/
# validation failure is exit 1.
echo "--- Stage 3b: producer intelligence ---"
producer_out=$($PY scripts/run_daily_producer_intelligence.py 2>&1); producer_exit=$?
echo "$producer_out"
if [ "$producer_exit" -ne 0 ]; then
    producer_status="FAILED"
else
    producer_status="SUCCESS"
fi
echo "STAGE_RESULT producer_intelligence=$producer_status exit=$producer_exit"

# --- Stage 3b2: News Intelligence (V2.1) ---
# NOT a required stage -- same precedent as Stage 2b/2c/3b: an AI
# synthesis failure or degraded/partial result here NEVER sets
# any_required_failure and NEVER blocks Stage 3c (dashboard generation) or
# Stage 4 (delivery). News Intelligence is a strictly additive layer on
# top of real, already-displayed news -- report/news_intelligence_
# orchestrator.py's own docstring guarantees a synthesis/validation
# failure or partial result never hides the underlying news item, and
# report/news_intelligence_synthesis.py's Phase 3C.3 completeness contract
# guarantees a partial result is never treated as a permanently-cached
# success (it gets a real retry on the next run). Must run AFTER Stage 3
# (report generation -- supplies the real LEAD items via
# report.web_data_v2.build_dashboard_data_v2, the SAME evidence source
# Stage 3b already reads for Producer Intelligence) and BEFORE Stage 3c
# (V2.1 dashboard generation -- report.web_data_v2._attach_news_
# intelligence only ever READS an already-persisted result, it never
# generates one at render time, so this stage must already have run for
# today's AI intelligence to appear at all). Reuses the existing
# production CLI unchanged (scripts/run_daily_news_intelligence.py) -- no
# orchestration logic duplicated here in shell. Runs exactly once per
# pipeline invocation, same as every other stage. The CLI's own exit-code
# contract already maps "failed" to a non-zero exit and everything else
# (including completed_partial) to exit 0 -- classify() below additionally
# flags completed_partial as DEGRADED (visible) even though its exit code
# is 0, matching the classify() pattern every other stage already uses for
# its own degraded-but-not-failed case.
echo "--- Stage 3b2: news intelligence ---"
echo "NEWS_INTELLIGENCE_STAGE_START"
news_intelligence_out=$($PY scripts/run_daily_news_intelligence.py 2>&1); news_intelligence_exit=$?
echo "$news_intelligence_out"
news_intelligence_real_status=$(echo "$news_intelligence_out" | grep -oE 'status=[a-z_]+' | tail -1 | cut -d= -f2)
echo "NEWS_INTELLIGENCE_STAGE_RESULT: ${news_intelligence_real_status:-unknown}"
news_intelligence_status=$(classify "$news_intelligence_out" "$news_intelligence_exit" "status=completed_partial")
echo "STAGE_RESULT news_intelligence=$news_intelligence_status exit=$news_intelligence_exit"

# --- Stage 3b3: Music Trend Intelligence (V2.1) ---
# NOT a required stage -- same precedent as Stage 2b/2c/3b/3b2: a
# synthesis/validation failure here never sets any_required_failure and
# never blocks Stage 3c (dashboard generation) or Stage 4 (delivery).
# Genre Radar / Production Radar / Producer Reference Radar / K-pop-A&R
# relevance (MUSIC INTELLIGENCE COMPLETION phase) is a strictly additive
# layer on top of an already-successful report run, same as Producer
# Intelligence -- report.music_trend_orchestrator.run_daily_music_trend_
# intelligence's own docstring guarantees a quiet no-evidence day is still
# exit 0, and never fabricates a fallback signal. Must run AFTER Stage 3
# (report generation -- report.web_data_v2.build_dashboard_data_v2 is what
# supplies its real Spotify-chart/TikTok-chart/Music-Industry-news
# evidence, the SAME source Stage 3b already reads) and BEFORE Stage 3c
# (V2.1 dashboard generation -- the Trend Radar UI section only ever READS
# an already-persisted MUSIC_TREND_INTELLIGENCE row, it never generates
# one at render time, so this stage must already have run for today's
# Trend Radar to appear at all). Reuses the existing production CLI
# unchanged (scripts/run_daily_music_trend_intelligence.py) -- no
# orchestration logic duplicated here in shell. Exit-code classification
# only (SUCCESS/FAILED), matching Stage 3b's own style: this CLI has no
# DEGRADED/PARTIAL concept either (see scripts/run_daily_music_trend_
# intelligence.py's exit code contract, identical to Stage 3b's).
echo "--- Stage 3b3: music trend intelligence ---"
music_trend_out=$($PY scripts/run_daily_music_trend_intelligence.py 2>&1); music_trend_exit=$?
echo "$music_trend_out"
if [ "$music_trend_exit" -ne 0 ]; then
    music_trend_status="FAILED"
else
    music_trend_status="SUCCESS"
fi
echo "STAGE_RESULT music_trend_intelligence=$music_trend_status exit=$music_trend_exit"

# --- Stage 3c: WEB V2.1 dashboard generation ---
# NOT a required stage, same precedent as Stage 2b/2c/3b/3b3: a failure
# here never sets any_required_failure -- V2.1 is an additive dashboard,
# not a replacement for V1's own generation (scripts/generate_daily_web_
# report.py is untouched and not called from this pipeline in this pass --
# V1 publishing remains its own separate, manual step, unchanged). Runs
# AFTER Stage 3b (Producer Intelligence) and Stage 3b3 (Music Trend
# Intelligence) specifically so today's results, if any, are already
# persisted and show up in the generated page instead of leaving it stale.
# Writes only to docs/v2/ (see the script's own docstring for why that's a
# separate namespace from V1's docs/ root) -- never touches V1's
# docs/index.html or docs/reports/.
echo "--- Stage 3c: web v2.1 dashboard generation ---"
web_v2_out=$($PY scripts/generate_daily_web_report_v2.py 2>&1); web_v2_exit=$?
echo "$web_v2_out"
if [ "$web_v2_exit" -ne 0 ]; then
    web_v2_status="FAILED"
else
    web_v2_status="SUCCESS"
fi
echo "STAGE_RESULT web_v2=$web_v2_status exit=$web_v2_exit"

echo "--- Stage 4: kakao delivery ---"
delivery_out=$($PY scripts/deliver_daily_report.py 2>&1); delivery_exit=$?
echo "$delivery_out"
if [ "$delivery_exit" -ne 0 ]; then
    delivery_status="FAILED"
    any_required_failure=1
else
    delivery_status="SUCCESS"
fi
echo "STAGE_RESULT delivery=$delivery_status exit=$delivery_exit"

# --- Stage 5: POST-RUN verified R2 offsite backup ---
# NOT blocking (main workload already ran, possibly mutating the DB --
# skipping this would just mean today's changes have no offsite copy at
# all, which is worse than trying and failing visibly) -- but a failure
# here IS added to any_required_failure so it's never silently absorbed
# into an otherwise-green pipeline run, per this phase's own explicit
# instruction ("POST_BACKUP_FAILED를 숨기지 않는다"). A POST failure never
# rolls back, deletes, or overwrites the production DB, and never deletes
# the PRE backup or any other existing R2 object -- db/backup.py/
# scripts/backup_database.py have no delete capability at all (Phase
# 3D-BACKUP's own "automatic backup deletion = OFF" invariant). Runs
# AFTER Stage 4 (delivery) so it captures the truly final end-of-day DB
# state, including delivery_history.
echo "--- Stage 5: POST-RUN verified R2 backup ---"
echo "BACKUP_POST_START"
backup_post_out=$($PY scripts/backup_database.py --type post 2>&1); backup_post_exit=$?
echo "$backup_post_out"
if [ "$backup_post_exit" -eq 0 ]; then
    backup_post_status="SUCCESS"
else
    backup_post_status="FAILED"
    any_required_failure=1
fi
echo "BACKUP_POST_RESULT=$backup_post_status"
echo "STAGE_RESULT backup_post=$backup_post_status exit=$backup_post_exit"
if [ "$backup_post_status" = "FAILED" ]; then
    echo "CRITICAL: POST-RUN verified R2 backup failed. Production DB was NOT rolled back, deleted, or modified because of this -- the PRE-RUN backup above (if it succeeded) remains the last known-good verified offsite copy."
fi

# --- Stage 6: R2 capacity check ---
# NOT a required stage -- a capacity-monitoring failure never retroactively
# marks an already-independently-verified PRE/POST backup as failed (that
# verification already happened inside each backup_database.py
# invocation above). Never deletes anything regardless of the reading.
echo "--- Stage 6: R2 capacity check ---"
capacity_out=$($PY scripts/backup_database.py --capacity-only 2>&1); capacity_exit=$?
echo "$capacity_out"
if [ "$capacity_exit" -eq 0 ]; then
    capacity_status="SUCCESS"
else
    capacity_status="FAILED"
fi
echo "STAGE_RESULT capacity_check=$capacity_status exit=$capacity_exit"

r2_alert_level=$(echo "$capacity_out" | grep -oE '^R2_ALERT_LEVEL=.*' | head -1 | cut -d= -f2-)
r2_capacity_forecast=$(echo "$capacity_out" | grep -oE '^R2_CAPACITY_FORECAST=.*' | head -1 | cut -d= -f2-)
capacity_alert_required=0
if [ -n "$r2_alert_level" ] && [ "$r2_alert_level" != "OK" ]; then
    capacity_alert_required=1
fi
if [ "$r2_capacity_forecast" = "CAPACITY_FORECAST_WARNING" ]; then
    capacity_alert_required=1
fi
echo "CAPACITY_ALERT_REQUIRED=$capacity_alert_required"
if [ "$capacity_alert_required" -eq 1 ]; then
    echo "CAPACITY ALERT: R2_ALERT_LEVEL=$r2_alert_level R2_CAPACITY_FORECAST=$r2_capacity_forecast -- surfaced here for a future notification-integration phase (no OS notification/scheduler is wired yet)."
fi

echo "=== SUMMARY ingestion=$ingestion_status music=$music_status spotify=$spotify_status signals=$signals_status report=$report_status producer_intelligence=$producer_status news_intelligence=$news_intelligence_status music_trend_intelligence=$music_trend_status web_v2=$web_v2_status delivery=$delivery_status backup_pre=$backup_pre_status backup_post=$backup_post_status capacity_check=$capacity_status CAPACITY_ALERT_REQUIRED=$capacity_alert_required ==="
exit "$any_required_failure"
