import json
from datetime import datetime, timedelta, timezone

import pytest

import kakao.token_store as token_store


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "token.json"
    saved = token_store.save(
        {
            "access_token": "at1",
            "refresh_token": "rt1",
            "access_token_expires_at": None,
            "refresh_token_expires_at": None,
        },
        path=path,
    )
    assert saved["schema_version"] == token_store.SCHEMA_VERSION
    assert "updated_at" in saved

    loaded = token_store.load(path=path)
    assert loaded["access_token"] == "at1"
    assert loaded["refresh_token"] == "rt1"
    assert loaded["schema_version"] == token_store.SCHEMA_VERSION


def test_load_missing_file_returns_none(tmp_path):
    path = tmp_path / "does_not_exist.json"
    assert token_store.load(path=path) is None


def test_merge_and_save_preserves_absent_keys(tmp_path):
    path = tmp_path / "token.json"
    token_store.save({"access_token": "at1", "refresh_token": "rt1"}, path=path)
    token_store.merge_and_save(
        {"access_token": "at2", "access_token_expires_at": None}, path=path
    )
    loaded = token_store.load(path=path)
    assert loaded["access_token"] == "at2"
    assert loaded["refresh_token"] == "rt1"  # preserved, not overwritten


def test_merge_and_save_overwrites_present_key_even_with_none(tmp_path):
    path = tmp_path / "token.json"
    token_store.save(
        {"access_token": "at1", "access_token_expires_at": "2099-01-01T00:00:00+00:00"},
        path=path,
    )
    token_store.merge_and_save(
        {"access_token": "at2", "access_token_expires_at": None}, path=path
    )
    loaded = token_store.load(path=path)
    assert loaded["access_token_expires_at"] is None


def test_load_rejects_corrupt_json(tmp_path):
    path = tmp_path / "token.json"
    # save() first so the file's ACL is correctly locked down (matches real
    # production state); a truncate-and-rewrite via write_text keeps that
    # same file's ACL, so load() reaches JSON parsing rather than failing
    # ACL verification first.
    token_store.save({"access_token": "placeholder"}, path=path)
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(token_store.TokenStoreCorruptError):
        token_store.load(path=path)


def test_load_rejects_unsupported_schema_version(tmp_path):
    path = tmp_path / "token.json"
    token_store.save({"access_token": "placeholder"}, path=path)
    path.write_text(
        json.dumps({"schema_version": 999, "access_token": "x"}), encoding="utf-8"
    )

    with pytest.raises(token_store.TokenStoreCorruptError):
        token_store.load(path=path)


def test_load_rejects_non_object_json(tmp_path):
    path = tmp_path / "token.json"
    token_store.save({"access_token": "placeholder"}, path=path)
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(token_store.TokenStoreCorruptError):
        token_store.load(path=path)


def test_atomic_save_leaves_original_intact_if_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    token_store.save({"access_token": "original"}, path=path)

    def boom(*args, **kwargs):
        raise OSError("simulated crash during os.replace")

    monkeypatch.setattr(token_store.os, "replace", boom)

    with pytest.raises(OSError):
        token_store.save({"access_token": "should_not_land"}, path=path)

    # os.replace never happened, so the original file must be exactly what
    # it was before the failed save — never partially overwritten.
    loaded = token_store.load(path=path)
    assert loaded["access_token"] == "original"

    # save()'s own except-block cleans up the temp file via the real
    # os.unlink (only os.replace was patched), so no stray *.tmp file
    # should remain either.
    leftover_temp_files = list(tmp_path.glob("token.json.*.tmp"))
    assert leftover_temp_files == []


def test_is_expired_or_unknown_none_is_unknown():
    assert token_store.is_expired_or_unknown(None) is True


def test_is_expired_or_unknown_unparseable_is_unknown():
    assert token_store.is_expired_or_unknown("not-a-timestamp") is True


def test_is_expired_or_unknown_future_is_not_expired():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert token_store.is_expired_or_unknown(future) is False


def test_is_expired_or_unknown_past_is_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert token_store.is_expired_or_unknown(past) is True


def test_is_expired_or_unknown_respects_margin():
    soon = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    assert token_store.is_expired_or_unknown(soon, margin_seconds=300) is True
    assert token_store.is_expired_or_unknown(soon, margin_seconds=10) is False
