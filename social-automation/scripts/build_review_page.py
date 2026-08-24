from __future__ import annotations

"""Build a static, mobile/PC-readable review page for one COMPLETE daily
output package and publish it under the same docs/v2/ GitHub Pages tree
SUPER NEWS already uses (a separate swipe-info/ subpath -- no SUPER NEWS
file is read or written). Does not regenerate any content; purely presents
what's already in output/<account>/<date>/.
"""

import argparse
import json
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS_ROOT = os.path.join(REPO_ROOT, "docs", "v2", "reports", "swipe-info")


def _read(path, default=""):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(account: str, date: str) -> dict:
    out_dir = os.path.join(REPO_ROOT, "social-automation", "output", account, date)
    if not os.path.isdir(out_dir):
        raise FileNotFoundError(f"no output directory for {account}/{date}: {out_dir}")

    run_summary = _read_json(os.path.join(out_dir, "run_summary.json"), {})
    qa_report = _read_json(os.path.join(out_dir, "qa_report.json"), {})
    caption = _read(os.path.join(out_dir, "instagram_caption.txt"))
    threads_text = _read(os.path.join(out_dir, "threads.txt"))
    fact_sheet = _read_json(os.path.join(out_dir, "fact_sheet.json"), {})

    ig_dir = os.path.join(out_dir, "instagram")
    png_names = sorted(f for f in os.listdir(ig_dir) if f.endswith(".png")) if os.path.isdir(ig_dir) else []

    status = run_summary.get("status") or qa_report.get("content_status") or "UNKNOWN"
    topic = run_summary.get("selected_topic") or fact_sheet.get("topic") or ""
    verified_at = fact_sheet.get("verified_at") or ""

    def render(dest_dir: str):
        assets_dir = os.path.join(dest_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        for name in png_names:
            shutil.copyfile(os.path.join(ig_dir, name), os.path.join(assets_dir, name))
        zip_name = f"SWIPE_INFO_{date}_업로드용_PNG.zip"
        zip_path = os.path.join(assets_dir, zip_name)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in png_names:
                archive.write(os.path.join(ig_dir, name), arcname=name)
        contact_sheet_src = os.path.join(out_dir, "preview", "contact_sheet.png")
        has_contact_sheet = os.path.isfile(contact_sheet_src)
        if has_contact_sheet:
            shutil.copyfile(contact_sheet_src, os.path.join(assets_dir, "contact_sheet.png"))

        pages_html = "".join(
            f'<div class="page-card"><img src="assets/{name}" loading="lazy" alt="page {i+1}">'
            f'<a class="dl" href="assets/{name}" download>page_{i+1:02d}.png 다운로드</a></div>'
            for i, name in enumerate(png_names)
        )
        qa_status = qa_report.get("content_qa_status") or "-"
        render_qa_status = qa_report.get("render_qa_status") or "-"

        html = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SWIPE_INFO {_esc(date)} 리뷰</title>
<style>
  :root {{ --violet:#7848D8; --magenta:#F04890; --bg:#F7F2FF; --text:#241B31; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,'Pretendard',sans-serif; background:var(--bg); color:var(--text); }}
  header {{ padding:20px 16px; background:linear-gradient(90deg,var(--violet),var(--magenta)); color:#fff; }}
  header h1 {{ margin:0 0 4px; font-size:20px; }}
  header .meta {{ font-size:13px; opacity:.9; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:700; background:#fff; color:var(--violet); }}
  main {{ max-width:720px; margin:0 auto; padding:16px; }}
  section {{ background:#fff; border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 1px 4px rgba(0,0,0,.06); }}
  h2 {{ font-size:15px; margin:0 0 10px; color:var(--violet); }}
  .contact-sheet {{ max-width:100%; border-radius:8px; }}
  .page-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; }}
  .page-card img {{ width:100%; border-radius:6px; display:block; }}
  .page-card .dl {{ display:block; text-align:center; font-size:12px; margin-top:4px; color:var(--violet); text-decoration:none; }}
  textarea {{ width:100%; min-height:120px; border:1px solid #e0d6f5; border-radius:8px; padding:10px; font-size:14px; font-family:inherit; }}
  .muted {{ color:#8a7fa0; font-size:12px; }}
  ul {{ margin:0; padding-left:18px; }}
</style></head>
<body>
<header>
  <h1>SWIPE_INFO 오늘 콘텐츠</h1>
  <div class="meta">{_esc(date)} · <span class="badge">{_esc(status)}</span></div>
</header>
<main>
  <section><h2>토픽</h2><p>{_esc(topic)}</p><p class="muted">검증 시각: {_esc(verified_at)}</p></section>
  {'<section><h2>컨택트 시트</h2><img class="contact-sheet" src="assets/contact_sheet.png" alt="contact sheet"></section>' if has_contact_sheet else ''}
  <section><h2>페이지 ({len(png_names)}장)</h2><div class="page-grid">{pages_html}</div></section>
  <section><h2>업로드용 파일</h2><a class="dl" href="assets/{zip_name}" download>PNG 전체 ZIP 다운로드</a></section>
  <section><h2>Instagram 캡션</h2><textarea readonly onclick="this.select()">{_esc(caption)}</textarea></section>
  <section><h2>Threads</h2><textarea readonly onclick="this.select()">{_esc(threads_text)}</textarea></section>
  <section><h2>QA</h2><p>content: <b>{_esc(qa_status)}</b> · render: <b>{_esc(render_qa_status)}</b></p></section>
</main>
</body></html>"""
        with open(os.path.join(dest_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)

    dated_dir = os.path.join(DOCS_ROOT, date)
    latest_dir = os.path.join(DOCS_ROOT, "latest")
    render(dated_dir)
    render(latest_dir)

    return {
        "status": status,
        "topic": topic,
        "page_count": len(png_names),
        "dated_path": os.path.relpath(dated_dir, REPO_ROOT).replace(os.sep, "/"),
        "latest_path": os.path.relpath(latest_dir, REPO_ROOT).replace(os.sep, "/"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="swipe_info")
    parser.add_argument("--date", required=True)
    args = parser.parse_args()

    result = build(args.account, args.date)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
