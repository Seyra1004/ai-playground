"""Kakao "나에게 보내기" (memo/default/send) client — sends ONE valid Kakao
message unit per call. Long-form report splitting, sequencing, and
duplicate-prevention belong to a higher layer (delivery.py / Phase 1B+), not
here.

Kakao's 기본 텍스트 템플릿 (basic text template) constraints enforced here:
- `text` must be <=200 characters (Kakao's documented limit for this
  template) — checked BEFORE calling the API; text over the limit is
  rejected with KakaoValidationError, never silently truncated.
- `link` is a required field for this template type, and per Kakao policy
  web_url/mobile_web_url must match a domain registered in that Kakao app's
  제품 설정 > 카카오톡 채널/링크 설정 (Web 플랫폼 도메인) — there is no safe
  generic default. A caller-supplied link_url is used if given; otherwise
  the REQUIRED `.env` value `KAKAO_DEFAULT_LINK_URL` is used. If neither is
  available, this raises before any network call is made — never falls back
  to an arbitrary external URL like a Kakao docs page.

Always goes through kakao.auth.get_valid_access_token() — never reads
token_store directly, so refresh is handled automatically on every call.
Only status codes and Kakao's short error/result fields are logged on
failure; the Authorization header, access_token, and raw response body are
never logged.
"""

import json
import logging

import requests

import logging_setup
from config import HTTP_TIMEOUT_SECONDS, get_required_env
from kakao.auth import get_valid_access_token

logger = logging.getLogger(__name__)

MEMO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# Kakao's documented limit for the 기본 텍스트 템플릿 `text` field.
MAX_TEXT_LENGTH = 200


class KakaoSendError(RuntimeError):
    """Raised when the Kakao memo send request fails (network, API error, or
    an unexpected/unsuccessful response). Never includes secret values, the
    Authorization header, or the raw response body."""


class KakaoValidationError(KakaoSendError):
    """Raised when the outgoing message doesn't satisfy Kakao's template
    constraints (e.g. text too long) — caught BEFORE any network call is
    made. A higher layer (delivery.py) is expected to split/shorten content
    and retry with a valid unit; this client never truncates silently."""


def send_memo(text, link_url=None, button_title=None):
    """Send ONE 기본 텍스트 템플릿 memo via 나에게 보내기.

    Returns the parsed JSON response body on success (HTTP 200 AND
    result_code == 0). Raises KakaoValidationError if `text` is empty or
    exceeds Kakao's length limit. If `link_url` is not given, resolves the
    required `KAKAO_DEFAULT_LINK_URL` env var — if that's also unset, raises
    config.MissingSecretError before any network call (no token refresh,
    no request) is attempted. Raises KakaoSendError for any network
    failure, non-200 response, malformed body, or a body that doesn't
    indicate success. Callers are responsible for recording the outcome
    (e.g. in delivery_history) — this function has no idempotency logic of
    its own."""
    if not text:
        raise KakaoValidationError("text must be non-empty.")
    if len(text) > MAX_TEXT_LENGTH:
        raise KakaoValidationError(
            f"text is {len(text)} characters, which exceeds Kakao's "
            f"{MAX_TEXT_LENGTH}-character limit for the basic text template. "
            "Split the message before calling send_memo()."
        )

    # Resolved before get_valid_access_token() so a missing/misconfigured
    # link fails fast without triggering a token refresh network call.
    effective_link = link_url if link_url else get_required_env("KAKAO_DEFAULT_LINK_URL")

    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": effective_link, "mobile_web_url": effective_link},
    }
    if button_title:
        template["button_title"] = button_title

    return _send_template(template)


# Kakao's documented per-button title limit for the feed template (버튼 이름) --
# far shorter than MAX_TEXT_LENGTH, checked separately since a button title
# is a distinct field, never counted against the 200-char text/description
# budget.
MAX_BUTTON_TITLE_LENGTH = 14


