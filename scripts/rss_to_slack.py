"""
RSS Feed to Slack Notifier (with Gemini AI Summary)
- RSS 피드를 파싱하여 Gemini로 요약 + 인사이트를 생성
- 카테고리별 이모지 태깅
- 오늘의 추천 글 선정
- Slack Webhook으로 전송
- 중복 방지: sent_entries.json에 이미 보낸 항목 ID를 저장
"""

import json
import os
import re
import sys
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import yaml
import google.generativeai as genai


# ── 경로 설정 ──────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
SENT_DB_PATH = ROOT_DIR / "data" / "sent_entries.json"


# ── Gemini 설정 ────────────────────────────────────────────
def init_gemini() -> genai.GenerativeModel | None:
    """Gemini API를 초기화합니다."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("⚠️  GEMINI_API_KEY가 없습니다. 요약 없이 원문 전송합니다.")
        return None

    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.0-flash")


def summarize_entry(model: genai.GenerativeModel, title: str, summary: str, link: str) -> dict:
    """Gemini로 글을 요약하고 인사이트를 생성합니다."""
    prompt = f"""아래 기술 블로그/뉴스 글의 제목과 내용을 분석하고, **반드시 한국어로만** 응답하세요.
영어/일본어/중국어 등 외국어 원문이라도 모든 응답은 한국어로 번역해야 합니다.

1. **title_ko**: 원문 제목이 한국어가 아니면 한국어로 자연스럽게 번역. 이미 한국어면 그대로.
2. **summary**: 핵심 내용을 1~2문장으로 간결하게 한국어 요약.
3. **insight**: "왜 읽어볼 만한지" 또는 "어떤 점이 흥미로운지"를 1문장으로 한국어 코멘트.

반드시 아래 JSON 형식으로만 응답하세요 (마크다운 코드블록 없이):
{{"title_ko": "한국어 제목", "summary": "한국어 요약", "insight": "한국어 인사이트"}}

---
제목: {title}
내용: {summary}
링크: {link}
"""
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()

        # 마크다운 코드블록 제거
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)
        return {
            "title_ko": result.get("title_ko", ""),
            "summary": result.get("summary", ""),
            "insight": result.get("insight", ""),
        }
    except (json.JSONDecodeError, Exception) as e:
        print(f"   ⚠️  Gemini 요약 실패: {e}")
        return {"title_ko": "", "summary": "", "insight": ""}


def pick_top_article(model: genai.GenerativeModel, all_entries: list[dict]) -> dict | None:
    """Gemini가 오늘의 추천 글 1개를 선정합니다."""
    if not all_entries:
        return None

    entries_text = "\n".join(
        f"- [{i}] {e['title']} ({e.get('feed_name', '')})"
        for i, e in enumerate(all_entries)
    )

    prompt = f"""아래 기술 블로그/뉴스 목록 중에서 개발자에게 가장 읽어볼 만한 글 1개를 골라주세요.

반드시 아래 JSON 형식으로만 응답하세요 (마크다운 코드블록 없이):
{{"index": 0, "reason": "추천 이유 1문장"}}

---
{entries_text}
"""
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()

        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)
        idx = int(result.get("index", 0))
        if 0 <= idx < len(all_entries):
            return {
                "entry": all_entries[idx],
                "reason": result.get("reason", ""),
            }
    except (json.JSONDecodeError, Exception) as e:
        print(f"⚠️  오늘의 추천 선정 실패: {e}")

    return None


# ── RSS / DB 유틸 ──────────────────────────────────────────
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
            "summary": entry.get("summary", "")[:500],
            "published": entry.get("published", ""),
            "feed_title": feed.feed.get("title", url),
        })
    return items


def get_category_emoji(category: str, emoji_map: dict) -> str:
    """카테고리에 해당하는 이모지를 반환합니다."""
    return emoji_map.get(category, emoji_map.get("default", "📄"))


def _strip_html(text: str) -> str:
    """HTML 태그를 제거하고 공백을 정리합니다."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Slack 메시지 빌드 ──────────────────────────────────────
