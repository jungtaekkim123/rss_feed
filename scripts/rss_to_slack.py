"""
RSS Feed to Slack Notifier
- RSS 피드를 파싱하여 새로운 글을 Slack Webhook으로 전송
- 중복 방지: sent_entries.json에 이미 보낸 항목 ID를 저장
"""

import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import yaml


# ── 경로 설정 ──────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
SENT_DB_PATH = ROOT_DIR / "data" / "sent_entries.json"


def load_config() -> dict:
    """config.yaml에서 RSS 피드 목록과 설정을 읽어옵니다."""
    if not CONFIG_PATH.exists():
        print(f"❌ 설정 파일을 찾을 수 없습니다: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sent_entries() -> dict:
    """이미 전송한 항목 ID를 로드합니다."""
    if not SENT_DB_PATH.exists():
        return {}
    with open(SENT_DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_sent_entries(entries: dict) -> None:
    """전송한 항목 ID를 저장합니다."""
    SENT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SENT_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def make_entry_id(entry) -> str:
    """피드 항목의 고유 ID를 생성합니다."""
    # id(guid) > link > title hash 순으로 우선순위
    if hasattr(entry, "id") and entry.id:
        return entry.id
    if hasattr(entry, "link") and entry.link:
        return entry.link
    raw = (entry.get("title", "") + entry.get("summary", "")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_feed(url: str, max_items: int = 5) -> list[dict]:
    """RSS/Atom 피드를 파싱하여 최신 항목을 반환합니다."""
    feed = feedparser.parse(url)

    if feed.bozo and not feed.entries:
        print(f"⚠️  피드 파싱 실패: {url} — {feed.bozo_exception}")
        return []

    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "id": make_entry_id(entry),
            "title": entry.get("title", "(제목 없음)"),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", "")[:200],
            "published": entry.get("published", ""),
            "feed_title": feed.feed.get("title", url),
        })
    return items


def build_slack_blocks(feed_name: str, entries: list[dict]) -> dict:
    """Slack Block Kit 형식의 메시지를 생성합니다."""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📰 {feed_name}",
                "emoji": True,
            },
        },
        {"type": "divider"},
    ]

    for entry in entries:
        title = entry["title"]
        link = entry["link"]
        summary = entry["summary"].replace("<", "&lt;").replace(">", "&gt;")
        if len(summary) > 150:
            summary = summary[:150] + "…"

        text = f"*<{link}|{title}>*"
        if summary:
            text += f"\n{summary}"
        if entry["published"]:
            text += f"\n🕐 {entry['published']}"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"🤖 RSS Bot • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            }
        ],
    })

    return {"blocks": blocks}


def send_to_slack(webhook_url: str, payload: dict) -> bool:
    """Slack Webhook으로 메시지를 전송합니다."""
    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200 and resp.text == "ok":
            return True
        print(f"⚠️  Slack 전송 실패: {resp.status_code} — {resp.text}")
        return False
    except requests.RequestException as e:
        print(f"❌ Slack 요청 에러: {e}")
        return False


def main():
    config = load_config()
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", config.get("slack_webhook_url", ""))

    if not webhook_url:
        print("❌ SLACK_WEBHOOK_URL이 설정되지 않았습니다.")
        print("   → GitHub Secrets에 SLACK_WEBHOOK_URL을 추가하거나 config.yaml에 설정하세요.")
        sys.exit(1)

    feeds = config.get("feeds", [])
    if not feeds:
        print("❌ 구독할 RSS 피드가 없습니다. config.yaml에 feeds를 추가하세요.")
        sys.exit(1)

    max_items = config.get("max_items_per_feed", 5)
    sent_entries = load_sent_entries()
    total_new = 0

    for feed_cfg in feeds:
        url = feed_cfg["url"]
        name = feed_cfg.get("name", url)
        print(f"\n🔍 피드 확인 중: {name}")

        entries = parse_feed(url, max_items=max_items)
        if not entries:
            print(f"   항목이 없습니다.")
            continue

        # 중복 필터링
        new_entries = [e for e in entries if e["id"] not in sent_entries]

        if not new_entries:
            print(f"   새로운 항목이 없습니다.")
            continue

        print(f"   📬 새로운 항목 {len(new_entries)}개 발견!")

        # Slack으로 전송
        payload = build_slack_blocks(name, new_entries)
        if send_to_slack(webhook_url, payload):
            # 전송 성공 시 DB에 기록
            for entry in new_entries:
                sent_entries[entry["id"]] = {
                    "title": entry["title"],
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }
            total_new += len(new_entries)
            print(f"   ✅ 전송 완료!")
        else:
            print(f"   ❌ 전송 실패!")

    # 오래된 항목 정리 (최근 500개만 유지)
    max_history = config.get("max_history", 500)
    if len(sent_entries) > max_history:
        sorted_items = sorted(
            sent_entries.items(),
            key=lambda x: x[1].get("sent_at", ""),
            reverse=True,
        )
        sent_entries = dict(sorted_items[:max_history])

    save_sent_entries(sent_entries)

    print(f"\n{'='*40}")
    print(f"✅ 완료! 총 {total_new}개의 새로운 항목을 전송했습니다.")


if __name__ == "__main__":
    main()
