#!/usr/bin/env bash
# SUPER NEWS daily production pipeline: ingestion -> music -> report
# generation -> Kakao delivery, run as ONE ordered execution (not four
# independent timers) because each stage depends on the previous one's
# persisted output.
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

cd /opt/super-news || exit 1
PY=.venv/bin/python3

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

echo "--- Stage 1: news ingestion ---"
ingestion_out=$($PY scripts/run_daily_ingestion.py 2>&1); ingestion_exit=$?
echo "$ingestion_out"
ingestion_status=$(classify "$ingestion_out" "$ingestion_exit" "status=FAILED|status=PARTIAL")
echo "STAGE_RESULT ingestion=$ingestion_status exit=$ingestion_exit"
[ "$ingestion_status" = "FAILED" ] && any_required_failure=1

echo "--- Stage 2: music collection ---"
music_out=$($PY scripts/run_daily_music.py 2>&1); music_exit=$?
echo "$music_out"
music_status=$(classify "$music_out" "$music_exit" "status=PARTIAL")
echo "STAGE_RESULT music=$music_status exit=$music_exit"
[ "$music_status" = "FAILED" ] && any_required_failure=1

echo "--- Stage 3: report generation ---"
report_out=$($PY scripts/run_daily_report.py 2>&1); report_exit=$?
echo "$report_out"
report_status=$(classify "$report_out" "$report_exit" "status=REPORT_FAILED")
echo "STAGE_RESULT report=$report_status exit=$report_exit"
[ "$report_status" = "FAILED" ] && any_required_failure=1

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

echo "=== SUMMARY ingestion=$ingestion_status music=$music_status report=$report_status delivery=$delivery_status ==="
exit "$any_required_failure"