def build_slack_blocks(feed_name: str, category_emoji: str, entries: list[dict]) -> dict:
    """Slack Block Kit 형식의 메시지를 생성합니다 (불릿 포맷)."""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{category_emoji} {feed_name}",
                "emoji": True,
            },
        },
        {"type": "divider"},
    ]

    for entry in entries:
        title = entry["title"]
        link = entry["link"]
        ai_title_ko = entry.get("ai_title_ko", "")
        ai_summary = entry.get("ai_summary", "")
        ai_insight = entry.get("ai_insight", "")

        # 한국어 제목이 있고 원문과 다르면 함께 표시
        if ai_title_ko and ai_title_ko != title:
            lines = [f"*<{link}|{ai_title_ko}>*", f"    _{title}_"]
        else:
            lines = [f"*<{link}|{title}>*"]

        if ai_summary:
            lines.append(f"    📝 {ai_summary}")
        if ai_insight:
            lines.append(f"    💡 {ai_insight}")

        if not ai_summary and not ai_insight:
            raw_summary = _strip_html(entry["summary"])
            if len(raw_summary) > 150:
                raw_summary = raw_summary[:150] + "…"
            if raw_summary:
                lines.append(f"    {raw_summary}")

        text = "\n".join(lines)

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"🤖 RSS Bot + Gemini • {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            }
        ],
    })

    return {"blocks": blocks}


def build_top_pick_blocks(top_pick: dict) -> dict:
    """오늘의 추천 글 Slack 메시지를 생성합니다."""
    entry = top_pick["entry"]
    reason = top_pick["reason"]

    display_title = entry.get("ai_title_ko") or entry["title"]
    text_lines = [f"*<{entry['link']}|{display_title}>*"]
    if display_title != entry["title"]:
        text_lines.append(f"_{entry['title']}_")
    text_lines.extend([f"_{entry.get('feed_name', '')}_", ""])

    if entry.get("ai_summary"):
        text_lines.append(f"📝 {entry['ai_summary']}")
    if entry.get("ai_insight"):
        text_lines.append(f"💡 {entry['ai_insight']}")
    if reason:
        text_lines.append(f"")
        text_lines.append(f"🎯 *추천 이유:* {reason}")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "⭐ 오늘의 추천 글",
                "emoji": True,
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(text_lines)},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Gemini가 오늘의 글을 골랐습니다 • {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                }
            ],
        },
    ]

    return {"blocks": blocks}


# ── Slack 전송 ─────────────────────────────────────────────
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


# ── 메인 ───────────────────────────────────────────────────
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

    emoji_map = config.get("category_emoji", {"default": "📄"})

    # Gemini 초기화
    gemini_model = init_gemini()

    max_items = config.get("max_items_per_feed", 5)
    sent_entries = load_sent_entries()
    total_new = 0
    all_new_entries = []  # 오늘의 추천 글 선정용

    for feed_cfg in feeds:
        url = feed_cfg["url"]
        name = feed_cfg.get("name", url)
        category = feed_cfg.get("category", "default")
        cat_emoji = get_category_emoji(category, emoji_map)

        print(f"\n🔍 피드 확인 중: {cat_emoji} {name}")

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

        # Gemini 요약 추가
        if gemini_model:
            for i, entry in enumerate(new_entries):
                print(f"   🧠 요약 중 ({i+1}/{len(new_entries)}): {entry['title'][:40]}...")
                result = summarize_entry(
                    gemini_model,
                    entry["title"],
                    entry["summary"],
                    entry["link"],
                )
                entry["ai_title_ko"] = result["title_ko"]
                entry["ai_summary"] = result["summary"]
                entry["ai_insight"] = result["insight"]
                # Rate limit 방지 (Gemini free tier)
                if i < len(new_entries) - 1:
                    time.sleep(1)

        # 오늘의 추천 글 후보에 추가
        for entry in new_entries:
            entry["feed_name"] = name
            all_new_entries.append(entry)

        # Slack으로 전송
        payload = build_slack_blocks(name, cat_emoji, new_entries)
        if send_to_slack(webhook_url, payload):
            for entry in new_entries:
                sent_entries[entry["id"]] = {
                    "title": entry["title"],
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }
            total_new += len(new_entries)
            print(f"   ✅ 전송 완료!")
        else:
            print(f"   ❌ 전송 실패!")

    # ── 오늘의 추천 글 전송 ──
    if gemini_model and all_new_entries:
        print(f"\n⭐ 오늘의 추천 글 선정 중...")
        top_pick = pick_top_article(gemini_model, all_new_entries)
        if top_pick:
            print(f"   🏆 추천: {top_pick['entry']['title']}")
            payload = build_top_pick_blocks(top_pick)
            if send_to_slack(webhook_url, payload):
                print(f"   ✅ 오늘의 추천 글 전송 완료!")
            else:
                print(f"   ❌ 오늘의 추천 글 전송 실패!")

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
