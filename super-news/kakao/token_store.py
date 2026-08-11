"""Persistence for Kakao access/refresh tokens.

Design (see PHASE 1A plan):
- Plain JSON file (`data/kakao_token.json`), NOT SQLite — kept isolated from
  business/run data so secret-handling never couples to DB schema changes.
- Atomic write: temp file -> flush -> fsync -> os.replace(), so a crash or power
  loss mid-write can never leave a half-written/corrupt token file.
- ACL apply AND verify are both SID-based (current user's SID, resolved at
  runtime, plus well-known SIDs) — never a display name like the USERNAME env
  var or the literal string "SYSTEM"/"Everyone"/"BUILTIN\\Users".
  Display-name matching is locale-dependent (breaks on e.g. Korean Windows);
  SIDs are not.
- Verification tolerates exactly four SIDs: the current user, SYSTEM,
  BUILTIN\\Administrators, and OWNER RIGHTS (the last only when the path's
  actual Owner is confirmed to be the current user). This is NOT "these are
  all safe" — measured on this machine, a brand-new directory/file created
  without an explicit security descriptor already carries non-inherited
  SYSTEM + BUILTIN\\Administrators + OWNER RIGHTS ACEs by default (this is
  how Windows' default token DACL works for an account in the Administrators
  group), and `icacls /inheritance:r /grant:r` can only ADD/replace the
  trustees you name — it cannot remove those pre-existing ones. Actually
  stripping them requires replacing the whole DACL with SeSecurityPrivilege,
  which was tested here and fails with PrivilegeNotHeldException under a
  normal (non-elevated) process token. Requiring this to run elevated would
  be a much bigger operational cost than the risk being managed, and directly
  works against Phase 1A's top priority (SUPER NEWS must send reliably every
  day, unattended, without UAC prompts). So Administrators is tolerated as a
  documented environmental constraint of running non-elevated on Windows —
  not a claim that it's harmless. Any local Administrator account already has
  far greater access to this machine than one file's ACL could ever prevent
  (take ownership, read LSASS, etc.), so this ACE doesn't meaningfully change
  the real threat model. Genuinely broad principals — Everyone,
  BUILTIN\\Users, Authenticated Users, INTERACTIVE, ANONYMOUS LOGON, Domain
  Users — are still rejected, since none of those are part of Windows'
  default new-object DACL and their presence would mean something is
  actually wrong (e.g. inheritance from a misconfigured parent directory).
- The DIRECTORY containing the token file (`data/`) is what actually gets
  locked down, with inheritance flags, once per save — not each individual
  file. Because NTFS applies a parent's inheritable ACEs to a file at the
  moment it's created, this means the temp file created by `tempfile.mkstemp`
  is already restricted to {current user, SYSTEM} from its very first instant
  on disk, before any secret content is written into it. There is no window
  where a partially-protected file holds secret data — the alternative
  (locking down each temp/final file individually, after creation) would have
  had exactly such a window. `save()` still verifies the temp file and the
  final file's own ACL as a defensive check; `load()` verifies (but never
  re-applies/mutates) both the directory's and the file's ACL, failing loudly
  rather than silently "healing" a permission change that might indicate an
  actual problem.
- Expiry fields are only ever populated from real `expires_in` /
  `refresh_token_expires_in` values Kakao returned — never guessed.
- Loading validates `schema_version` and JSON well-formedness before trusting
  the contents; failures raise a clear error without ever including raw file
  contents or secret values in the message.
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import TOKEN_STORE_PATH

import logging_setup

SCHEMA_VERSION = 1

# Well-known, locale-independent SIDs. See the module docstring above for why
# ADMINISTRATORS_SID is tolerated (a non-elevated-Windows constraint, not a
# safety claim) and why OWNER_RIGHTS_SID is only tolerated conditionally.
_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"
_OWNER_RIGHTS_SID = "S-1-3-4"


class TokenStoreError(RuntimeError):
    """Base class for all token store errors. Subclasses never include raw
    file contents, icacls/PowerShell output, or secret values in their
    messages."""


class TokenStoreInsecureError(TokenStoreError):
    """Raised when ACL lock-down on the token directory/file could not be
    applied or verified — on save, or on load after e.g. a PC restart. The
    caller must treat the token file as exposed and abort rather than
    proceed."""


class TokenStoreCorruptError(TokenStoreError):
    """Raised when the token file passes ACL verification but its contents
    are not valid JSON, not a JSON object, or declare an unsupported
    schema_version."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _register_known_secrets(data):
    for key in ("access_token", "refresh_token"):
        value = data.get(key)
        if value:
            logging_setup.register_secret(value)


