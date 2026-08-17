# SUPER NEWS -- Windows Task Scheduler registration for the ONE full daily
# production entrypoint (scripts/run_daily_full_pipeline_v2.py): fresh
# ingestion -> Apple/Spotify music collection -> derived signals ->
# SUPER NEWS MUSIC Kakao delivery -> SUPER NEWS DAILY Kakao delivery.
#
# NOT executed automatically by anything in this repo -- this script only
# REGISTERS a scheduled task; it never runs the pipeline itself. Registering
# a real OS-level scheduled task is a system-level change, so this is left
# as an explicit, separately-run step requiring its own confirmation, not
# bundled into any other script's execution path.
#
# Usage (run manually, from an elevated or normal PowerShell prompt -- a
# per-user logon task does not require elevation):
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_windows_task.ps1
#
# What it does: creates (or replaces, -Force) a Task Scheduler task named
# "SuperNewsDailyPipelineV2" that runs once daily at 09:00 local time,
# invoking the project's own .venv Python interpreter against
# scripts\run_daily_full_pipeline_v2.py with the project directory as its
# working directory (so config.py's PROJECT_ROOT-relative paths resolve
# correctly regardless of Task Scheduler's own default working directory --
# see config.py's own module docstring on exactly this point). The
# SUPER_NEWS_NO_PAID_API=1 cost guard does NOT depend on this task's own
# launch environment -- run_daily_full_pipeline_v2.py forces it into its own
# process environment unconditionally, before any other import (see that
# script's own module docstring), so it applies regardless of how or by
# whom this task is ever triggered.
#
# To verify after registration:      schtasks /query /tn "SuperNewsDailyPipelineV2" /v /fo LIST
# To run it once immediately:        schtasks /run /tn "SuperNewsDailyPipelineV2"
# To remove it:                      schtasks /delete /tn "SuperNewsDailyPipelineV2" /f
#
# Superseded name (never actually registered in any prior session, so there
# is no stale duplicate to remove): "SuperNewsKakaoDeliveryV2", which only
# sent already-persisted data with no fresh ingestion of its own. This is
# the ONE intended SUPER NEWS scheduled task -- do not register both.

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ScriptPath = Join-Path $ProjectRoot "scripts\run_daily_full_pipeline_v2.py"
$TaskName = "SuperNewsDailyPipelineV2"

if (-not (Test-Path $PythonExe)) {
    throw "Project venv python not found at $PythonExe -- run this from the super-news project with .venv already created."
}
if (-not (Test-Path $ScriptPath)) {
    throw "Full pipeline entrypoint not found at $ScriptPath."
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`"" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "SUPER NEWS: fresh ingestion -> music collection -> derived signals -> SUPER NEWS MUSIC + DAILY Kakao digests (SUPER_NEWS_NO_PAID_API=1 self-enforced; idempotent per report_date; survives closing VS Code/Claude Code since Task Scheduler owns the process)." `
    -Force

Write-Host "Registered scheduled task '$TaskName' -- daily at 09:00 local time."
Write-Host "Verify:  schtasks /query /tn `"$TaskName`" /v /fo LIST"
