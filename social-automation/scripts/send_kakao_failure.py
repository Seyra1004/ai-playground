from __future__ import annotations

"""Best-effort alert for a failed SWIPE_INFO systemd run."""

import argparse
import os
import sys

SUPER_NEWS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "super-news")
sys.path.insert(0, SUPER_NEWS_DIR)
PAGES_BASE_URL = "https://seyra1004.github.io/ai-playground/v2/reports/swipe-info/latest/"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", default="일일 제작 서비스가 실패했습니다")
    args = parser.parse_args()
    from kakao.client import KakaoSendError, KakaoValidationError, send_feed_memo  # noqa: E402

    try:
        send_feed_memo(
            title="SWIPE_INFO 제작 확인 필요",
            description=args.reason[:200],
            link_url=PAGES_BASE_URL,
            buttons=[("마지막 검토본 보기", PAGES_BASE_URL)],
        )
        print("KAKAO_FAILURE_ALERT_OK")
        return 0
    except (KakaoSendError, KakaoValidationError) as exc:
        print(f"KAKAO_FAILURE_ALERT_FAILED {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
