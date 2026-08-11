"""One-time interactive Kakao OAuth bootstrap.

Run this manually, once:

    .venv\\Scripts\\python.exe scripts\\bootstrap_auth.py

Scope (intentionally narrow — this script does exactly this and nothing
else): present the authorization URL -> wait for the localhost callback ->
receive the authorization code in memory only -> hand it to
kakao.auth.exchange_authorization_code() -> report success/failure without
ever exposing a secret -> exit. Token storage, refresh, and all Kakao API
logic live in kakao.auth / kakao.token_store and are not duplicated here.

Does NOT modify or reuse get_auth_code.py — that script is untouched.

What this does NOT guarantee: after bootstrap, access-token refresh from the
stored refresh_token is automatic in normal operation, but re-running this
script may still be needed later if the refresh_token expires or is revoked,
the user disconnects the app in Kakao, or Kakao's authorization policy
changes. No OAuth token lifetime is assumed or hardcoded anywhere here.
"""

import logging
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from config import MissingSecretError, get_required_env
from kakao.auth import AuthorizationCodeError, KakaoAuthError, exchange_authorization_code
from kakao.token_store import TokenStoreCorruptError, TokenStoreInsecureError

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"

# How long we wait for the USER to finish the browser login/approval step.
# This is a UX wait bound, unrelated to any Kakao token lifetime.
CALLBACK_WAIT_TIMEOUT_SECONDS = 300
# How often handle_request() polls while waiting, so the timeout above is
# actually checked instead of blocking indefinitely on one accept().
_POLL_INTERVAL_SECONDS = 5

_PAGE_SUCCESS = (
    "<h1>Kakao 인증 완료</h1><p>터미널로 돌아가세요. 이 창은 닫아도 됩니다.</p>"
).encode("utf-8")
_PAGE_OAUTH_ERROR = (
    "<h1>Kakao 인증 실패</h1><p>터미널을 확인하세요.</p>"
).encode("utf-8")
_PAGE_NO_CODE = "<h1>code가 없습니다.</h1>".encode("utf-8")
_PAGE_UNEXPECTED = "<h1>Not found</h1>".encode("utf-8")


class BootstrapError(RuntimeError):
    """Local control-flow error for this script only (server bind failure,
    OAuth error callback, user-interaction timeout). Never carries a secret
    value — every message here is a fixed string this script itself wrote."""


class _CallbackResult:
    """Tiny mutable box so the per-request handler instance can hand a
    result back to the waiting loop in _run_callback_server()."""

    def __init__(self):
        self.code = None
        self.oauth_error = False


class _CodeCaptureHandler(BaseHTTPRequestHandler):
    # Set on the class before the server starts — BaseHTTPRequestHandler
    # instances are created fresh per request by HTTPServer, so this is the
    # simplest way to pass in the expected path and where to write the result.
    expected_path = "/"
    result = None

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if parsed.path != self.expected_path:
            # Never accept a code (or anything else) from an unexpected path —
            # e.g. a browser's incidental /favicon.ico request.
            self.wfile.write(_PAGE_UNEXPECTED)
            return

        if "error" in query:
            # Kakao reported an OAuth error (e.g. access_denied). Stop waiting
            # for a code that will never arrive — but never echo the raw
            # error/error_description query values back or into the console.
            self.result.oauth_error = True
            self.wfile.write(_PAGE_OAUTH_ERROR)
            return

        code = query.get("code", [None])[0]
        if code:
            self.result.code = code
            self.wfile.write(_PAGE_SUCCESS)
        else:
            self.wfile.write(_PAGE_NO_CODE)

    def log_message(self, format, *args):
        pass  # keep console output limited to this script's own print()s


def _run_callback_server(redirect_uri):
    parsed = urlparse(redirect_uri)
    path = parsed.path or "/"
    hostname = parsed.hostname or "localhost"
    port = parsed.port or 80
    # Bind explicitly to the loopback address rather than whatever "localhost"
    # might resolve to, so this bootstrap server is never reachable from
    # outside this machine.
    bind_host = "127.0.0.1" if hostname in ("localhost", "127.0.0.1") else hostname

    result = _CallbackResult()
    _CodeCaptureHandler.expected_path = path
    _CodeCaptureHandler.result = result

    try:
        server = HTTPServer((bind_host, port), _CodeCaptureHandler)
    except OSError as exc:
        raise BootstrapError(
            f"Could not start the local callback server on {bind_host}:{port}."
        ) from exc

    server.timeout = _POLL_INTERVAL_SECONDS
    deadline = time.monotonic() + CALLBACK_WAIT_TIMEOUT_SECONDS

    print(f"Waiting for the Kakao redirect on {redirect_uri} ...")
    try:
        while result.code is None and not result.oauth_error:
            if time.monotonic() >= deadline:
                raise BootstrapError(
                    "Timed out waiting for the Kakao redirect. Restart the "
                    "authorization flow and complete the browser login sooner."
                )
            server.handle_request()
    finally:
        server.server_close()

    if result.oauth_error:
        raise BootstrapError(
            "Kakao returned an OAuth error during authorization. "
            "Restart the authorization flow."
        )
    return result.code


def main():
    logging_setup.setup_logging()

    try:
        rest_api_key = get_required_env("KAKAO_REST_API_KEY")
        redirect_uri = get_required_env("KAKAO_REDIRECT_URI")
    except MissingSecretError as exc:
        print(f"Bootstrap failed (missing configuration): {exc}")
        sys.exit(1)

    # client_id is a public OAuth identifier, not a secret — it's meant to
    # appear in this URL (and will be visible in the browser's address bar
    # regardless), unlike client_secret/tokens which this script never prints.
    query = urlencode(
        {
            "client_id": rest_api_key,
            "redirect_uri": redirect_uri,
            "response_type": "code",
        }
    )
    authorize_url = f"{AUTHORIZE_URL}?{query}"

    print("=" * 70)
    print("1. Open this URL in a browser and log into Kakao:")
    print(authorize_url)
    print("2. Approve access. You will be redirected back here automatically.")
    print("=" * 70)

    try:
        auth_code = _run_callback_server(redirect_uri)
    except BootstrapError as exc:
        logger.error("Bootstrap callback failed: %s", type(exc).__name__)
        print(f"Bootstrap failed: {exc}")
        sys.exit(1)

    try:
        exchange_authorization_code(auth_code)
    except AuthorizationCodeError:
        logger.error("Bootstrap failed: authorization code exchange rejected.")
        print(
            "Bootstrap failed: the authorization code was invalid, expired, "
            "or already used. Restart the authorization flow with a new code."
        )
        sys.exit(1)
    except (TokenStoreInsecureError, TokenStoreCorruptError):
        logger.error("Bootstrap failed: token store could not be trusted.")
        print(
            "Bootstrap failed: the token store could not be trusted "
            "(security/integrity check failed). See the log for details."
        )
        sys.exit(1)
    except KakaoAuthError:
        logger.error("Bootstrap failed: Kakao authentication request failed.")
        print("Bootstrap failed: the authentication request failed.")
        sys.exit(1)

    print("Bootstrap succeeded.")
    print(
        "In normal operation, access-token refresh from the stored "
        "refresh_token is now automatic. Re-running this script may still be "
        "needed later if the refresh_token expires or is revoked, the user "
        "disconnects the app, or Kakao's authorization policy changes."
    )


if __name__ == "__main__":
    main()
