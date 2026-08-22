#!/usr/bin/env bash
# SUPER NEWS Kakao delivery retry -- retries ONLY the delivery step, never
# collection or report generation, using the V2 MUSIC/DAILY delivery
# idempotency (report_delivery_v2.MUSIC_REPORT_TYPE/DAILY_REPORT_TYPE via
# scripts/run_daily_kakao_delivery_v2.py): a product already sent for this
# report_date is an immediate no-op ("skipped_duplicate", no Kakao call);
# MUSIC and DAILY are independent, so one already-sent/failed product never
# blocks a retry of the other. Triggered by super-news-delivery-retry.timer
# at exactly 3 fixed offsets after the main pipeline (see that unit's
# OnCalendar= lines) -- bounded, not indefinite; there is no 4th attempt.
#
# PRODUCTION INCIDENT FIX (2026-08-22, confirmed real defect): this used to
# `cd /opt/super-news` (the OLD V1 path, hardcoded) and call
# scripts/deliver_daily_report.py (the V1-only, `reports`-table delivery
# CLI, entirely disconnected from V2's MUSIC/DAILY schema) -- so this timer
# fired 3x every day and always no-op'd, unable to ever retry a real V2
# MUSIC/DAILY delivery failure. Fixed to rely on the systemd unit's own
# WorkingDirectory (never hardcode a deploy path here) and call the real V2
# delivery entrypoint.
set -uo pipefail

# Same lock as the main pipeline: if a full pipeline run (which ends with
# its own delivery attempt) is still in flight, skip this tick rather than
# racing it on the DB -- the next scheduled retry slot (if any remain) will
# pick up a still-failed delivery.
exec 200>/tmp/super-news-pipeline.lock
if ! flock -n 200; then
    echo "Main pipeline is currently running -- skipping this retry tick."
    exit 0
fi

output=$(.venv/bin/python3 scripts/run_daily_kakao_delivery_v2.py 2>&1); exit_code=$?
echo "$output"

# Retryable vs non-retryable is logged for observability only -- with just
# 3 bounded attempts total, the cost of a few more clearly-labeled failed
# tries is negligible, so this does not skip or cancel remaining slots.
if [ "$exit_code" -ne 0 ]; then
    if echo "$output" | grep -qE "ReauthRequiredError|AuthorizationCodeError|MissingSecretError"; then
        echo "RETRY_CLASSIFICATION=non-retryable (reauth/config) -- manual intervention required, not just a retry"
    else
        echo "RETRY_CLASSIFICATION=retryable (likely transient) -- will try again at the next scheduled slot, if any remain"
    fi
fi

exit "$exit_code"
