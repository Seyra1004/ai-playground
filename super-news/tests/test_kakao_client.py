"""kakao.client: send_memo (기본 텍스트 템플릿, one link) and send_feed_memo
(피드 템플릿, up to 2 real buttons -- ADDED 2026-08-22 for SUPER NEWS
DAILY/MUSIC's date-fixed-archive + latest-page two-link requirement).
requests.post is mocked throughout -- no real Kakao API call in this file.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from kakao.client import (
    MAX_BUTTON_TITLE_LENGTH,
    MAX_TEXT_LENGTH,
    KakaoValidationError,
    send_feed_memo,
    send_memo,
)


def _mock_response(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {"result_code": 0}
    return resp


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr("kakao.client.get_valid_access_token", lambda: "fake_access_token")


def test_send_memo_still_sends_text_template_with_one_link():
    with patch("kakao.client.requests.post") as mock_post:
        mock_post.return_value = _mock_response()
        send_memo("hello", link_url="https://example.com/latest", button_title="더 보기")

    sent_template = json.loads(mock_post.call_args.kwargs["data"]["template_object"])
    assert sent_template["object_type"] == "text"
    assert sent_template["text"] == "hello"
    assert sent_template["link"]["web_url"] == "https://example.com/latest"
    assert sent_template["button_title"] == "더 보기"
    assert "buttons" not in sent_template  # unchanged single-link contract


def test_send_feed_memo_payload_contains_both_intended_links():
    dated_url = "https://example.com/v2/reports/daily/2026-08-22.html"
    latest_url = "https://example.com/v2/daily.html"
    with patch("kakao.client.requests.post") as mock_post:
        mock_post.return_value = _mock_response()
        send_feed_memo(
            "SUPER NEWS DAILY", "AI: ...\nECONOMY: ...\nSOCIETY: ...",
            link_url=latest_url,
            buttons=[("오늘 DAILY", dated_url), ("최신 DAILY", latest_url)],
        )

    sent_template = json.loads(mock_post.call_args.kwargs["data"]["template_object"])
    assert sent_template["object_type"] == "feed"
    assert sent_template["content"]["title"] == "SUPER NEWS DAILY"
    button_urls = {b["title"]: b["link"]["web_url"] for b in sent_template["buttons"]}
    assert button_urls == {"오늘 DAILY": dated_url, "최신 DAILY": latest_url}


def test_send_feed_memo_date_fixed_button_reflects_report_date_not_click_time():
    """The date-fixed button URL must come from the caller-supplied
    report_date, never derived from "today" inside the client -- this
    client has no notion of report_date at all, it only ever forwards
    whatever URL the caller (report_delivery_v2) already resolved."""
    with patch("kakao.client.requests.post") as mock_post:
        mock_post.return_value = _mock_response()
        send_feed_memo(
            "SUPER NEWS MUSIC", "LEAD: ...",
            link_url="https://example.com/v2/music.html",
            buttons=[
                ("오늘 MUSIC", "https://example.com/v2/reports/music/2026-08-19.html"),
                ("최신 MUSIC", "https://example.com/v2/music.html"),
            ],
        )
    sent_template = json.loads(mock_post.call_args.kwargs["data"]["template_object"])
    dated_button = sent_template["buttons"][0]
    assert "2026-08-19" in dated_button["link"]["web_url"]


def test_send_feed_memo_rejects_more_than_two_buttons():
    with pytest.raises(KakaoValidationError):
        send_feed_memo(
            "t", "d", link_url="https://example.com",
            buttons=[("a", "https://x/1"), ("b", "https://x/2"), ("c", "https://x/3")],
        )


def test_send_feed_memo_rejects_empty_buttons():
    with pytest.raises(KakaoValidationError):
        send_feed_memo("t", "d", link_url="https://example.com", buttons=[])


def test_send_feed_memo_rejects_oversized_button_title():
    with pytest.raises(KakaoValidationError):
        send_feed_memo(
            "t", "d", link_url="https://example.com",
            buttons=[("가" * (MAX_BUTTON_TITLE_LENGTH + 1), "https://example.com")],
        )


def test_send_feed_memo_rejects_description_over_kakao_limit():
    with pytest.raises(KakaoValidationError):
        send_feed_memo(
            "t", "x" * (MAX_TEXT_LENGTH + 1), link_url="https://example.com",
            buttons=[("a", "https://x/1")],
        )


def test_send_feed_memo_never_calls_network_when_validation_fails():
    with patch("kakao.client.requests.post") as mock_post:
        with pytest.raises(KakaoValidationError):
            send_feed_memo("t", "d", link_url="https://example.com", buttons=[])
    mock_post.assert_not_called()
