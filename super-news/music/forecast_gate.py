"""3-6 month genre/style forecasting -- minimum-data GATE only.

Real forecasting requires months of real accumulated observation history
that does not exist yet (SUPER NEWS's music collection began 2026-08-12).
This module reports INSUFFICIENT_HISTORY honestly instead of ever
fabricating a forecast from thin data. The actual forecasting computation
(monthly_forecasts table, already schema-ready and unpopulated) activates
only once check_forecast_readiness() reports READY for real -- not
implemented here, since there is nothing genuine to forecast from yet.
"""

from datetime import datetime

MIN_HISTORY_DAYS = 90  # ~3 months, the low end of the "3-6 month" requirement

STATUS_READY = "READY"
STATUS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


def check_forecast_readiness(conn, source_name, as_of_date_kst=None):
    """Returns {"status", "days_of_history", "min_required_days"}.
    days_of_history is computed from the actual span between the earliest
    and latest music_observations rows for this source -- never assumed,
    never estimated from a config value. status is READY only once that
    real span meets MIN_HISTORY_DAYS."""
    row = conn.execute(
        "SELECT MIN(observed_at) AS earliest, MAX(observed_at) AS latest FROM music_observations WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    if row["earliest"] is None:
        return {"status": STATUS_INSUFFICIENT_HISTORY, "days_of_history": 0, "min_required_days": MIN_HISTORY_DAYS}

    earliest = datetime.fromisoformat(row["earliest"])
    latest = datetime.fromisoformat(row["latest"])
    days = (latest - earliest).days
    status = STATUS_READY if days >= MIN_HISTORY_DAYS else STATUS_INSUFFICIENT_HISTORY
    return {"status": status, "days_of_history": days, "min_required_days": MIN_HISTORY_DAYS}
