
#!/usr/bin/env python3
# XIAOZHI NEWS RADIO - ISOLATED BATCH BUILDER V1
# Source: data/news.json
# Output: data/news_radio.json
# Does not modify existing news collection or firmware.

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "news.json"
OUTPUT = ROOT / "data" / "news_radio.json"

PART_CHARS = 520
MAX_STORIES = 120

INTRO_URL = (
    "https://raw.githubusercontent.com/"
    "infotaxrana-ai/xiaozhi-news-static/"
    "main/intro.mp3"
)


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def split_into_parts(text, limit=PART_CHARS):
    text = normalize(text)
    if not text:
        return []

    sentences = re.split(r"(?<=[।!?])\s+", text)

    parts = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Long sentences are split on spaces first.
        words = sentence.split()
        chunks = []
        chunk = ""

        for word in words:
            if len(word) > limit:
                if chunk:
                    chunks.append(chunk)
                    chunk = ""

                for pos in range(0, len(word), limit):
                    chunks.append(word[pos:pos + limit])
                continue

            if not chunk:
                chunk = word
            elif len(chunk) + len(word) + 1 <= limit:
                chunk += " " + word
            else:
                chunks.append(chunk)
                chunk = word

        if chunk:
            chunks.append(chunk)

        for item in chunks:
            if not current:
                current = item
            elif len(current) + len(item) + 1 <= limit:
                current += " " + item
            else:
                parts.append(current)
                current = item

    if current:
        parts.append(current)

    return parts


def main():
    if not SOURCE.is_file():
        raise SystemExit("ERROR: data/news.json missing")

    source = json.loads(SOURCE.read_text(encoding="utf-8"))

    articles = source.get("articles")
    if not isinstance(articles, list) or not articles:
        raise SystemExit("ERROR: No source articles")

    stories = []

    for article in articles[:MAX_STORIES]:
        title = normalize(article.get("title"))
        detail = normalize(article.get("detail"))
        url = str(article.get("url") or "")
        publisher = normalize(article.get("source"))

        if not title or not url:
            continue

        parts = split_into_parts(detail)

        if not parts:
            parts = [
                "এই সংবাদের বিস্তারিত তথ্য "
                "সংবাদ উৎসে পাওয়া যায়নি।"
            ]

        if any(len(part) > PART_CHARS for part in parts):
            raise SystemExit("ERROR: Part size overflow")

        stories.append({
            "id": len(stories) + 1,
            "title": title,
            "source": publisher,
            "category": normalize(
                article.get("category") or "বাংলাদেশ"
            ),
            "url": url,
            "published": article.get("published"),
            "published_iso": article.get("published_iso"),
            "part_count": len(parts),
            "parts": parts
        })

    if not stories:
        raise SystemExit("ERROR: No valid stories")

    payload = {
        "schema_version": 1,
        "radio_version": "3.9-compatible-data-v1",
        "source_generated_at": source.get("generated_at"),
        "intro_url": INTRO_URL,
        "part_char_limit": PART_CHARS,
        "story_count": len(stories),
        "interactive": True,
        "requires_user_confirmation": True,
        "navigation": {
            "start": "first story",
            "continue": "next detail part",
            "skip": "skip remaining detail",
            "next": "next story",
            "stop": "end bulletin"
        },
        "stories": stories
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    total_parts = sum(x["part_count"] for x in stories)

    print("NEWS RADIO BATCH BUILDER SUCCESS")
    print("Stories:", len(stories))
    print("Parts:", total_parts)
    print("Maximum characters per part:", PART_CHARS)
    print("Intro URL:", INTRO_URL)
    print("Output:", OUTPUT)


if __name__ == "__main__":
    main()
