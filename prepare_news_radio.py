
#!/usr/bin/env python3
"""Xiaozhi radio JSON builder; existing firmware schema v1."""

import json
import re

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "news.json"
OUTPUT = ROOT / "data" / "news_radio.json"

PART_CHARS = 520
MAX_STORIES = 120
MAX_BYTES = 340000

BD_ZONE = timezone(timedelta(hours=6))

BN_DIGITS = str.maketrans(
    "0123456789",
    "০১২৩৪৫৬৭৮৯"
)

BN_WEEKDAYS = [
    "সোমবার", "মঙ্গলবার", "বুধবার",
    "বৃহস্পতিবার", "শুক্রবার",
    "শনিবার", "রবিবার"
]

BN_GREG_MONTHS = [
    "জানুয়ারি", "ফেব্রুয়ারি", "মার্চ",
    "এপ্রিল", "মে", "জুন",
    "জুলাই", "আগস্ট", "সেপ্টেম্বর",
    "অক্টোবর", "নভেম্বর", "ডিসেম্বর"
]

BN_MONTHS = [
    "বৈশাখ", "জ্যৈষ্ঠ", "আষাঢ়",
    "শ্রাবণ", "ভাদ্র", "আশ্বিন",
    "কার্তিক", "অগ্রহায়ণ", "পৌষ",
    "মাঘ", "ফাল্গুন", "চৈত্র"
]

INTRO_URL = (
    "https://raw.githubusercontent.com/"
    "infotaxrana-ai/xiaozhi-news-static/"
    "main/intro.mp3"
)


def bengali(value):
    return str(value).translate(BN_DIGITS)


def leap(y):
    return (
        y % 400 == 0
        or (
            y % 4 == 0
            and y % 100 != 0
        )
    )


def bangla_date(today):
    start = (
        today.year
        if (today.month, today.day) >= (4, 14)
        else today.year - 1
    )

    elapsed = (
        today - date(start, 4, 14)
    ).days

    month_days = (
        [31] * 6
        + [30] * 4
        + [
            30 if leap(start + 1) else 29,
            30
        ]
    )

    for month, days in zip(
        BN_MONTHS,
        month_days
    ):
        if elapsed < days:
            return (
                f"{bengali(elapsed + 1)} "
                f"{month} "
                f"{bengali(start - 593)} বঙ্গাব্দ"
            )

        elapsed -= days

    raise ValueError("Invalid Bengali date")


def intro():
    today = datetime.now(
        BD_ZONE
    ).date()

    english_date = (
        f"{BN_WEEKDAYS[today.weekday()]}, "
        f"{bengali(today.day)} "
        f"{BN_GREG_MONTHS[today.month - 1]} "
        f"{bengali(today.year)}"
    )

    local_date = bangla_date(today)

    return {
        "enabled": True,
        "version": 2,
        "date_source": "github",
        "clock_source": "esp32",
        "timezone": "Asia/Dhaka",
        "date_generated_for": today.isoformat(),
        "english_date": english_date,
        "bangla_date": local_date,
        "lines": [
            "আসসালামু আলাইকুম।",
            "আমি মিসেস এলেক্সা।",
            "এলেক্সার বিগত ছত্রিশ ঘণ্টার সংবাদ বুলেটিনে আপনাকে স্বাগতম।",
            f"আজ {english_date} খ্রিষ্টাব্দ।",
            f"বাংলা {local_date}।",
            "বাংলাদেশ সময় এখন {time}।",
            (
                "সংবাদের উৎস হলো বিবিসি বাংলা, "
                "প্রথম আলো, জাগো নিউজ টুয়েন্টিফোর, "
                "বিডি টুয়েন্টিফোর লাইভ এবং বাংলাদেশ টুডে।"
            ),
            "চলুন, শুরু করা যাক।"
        ]
    }


def normalize(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "")
    ).strip()


def parts_of(value):
    text = normalize(value)

    if not text:
        return [
            "এই সংবাদের বিস্তারিত তথ্য "
            "সংবাদ উৎসে পাওয়া যায়নি।"
        ]

    sentences = re.split(
        r"(?<=[।!?])\s+",
        text
    )

    parts = []
    current = ""

    for sentence in sentences:
        for word in sentence.split():

            while len(word) > PART_CHARS:
                if current:
                    parts.append(current)
                    current = ""

                parts.append(
                    word[:PART_CHARS]
                )

                word = word[PART_CHARS:]

            if not word:
                continue

            if not current:
                current = word

            elif (
                len(current)
                + len(word)
                + 1
                <= PART_CHARS
            ):
                current += " " + word

            else:
                parts.append(current)
                current = word

    if current:
        parts.append(current)

    return parts


def main():
    source = json.loads(
        SOURCE.read_text(encoding="utf-8")
    )

    articles = source.get(
        "articles",
        []
    )

    if not articles:
        raise SystemExit(
            "ERROR: No news articles"
        )

    stories = []

    for a in articles[:MAX_STORIES]:

        title = normalize(
            a.get("title")
        )

        url = str(
            a.get("url") or ""
        )

        if not title or not url:
            continue

        parts = parts_of(
            a.get("detail")
        )

        stories.append({
            "id": len(stories) + 1,
            "title": title,
            "source": normalize(
                a.get("source")
            ),
            "category": normalize(
                a.get("category")
                or "বাংলাদেশ"
            ),
            "url": url,
            "published": a.get("published"),
            "published_iso": a.get("published_iso"),
            "part_count": len(parts),
            "parts": parts
        })

    if not stories:
        raise SystemExit(
            "ERROR: No valid stories"
        )

    payload = {
        "schema_version": 1,
        "radio_version": "3.9-compatible-data-v1",
        "source_generated_at": source.get(
            "generated_at"
        ),
        "intro_url": INTRO_URL,
        "anchor_intro": intro(),
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

    def encode():
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":")
            ) + "\n"
        ).encode("utf-8")

    data = encode()

    # Never exceed the ESP32 download budget.
    # Preserve each retained story's complete details.
    # Drop oldest stories rather than cutting details.

    while (
        len(data) > MAX_BYTES
        and len(stories) > 1
    ):
        stories.pop()

        payload["story_count"] = len(stories)

        data = encode()

    if len(data) > MAX_BYTES:
        raise SystemExit(
            "ERROR: Radio JSON exceeds 340KB"
        )

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT.write_bytes(data)

    print("NEWS RADIO BUILDER SUCCESS")
    print("Stories:", len(stories))

    print(
        "Total parts:",
        sum(
            s["part_count"]
            for s in stories
        )
    )

    print(
        "Multi-part stories:",
        sum(
            s["part_count"] > 1
            for s in stories
        )
    )

    print(
        "JSON bytes:",
        len(data)
    )


if __name__ == "__main__":
    main()