def send_feed_memo(title, description, link_url=None, buttons=None):
    """Send ONE 피드 템플릿 (feed template) memo via 나에게 보내기 -- unlike
    send_memo's 기본 텍스트 템플릿 (exactly one link), the feed template
    supports up to 2 real, independently-clickable buttons (each its own
    title + link), which is the ONLY native Kakao mechanism for "provide
    two distinct destinations from one message" (ADDED 2026-08-22, SUPER
    NEWS DAILY/MUSIC date-fixed-archive + latest-page two-link
    requirement). Button titles are a SEPARATE field from `description`
    and do not consume MAX_TEXT_LENGTH's 200-char budget.

    `buttons`: a list of 1-2 (title, url) tuples. `link_url` (falls back
    to KAKAO_DEFAULT_LINK_URL, same contract as send_memo) is the
    fallback tap target for the message body itself, outside the
    buttons -- callers that already have a natural "primary" URL should
    pass it explicitly rather than relying on the KAKAO_DEFAULT_LINK_URL
    fallback.

    Raises KakaoValidationError for empty title/description, description
    over MAX_TEXT_LENGTH, an empty/oversized (>2) buttons list, or a
    button title over MAX_BUTTON_TITLE_LENGTH -- same fail-fast-before-
    any-network-call discipline as send_memo. Returns the parsed JSON
    response body on success; raises KakaoSendError the same way
    send_memo does for any network/API-level failure."""
    if not title:
        raise KakaoValidationError("title must be non-empty.")
    if not description:
        raise KakaoValidationError("description must be non-empty.")
    if len(description) > MAX_TEXT_LENGTH:
        raise KakaoValidationError(
            f"description is {len(description)} characters, which exceeds Kakao's "
            f"{MAX_TEXT_LENGTH}-character limit."
        )
    if not buttons or not (1 <= len(buttons) <= 2):
        raise KakaoValidationError("buttons must be a list of 1 or 2 (title, url) tuples.")
    for button_title, button_url in buttons:
        if not button_title or not button_url:
            raise KakaoValidationError("each button needs a non-empty title and url.")
        if len(button_title) > MAX_BUTTON_TITLE_LENGTH:
            raise KakaoValidationError(
                f"button title {button_title!r} is {len(button_title)} characters, "
                f"which exceeds Kakao's {MAX_BUTTON_TITLE_LENGTH}-character button-title limit."
            )

    effective_link = link_url if link_url else get_required_env("KAKAO_DEFAULT_LINK_URL")

    template = {
        "object_type": "feed",
        "content": {
            "title": title,
            "description": description,
            "link": {"web_url": effective_link, "mobile_web_url": effective_link},
        },
        "buttons": [
            {"title": button_title, "link": {"web_url": button_url, "mobile_web_url": button_url}}
            for button_title, button_url in buttons
        ],
    }
    return _send_template(template)


def _send_template(template):
    """Shared send + response-validation core for every template_object
    shape this client sends (text/send_memo, feed/send_feed_memo) --
    identical Kakao API contract regardless of object_type: HTTP 200 AND
    body["result_code"] == 0 are both required for success, Authorization/
    access_token/raw body are never logged."""
    access_token = get_valid_access_token()
    logging_setup.register_secret(access_token)

    headers = {"Authorization": f"Bearer {access_token}"}
    data = {"template_object": json.dumps(template, ensure_ascii=False)}

    try:
        response = requests.post(
            MEMO_SEND_URL, headers=headers, data=data, timeout=HTTP_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        logger.error("Kakao memo send failed: network error (%s)", type(exc).__name__)
        raise KakaoSendError("Kakao memo send failed due to a network error.") from exc

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.status_code != 200:
        error_code = None
        if isinstance(body, dict):
            error_code = body.get("code") or body.get("error_code") or body.get("error")
        logger.error(
            "Kakao memo send failed: status=%s error=%s",
            response.status_code,
            error_code,
        )
        raise KakaoSendError(
            f"Kakao memo send failed with status {response.status_code} "
            f"(error={error_code!r})."
        )

    if not isinstance(body, dict):
        raise KakaoSendError(
            "Kakao memo send returned status 200 but the body was not a JSON object."
        )

    # Kakao's memo/default/send indicates success via result_code == 0 in the
    # body, independent of the HTTP status — a 200 alone isn't sufficient.
    result_code = body.get("result_code")
    if result_code != 0:
        logger.error("Kakao memo send returned status 200 but result_code=%s", result_code)
        raise KakaoSendError(
            f"Kakao memo send did not report success (result_code={result_code!r})."
        )

    logger.info("Kakao memo sent successfully.")
    return body
