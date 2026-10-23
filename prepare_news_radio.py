
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


# NEWS_ANCHOR_GITHUB_V1
# Spoken opening text lives on GitHub.
# Date and time placeholders are resolved by ESP32.
# No server-side time snapshot is spoken.

ANCHOR_INTRO = {
    "enabled": True,
    "clock_source": "esp32",
    "timezone": "Asia/Dhaka",
    "version": 1,
    "lines": [
        "আসসালামু আলাইকুম।",
        "আমি মিসেস এলেক্সা।",
        "এলেক্সার বিগত ছত্রিশ ঘণ্টার সংবাদ বুলেটিনে আপনাকে স্বাগতম।",
        "আজ {englishDate} খ্রিষ্টাব্দ।",
        "বাংলা {banglaDate}।",
        "বাংলাদেশ সময় এখন {time}।",
        "সংবাদের উৎস হলো বিবিসি বাংলা, প্রথম আলো, জাগো নিউজ টুয়েন্টিফোর, বিডি টুয়েন্টিফোর লাইভ এবং বাংলাদেশ টুডে।",
        "চলুন, শুরু করা যাক।"
    ]
}

# NEWS_ANCHOR_GITHUB_DATE_V2
# Calendar dates are generated at GitHub build time.
# Live time remains an ESP32-side placeholder.

from datetime import datetime, date, timedelta, timezone
# Bangladesh standard time: UTC+06, no tzdata required.

BD_ZONE = timezone(timedelta(hours=6))
BD_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

BN_WEEKDAYS = [
    "সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার",
    "শুক্রবার", "শনিবার", "রবিবার"
]

BN_GREG_MONTHS = [
    "জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল",
    "মে", "জুন", "জুলাই", "আগস্ট",
    "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর"
]

BN_MONTHS = [
    "বৈশাখ", "জ্যৈষ্ঠ", "আষাঢ়", "শ্রাবণ",
    "ভাদ্র", "আশ্বিন", "কার্তিক", "অগ্রহায়ণ",
    "পৌষ", "মাঘ", "ফাল্গুন", "চৈত্র"
]


def bd_digits(value):
    return str(value).translate(BD_DIGITS)


def gregorian_bd(today):
    return (
        f"{BN_WEEKDAYS[today.weekday()]}, "
        f"{bd_digits(today.day)} "
        f"{BN_GREG_MONTHS[today.month - 1]} "
        f"{bd_digits(today.year)}"
    )


def is_leap(year):
    return (
        year % 400 == 0 or
        (year % 4 == 0 and year % 100 != 0)
    )


def bengali_calendar_bd(today):
    start_year = (
        today.year if
        (today.month, today.day) >= (4, 14)
        else today.year - 1
    )

    elapsed = (today - date(start_year, 4, 14)).days
    bangla_year = start_year - 593

    month_lengths = [
        31, 31, 31, 31, 31, 31,
        30, 30, 30, 30,
        30 if is_leap(start_year + 1) else 29,
        30
    ]

    for month_name, length in zip(BN_MONTHS, month_lengths):
        if elapsed < length:
            return (
                f"{bd_digits(elapsed + 1)} "
                f"{month_name} "
                f"{bd_digits(bangla_year)} বঙ্গাব্দ"
            )
        elapsed -= length

    raise ValueError("Bangla calendar date out of range")


def make_anchor_intro():
    now = datetime.now(BD_ZONE)
    today = now.date()

    english_date = gregorian_bd(today)
    bangla_date = bengali_calendar_bd(today)

    lines = [
        "আসসালামু আলাইকুম।",
        "আমি মিসেস এলেক্সা।",
        "এলেক্সার বিগত ছত্রিশ ঘণ্টার সংবাদ বুলেটিনে আপনাকে স্বাগতম।",
        f"আজ {english_date} খ্রিষ্টাব্দ।",
        f"বাংলা {bangla_date}।",
        "বাংলাদেশ সময় এখন {time}।",
        "সংবাদের উৎস হলো বিবিসি বাংলা, প্রথম আলো, "
        "জাগো নিউজ টুয়েন্টিফোর, বিডি টুয়েন্টিফোর লাইভ "
        "এবং বাংলাদেশ টুডে।",
        "চলুন, শুরু করা যাক।"
    ]

    return {
        "enabled": True,
        "version": 2,
        "date_source": "github",
        "clock_source": "esp32",
        "timezone": "Asia/Dhaka",
        "date_generated_for": today.isoformat(),
        "english_date": english_date,
        "bangla_date": bangla_date,
        "lines": lines
    }

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
        "anchor_intro": make_anchor_intro(),
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
