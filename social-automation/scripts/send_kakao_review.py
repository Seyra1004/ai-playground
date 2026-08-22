from __future__ import annotations

"""Send ONE Kakao "나에게 보내기" feed memo pointing at a SWIPE_INFO review
page. Must be run with SUPER_NEWS's own venv python (its kakao/config/
logging_setup modules and their deps -- requests, python-dotenv -- live
there, not in social-automation's venv). Reads only; never writes any
SUPER_NEWS file. No PNG attachment -- link only.
"""

import argparse
import os
import sys

SUPER_NEWS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "super-news")
sys.path.insert(0, SUPER_NEWS_DIR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--dated-url", required=True)
    parser.add_argument("--latest-url", required=True)
    args = parser.parse_args()

    from kakao.client import KakaoSendError, KakaoValidationError, send_feed_memo  # noqa: E402

    description = f"[{args.status}] {args.topic}"[:200]
    try:
        resp = send_feed_memo(
            title="SWIPE_INFO 오늘 콘텐츠",
            description=description,
            link_url=args.dated_url,
            buttons=[("오늘 리뷰 보기", args.dated_url), ("최신 콘텐츠", args.latest_url)],
        )
        print(f"KAKAO_OK result_code={resp.get('result_code')}")
        return 0
    except (KakaoSendError, KakaoValidationError) as exc:
        print(f"KAKAO_FAILED {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
