"""Manual test: send ONE real Kakao "나에게 보내기" message using the managed,
auto-refreshing token store.

This performs a REAL external side effect (an actual Kakao message send) and
must only be run deliberately, with explicit approval:

    .venv\\Scripts\\python.exe scripts\\send_test_message.py

No idempotency/dedup logic here by design — kakao.client.send_memo() is a
low-level single-message sender, and delivery_history-based duplicate
prevention belongs to the future delivery.py layer, not this manual check.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup
from kakao.auth import KakaoAuthError, ReauthRequiredError
from kakao.client import KakaoSendError, KakaoValidationError, send_memo

TEST_MESSAGE = "Super News 연결 테스트 메시지입니다."


def main():
    logging_setup.setup_logging()

    try:
        result = send_memo(TEST_MESSAGE)
    except ReauthRequiredError:
        print(
            "Send failed: re-authentication required. "
            "Run scripts/bootstrap_auth.py again."
        )
        sys.exit(1)
    except KakaoValidationError as exc:
        print(f"Send failed: message did not pass validation ({exc}).")
        sys.exit(1)
    except (KakaoSendError, KakaoAuthError):
        print("Send failed: the request to Kakao did not succeed.")
        sys.exit(1)

    print("Message sent successfully.")
    print(f"result_code={result.get('result_code')}")


if __name__ == "__main__":
    main()
