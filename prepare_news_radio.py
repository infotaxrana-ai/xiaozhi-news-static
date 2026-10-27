
#!/usr/bin/env python3
"""Xiaozhi News Radio v2 - Random Mixed Sources."""

import json
import random
import re

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "news.json"
OUTPUT = ROOT / "data" / "news_radio.json"

PART_CHARS = 520
MAX_STORIES = 120
MAX_BYTES = 340000

BD_ZONE = timezone(timedelta(hours=6))
RNG = random.SystemRandom()

BN_DIGITS = str.maketrans(
    "0123456789", "০১২৩৪৫৬৭৮৯"
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


def leap(year):
    return (
        year % 400 == 0
        or (year % 4 == 0 and year % 100 != 0)
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

    days = (
        [31] * 6
        + [30] * 4
        + [30 if leap(start + 1) else 29, 30]
    )

    for month, count in zip(BN_MONTHS, days):
        if elapsed < count:
            return (
                f"{bengali(elapsed + 1)} "
                f"{month} "
                f"{bengali(start - 593)} বঙ্গাব্দ"
            )

        elapsed -= count

    raise ValueError("Invalid Bengali date")


def intro():
    today = datetime.now(BD_ZONE).date()

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
            "এলেক্সার বিগত ছত্রিশ ঘণ্টার সংবাদ বুলেটিনে রানা ভাই আপনাকে স্বাগতম।",
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
        r"\s+", " ", str(value or "")
    ).strip()


def parts_of(value):
    text = normalize(value)

    if not text:
        return [
            "এই সংবাদের বিস্তারিত তথ্য "
            "সংবাদ উৎসে পাওয়া যায়নি।"
        ]

    parts = []
    current = ""

    for word in text.split():
        while len(word) > PART_CHARS:
            if current:
                parts.append(current)
                current = ""

            parts.append(word[:PART_CHARS])
            word = word[PART_CHARS:]

        if not word:
            continue

        if not current:
            current = word

        elif len(current) + len(word) + 1 <= PART_CHARS:
            current += " " + word

        else:
            parts.append(current)
            current = word

    if current:
        parts.append(current)

    return parts


def mixed_sources(articles):
    """
    Randomly interleave different publishers.

    Avoid consecutive stories from the same source
    whenever another source has stories available.
    """

    groups = defaultdict(list)

    for article in articles:
        source = normalize(
            article.get("source") or "অজানা উৎস"
        )

        groups[source].append(article)

    # Keep fresher stories toward the front
    # of each publisher's queue.
    for items in groups.values():
        items.sort(
            key=lambda a: a.get("published") or 0,
            reverse=True
        )

        # Shuffle inside small freshness windows.
        for i in range(0, len(items), 5):
            block = items[i:i + 5]
            RNG.shuffle(block)
            items[i:i + 5] = block

    positions = {
        name: 0 for name in groups
    }

    output = []
    previous_source = None

    while len(output) < MAX_STORIES:
        available = [
            name
            for name, items in groups.items()
            if positions[name] < len(items)
        ]

        if not available:
            break

        alternatives = [
            name for name in available
            if name != previous_source
        ]

        if alternatives:
            available = alternatives

        # Prioritize sources represented less often
        # in the output, with random tie-breaking.
        played = Counter(
            item.get("source") for item in output
        )

        smallest = min(
            played[name] for name in available
        )

        choices = [
            name for name in available
            if played[name] == smallest
        ]

        chosen = RNG.choice(choices)

        output.append(
            groups[chosen][positions[chosen]]
        )

        positions[chosen] += 1
        previous_source = chosen

    return output


def make_story(article, number):
    title = normalize(article.get("title"))
    url = str(article.get("url") or "")

    if not title or not url:
        return None

    parts = parts_of(article.get("detail"))

    return {
        "id": number,
        "title": title,
        "source": normalize(article.get("source")),
        "category": normalize(
            article.get("category") or "বাংলাদেশ"
        ),
        "url": url,
        "published": article.get("published"),
        "published_iso": article.get("published_iso"),
        "part_count": len(parts),
        "parts": parts
    }


def main():
    raw = json.loads(
        SOURCE.read_text(encoding="utf-8")
    )

    articles = raw.get("articles", [])

    if not articles:
        raise SystemExit("ERROR: No news articles")

    valid = [
        article for article in articles
        if article.get("title") and article.get("url")
    ]

    ordered = mixed_sources(valid)

    stories = []

    for article in ordered:
        story = make_story(
            article, len(stories) + 1
        )

        if story:
            stories.append(story)

    if not stories:
        raise SystemExit("ERROR: No valid stories")

    payload = {
        "schema_version": 1,
        "radio_version": "3.9-compatible-data-v1",
        "source_generated_at": raw.get("generated_at"),
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

    # Keep complete detail parts and stay below
    # the firmware's existing download budget.
    while len(data) > MAX_BYTES and len(stories) > 1:
        stories.pop()
        payload["story_count"] = len(stories)
        data = encode()

    if len(data) > MAX_BYTES:
        raise SystemExit(
            "ERROR: News Radio JSON exceeds 340KB"
        )

    # Keep IDs sequential after size trimming.
    for index, story in enumerate(stories, start=1):
        story["id"] = index

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT.write_bytes(encode())

    print("NEWS RADIO BUILDER SUCCESS")
    print("RADIO ORDER: RANDOM MIXED SOURCES")
    print("Stories:", len(stories))

    print(
        "Source distribution:",
        dict(Counter(s["source"] for s in stories))
    )

    print(
        "First 15 sources:",
        [s["source"] for s in stories[:15]]
    )

    print(
        "Consecutive same-source pairs:",
        sum(
            stories[i]["source"] == stories[i - 1]["source"]
            for i in range(1, len(stories))
        )
    )

    print(
        "Total parts:",
        sum(s["part_count"] for s in stories)
    )

    print("JSON bytes:", len(encode()))


if __name__ == "__main__":
    main()