def _run_powershell(script):
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
    )


def _get_current_user_sid():
    try:
        result = _run_powershell(
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise TokenStoreInsecureError(
            "Could not determine current user's SID to manage ACLs "
            "— treat the token store as exposed."
        ) from exc
    sid = result.stdout.strip()
    if not sid.startswith("S-1-"):
        raise TokenStoreInsecureError(
            "Could not determine current user's SID to manage ACLs "
            "— treat the token store as exposed."
        )
    return sid


def _apply_dir_lock_down(dir_path):
    """SID-based ACL apply, directory-only. Grants Full Control, with
    object-inherit/container-inherit flags, to ONLY the current user's SID and
    SYSTEM's SID — no display names involved anywhere in this call. Any file
    later created inside `dir_path` (via tempfile.mkstemp or otherwise)
    inherits exactly this restricted ACL at creation time."""
    current_user_sid = _get_current_user_sid()
    grant_current = f"*{current_user_sid}:(OI)(CI)F"
    grant_system = f"*{_SYSTEM_SID}:(OI)(CI)F"
    try:
        subprocess.run(
            [
                "icacls",
                str(dir_path),
                "/inheritance:r",
                "/grant:r",
                grant_current,
                "/grant:r",
                grant_system,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise TokenStoreInsecureError(
            f"ACL lock-down on {dir_path.name} failed to apply "
            "— treat the token store as exposed."
        ) from exc


def _get_owner_sid(path):
    """Resolve the path's Owner (an account-name string from Get-Acl) to a
    SID, so OWNER RIGHTS can be verified against the actual owner rather than
    assumed safe by name alone."""
    escaped_path = str(path).replace("'", "''")
    script = (
        f"(Get-Acl -LiteralPath '{escaped_path}').Owner | ForEach-Object {{ "
        "(New-Object System.Security.Principal.NTAccount($_))."
        "Translate([System.Security.Principal.SecurityIdentifier]).Value "
        "}"
    )
    try:
        result = _run_powershell(script)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise TokenStoreInsecureError(
            f"Owner of {path.name} could not be determined — treat it as exposed."
        ) from exc
    owner_sid = result.stdout.strip()
    if not owner_sid.startswith("S-1-"):
        raise TokenStoreInsecureError(
            f"Owner of {path.name} could not be determined — treat it as exposed."
        )
    return owner_sid


def _verify_acl_or_raise(path):
    """SID-based allow-list check, works on a file or a directory: every ACE
    must resolve to the current user's SID, SYSTEM, BUILTIN\\Administrators,
    or (only when this path's Owner is confirmed to be the current user)
    OWNER RIGHTS. See the module docstring for why Administrators/OWNER
    RIGHTS are tolerated rather than treated as a failure. Any other
    principal — Everyone, BUILTIN\\Users, Authenticated Users, INTERACTIVE,
    ANONYMOUS LOGON, Domain Users, or anything else — still fails
    verification. Read-only — never mutates permissions, so a failure here
    always means "stop and investigate," not something silently
    auto-corrected."""
    current_user_sid = _get_current_user_sid()
    allowed = {current_user_sid, _SYSTEM_SID, _ADMINISTRATORS_SID}
    if _get_owner_sid(path) == current_user_sid:
        allowed.add(_OWNER_RIGHTS_SID)

    escaped_path = str(path).replace("'", "''")
    script = (
        f"$acl = Get-Acl -LiteralPath '{escaped_path}'; "
        "$acl.Access | ForEach-Object { "
        "$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value "
        "}"
    )
    try:
        result = _run_powershell(script)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise TokenStoreInsecureError(
            f"ACL on {path.name} could not be verified — treat it as exposed."
        ) from exc

    granted_sids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not granted_sids:
        raise TokenStoreInsecureError(
            f"ACL on {path.name} could not be verified (no access entries found) "
            "— treat it as exposed."
        )
    for sid in granted_sids:
        if sid not in allowed:
            raise TokenStoreInsecureError(
                f"ACL on {path.name} grants access to an unexpected account "
                "— treat it as exposed."
            )


def load(path=None):
    """Verify (never re-apply) the directory's and the file's ACL BEFORE
    reading its contents. If verification fails, the token contents are never
    read into memory or returned. If the ACL is fine but the JSON is malformed
    or its schema_version is unsupported, raises TokenStoreCorruptError
    without echoing file contents."""
    path = Path(path) if path is not None else TOKEN_STORE_PATH
    if not path.exists():
        return None

    _verify_acl_or_raise(path.parent)
    _verify_acl_or_raise(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise TokenStoreCorruptError(
            f"{path.name} is not valid JSON and cannot be read — treat it as corrupt."
        ) from exc

    if not isinstance(data, dict):
        raise TokenStoreCorruptError(
            f"{path.name} does not contain a JSON object — treat it as corrupt."
        )

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise TokenStoreCorruptError(
            f"{path.name} has unsupported schema_version {schema_version!r} "
            f"(expected {SCHEMA_VERSION})."
        )

    _register_known_secrets(data)
    return data


def save(data, path=None):
    """Lock down the containing directory (self-healing — re-applied every
    save), then atomically persist `data` into a temp file that inherits that
    lock-down from the moment it's created, then replace the final path.
    Always stamps schema_version and updated_at."""
    path = Path(path) if path is not None else TOKEN_STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _apply_dir_lock_down(path.parent)
    _verify_acl_or_raise(path.parent)

    payload = dict(data)
    payload["schema_version"] = SCHEMA_VERSION
    payload["updated_at"] = _now_iso()
    _register_known_secrets(payload)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        # Inherited from the just-locked-down directory at creation time —
        # verified here as a defensive check before any secret is written in.
        _verify_acl_or_raise(tmp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    _verify_acl_or_raise(path)
    return payload


def merge_and_save(updates, path=None):
    """Load existing token data (ACL- and schema-verified), apply `updates` on
    top, then save atomically.

    Update semantics (deliberately PATCH-like, not "ignore None"): a key that
    IS PRESENT in `updates` always overwrites the stored value, even with
    None — this matters because a new access_token's expiry must replace the
    old access_token's expiry even when the new one is unknown (Kakao didn't
    return expires_in), rather than leaving a stale expiry attached to a
    token it no longer describes. A key that is simply ABSENT from `updates`
    is left untouched — e.g. callers omit refresh_token/refresh_token_expires_at
    entirely when Kakao's response didn't rotate the refresh_token, so the
    existing one (and the expiry describing THAT still-valid token) survives."""
    path = Path(path) if path is not None else TOKEN_STORE_PATH
    existing = load(path) or {}
    merged = dict(existing)
    merged.update(updates)
    return save(merged, path)


def is_expired_or_unknown(expires_at_iso, margin_seconds=0):
    """True if the timestamp is missing/unparseable (unknown -> treat as
    needing refresh) or is at/past now (+ safety margin). Never infers a
    lifetime that wasn't actually provided by Kakao."""
    if not expires_at_iso:
        return True
    try:
        expires_at = datetime.fromisoformat(expires_at_iso)
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at - timedelta(seconds=margin_seconds)
