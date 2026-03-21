# RSS Feed to Slack Bot

GitHub Actions를 이용하여 RSS 피드를 매일 자동으로 Slack 채널에 전송하는 봇입니다.

## 기능

- 여러 RSS/Atom 피드를 동시에 구독
- Slack Block Kit으로 깔끔한 메시지 포맷
- 중복 전송 방지 (이미 보낸 항목은 다시 보내지 않음)
- 매일 오전 9시(KST) 자동 실행 + 수동 실행 가능

## 빠른 시작

### 1. Slack Webhook 설정

1. [Slack API](https://api.slack.com/apps)에서 새 앱을 만듭니다
2. **Incoming Webhooks**를 활성화합니다
3. 원하는 채널에 Webhook을 추가하고 URL을 복사합니다

### 2. GitHub Secrets 등록

GitHub 저장소 → Settings → Secrets and variables → Actions에서:

| Secret 이름 | 값 |
|---|---|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

### 3. RSS 피드 설정

`config.yaml` 파일에서 구독할 피드를 추가/수정합니다:

```yaml
feeds:
  - name: "Hacker News"
    url: "https://hnrss.org/best"
  - name: "내 블로그"
    url: "https://myblog.com/rss.xml"
```

### 4. 실행

- **자동 실행**: 매일 오전 9시(KST)에 자동 실행됩니다
- **수동 실행**: GitHub → Actions → "RSS Feed to Slack" → Run workflow

## 프로젝트 구조

```
├── .github/workflows/
│   └── rss-to-slack.yaml    # GitHub Actions 워크플로우
├── scripts/
│   └── rss_to_slack.py      # RSS 파싱 및 Slack 전송 스크립트
├── data/
│   └── sent_entries.json    # 중복 방지용 전송 기록 (자동 생성)
├── config.yaml              # RSS 피드 및 설정
└── requirements.txt         # Python 의존성
```

## 로컬 테스트

```bash
pip install -r requirements.txt
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
python scripts/rss_to_slack.py
```

## 설정 옵션 (config.yaml)

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `max_items_per_feed` | 5 | 피드당 가져올 최대 항목 수 |
| `max_history` | 500 | 중복 방지 히스토리 보관 수 |

## 실행 시간 변경

`.github/workflows/rss-to-slack.yaml`에서 cron 표현식을 수정합니다:

```yaml
schedule:
  - cron: "0 0 * * *"   # 매일 KST 09:00 (UTC 00:00)
  - cron: "0 0 * * 1-5" # 평일만 KST 09:00
  - cron: "0 0,6 * * *" # 매일 KST 09:00, 15:00
```
