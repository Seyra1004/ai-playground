#!/usr/bin/env bash
# SUPER NEWS Kakao delivery retry -- retries ONLY the delivery step, never
# collection or report generation, using the existing delivery idempotency
# (report_delivery.decide_delivery_action): a report_date that's already
# been sent makes this an immediate no-op ("skipped_duplicate", exit 0, no
# Kakao call). Triggered by super-news-delivery-retry.timer at exactly 3
# fixed offsets after the main pipeline (see that unit's OnCalendar= lines)
# -- bounded, not indefinite; there is no 4th attempt.
set -uo pipefail

cd /opt/super-news || exit 1

# Same lock as the main pipeline: if a full pipeline run (which ends with
# its own delivery attempt) is still in flight, skip this tick rather than
# racing it on the DB -- the next scheduled retry slot (if any remain) will
# pick up a still-failed delivery.
exec 200>/tmp/super-news-pipeline.lock
if ! flock -n 200; then
    echo "Main pipeline is currently running -- skipping this retry tick."
    exit 0
fi

output=$(.venv/bin/python3 scripts/deliver_daily_report.py 2>&1); exit_code=$?
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
