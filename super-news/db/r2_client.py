"""Thin Cloudflare R2 (S3-compatible) client wrapper -- the ONLY module in
this project that imports boto3, matching the existing project convention
of a single, narrow import boundary per external SDK (report/llm_
anthropic.py for anthropic, report/translation_anthropic.py for the same).

Credential handling matches config.py's existing get_optional_env/
get_required_env pattern exactly -- never hardcoded, never logged, never
included in any manifest or backup content (see db/backup.py's own
docstring for the full DATABASE BACKUP INVARIANT).

Config (env vars, set in super-news/.env -- see .env.example):
  R2_ACCOUNT_ID          -- Cloudflare account id (used to build the R2
                            S3-compatible endpoint URL)
  R2_ACCESS_KEY_ID        -- R2 API token access key id
  R2_SECRET_ACCESS_KEY    -- R2 API token secret
  R2_BUCKET_NAME           -- default "super-news-backups" if unset
  R2_FREE_STORAGE_GB       -- reference free-tier allowance for capacity
                              alerts (see db/backup.py); default 10
"""

from pathlib import Path

from config import get_optional_env

DEFAULT_BUCKET_NAME = "super-news-backups"


class R2NotConfiguredError(RuntimeError):
    """Raised when R2 credentials/bucket are not configured -- the
    "not configured at all" case, distinct from a real transient/permanent
    upload or download failure. Never includes any credential VALUE."""


def is_configured():
    """True if enough config exists to attempt building a real R2 client,
    decided deterministically without a network round-trip -- same shape
    as report.translation.TranslationProvider.is_configured()."""
    return bool(
        get_optional_env("R2_ACCOUNT_ID")
        and get_optional_env("R2_ACCESS_KEY_ID")
        and get_optional_env("R2_SECRET_ACCESS_KEY")
    )


def bucket_name():
    return get_optional_env("R2_BUCKET_NAME", DEFAULT_BUCKET_NAME)


def free_storage_gb():
    from db.backup import DEFAULT_R2_FREE_STORAGE_GB

    raw = get_optional_env("R2_FREE_STORAGE_GB")
    return float(raw) if raw else DEFAULT_R2_FREE_STORAGE_GB


def build_client():
    """Constructs a real boto3 S3-compatible client pointed at this
    account's R2 endpoint. Raises R2NotConfiguredError (never a boto3
    exception with a confusing message) if credentials are missing --
    mirrors report.translation_anthropic.AnthropicTranslationProvider's
    own "never a construction-time crash for a missing-credential
    environment" discipline: callers should check is_configured() first,
    but this is also safe to call directly."""
    if not is_configured():
        raise R2NotConfiguredError(
            "R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY are not fully set. "
            "Copy super-news/.env.example to super-news/.env and fill in the R2_* values."
        )
    import boto3

    account_id = get_optional_env("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=get_optional_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=get_optional_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def upload_object(client, bucket, key, local_path):
    client.upload_file(str(local_path), bucket, key)


def head_object(client, bucket, key):
    """Returns {"exists": bool, "size_bytes": int|None} -- never raises
    for a genuinely missing object (that's a normal "not found yet"
    outcome, not a crash); re-raises any other real client error."""
    from botocore.exceptions import ClientError

    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            return {"exists": False, "size_bytes": None}
        raise
    return {"exists": True, "size_bytes": response.get("ContentLength")}


def download_object(client, bucket, key, dest_path):
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, key, str(dest_path))
    return dest_path


def list_all_objects(client, bucket, prefix=None):
    """Returns a list of {"key": str, "size_bytes": int, "last_modified":
    datetime} for every object under `prefix` (or the whole bucket if
    None) -- paginates internally so this is correct regardless of object
    count. Used both for the restore drill (finding the object just
    uploaded) and for capacity accounting (db.backup.classify_capacity)."""
    paginator = client.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    objects = []
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            objects.append({"key": obj["Key"], "size_bytes": obj["Size"], "last_modified": obj["LastModified"]})
    return objects


def get_bucket_usage_bytes(client, bucket, prefix=None):
    """Sum of real object sizes under `prefix` (or the whole bucket) --
    the documented fallback (Phase 3D-BACKUP section 13's own
    instruction) when a direct Cloudflare storage-usage metric API isn't
    available through this credential/SDK. Never a guess -- always the
    real sum of what list_objects_v2 actually reports."""
    return sum(obj["size_bytes"] for obj in list_all_objects(client, bucket, prefix))
