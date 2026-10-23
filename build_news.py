
#!/usr/bin/env python3
"""Xiaozhi Bengali news collector. Python standard library only."""

import concurrent.futures
import datetime as dt
import email.utils
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urlunparse

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
WINDOW = dt.timedelta(hours=36)

OUT = Path(__file__).resolve().parent / "data" / "news.json"

MAX_ARTICLES = 120
MAX_REPORT_CHARS = 2400
MAX_HTML_BYTES = 1_500_000
MAX_RSS_BYTES = 2_000_000
MAX_ARTICLE_REQUESTS = 100

MIN_RSS_DETAIL = 1150
MIN_EXTRA_GAIN = 80

SOURCES = [
    ("প্রথম আলো", "https://www.prothomalo.com/feed/", "bn"),
    ("জাগোনিউজ২৪", "https://www.jagonews24.com/rss/rss.xml", "bn"),
    ("বিডি২৪লাইভ", "https://www.bd24live.com/feed", "bn"),
    ("বিবিসি বাংলা", "https://feeds.bbci.co.uk/bengali/rss.xml", "bn"),
    ("বাংলাদেশ টুডে", "https://thebangladeshtoday.com/feed/", "en"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; XiaozhiNewsCollector/3.0)",
    "Accept": "text/html,application/rss+xml,application/xml,*/*",
    "Accept-Language": "bn-BD,bn;q=0.9,en;q=0.5",
}

BAD = {
    "script", "style", "nav", "footer", "header",
    "aside", "form", "svg", "noscript", "iframe", "button"
}


def clean(value, limit=MAX_REPORT_CHARS):
    value = html.unescape(
        re.sub(r"<[^>]*>", " ", str(value or ""))
    )
    return re.sub(r"\s+", " ", value).strip()[:limit]


def category(value):
    value = value.lower()

    rules = [
        ("রাজনীতি", r"রাজনীতি|নির্বাচন|সংসদ|ভোট"),
        ("খেলাধুলা", r"ক্রিকেট|ফুটবল|খেলাধুলা|ম্যাচ|বিশ্বকাপ|বিসিবি"),
        ("প্রযুক্তি", r"প্রযুক্তি|বিজ্ঞান|কৃত্রিম বুদ্ধিমত্তা|স্মার্টফোন|ইন্টারনেট|এআই"),
        ("অর্থনীতি", r"অর্থনীতি|বাণিজ্য|ব্যবসা|শেয়ারবাজার|ব্যাংক|মূল্যস্ফীতি"),
        ("আন্তর্জাতিক", r"আন্তর্জাতিক|বিশ্ব|যুক্তরাষ্ট্র|রাশিয়া|চীন|গাজা|ইসরায়েল"),
        ("বিনোদন", r"বিনোদন|সিনেমা|অভিনেতা|অভিনেত্রী|চলচ্চিত্র"),
    ]

    for name, words in rules:
        if re.search(words, value):
            return name

    return "বাংলাদেশ"


def date_from(value):
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            return None

    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)

    return d.astimezone(UTC)


def field(node, *names):
    for name in names:
        found = node.find(name)

        if found is not None:
            value = "".join(found.itertext()).strip()
            if value:
                return value

    return ""


def safe_url(url, feed):
    try:
        u = urlparse(url)
        source = urlparse(feed)

        if (
            u.scheme != "https"
            or not u.hostname
            or not source.hostname
        ):
            return ""

        if (
            u.port not in (None, 443)
            or u.username
            or u.password
        ):
            return ""

        host = u.hostname.lower()
        root = source.hostname.lower().removeprefix("www.")

        if host != root and not host.endswith("." + root):
            return ""

        if u.path.lower().endswith(
            (".pdf", ".mp3", ".mp4", ".zip")
        ):
            return ""

        return urlunparse((
            "https",
            u.netloc,
            u.path or "/",
            "",
            u.query,
            ""
        ))

    except ValueError:
        return ""


def fetch(url, limit, timeout=10):
    req = urllib.request.Request(
        url,
        headers=HEADERS
    )

    with urllib.request.urlopen(
        req,
        timeout=timeout
    ) as res:

        if urlparse(res.geturl()).scheme != "https":
            raise ValueError("Insecure redirect")

        content = res.read(limit + 1)

        if len(content) > limit:
            raise ValueError("Response too large")

        return content, res.headers, res.geturl()


class BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.stack = []
        self.paragraphs = []
        self.current = []
        self.scripts = []
        self.in_jsonld = False
        self.in_p = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.stack.append(tag)

        if (
            tag == "script"
            and "ld+json" in attrs.get("type", "").lower()
        ):
            self.in_jsonld = True
            self.scripts.append("")

        if tag == "p" and not self.in_p:
            self.in_p = True
            self.current = []

    def handle_data(self, data):
        if self.in_jsonld and self.scripts:
            self.scripts[-1] += data

        if (
            self.in_p
            and not any(t in BAD for t in self.stack)
        ):
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            p = clean("".join(self.current), 1800)

            if len(p) >= 40:
                self.paragraphs.append(p)

            self.in_p = False
            self.current = []

        if tag == "script":
            self.in_jsonld = False

        if tag in self.stack:
            i = (
                len(self.stack) - 1
                - self.stack[::-1].index(tag)
            )
            self.stack = self.stack[:i]


def ld_bodies(value):
    if isinstance(value, list):
        for x in value:
            yield from ld_bodies(x)

    if isinstance(value, dict):
        body = value.get("articleBody")

        if isinstance(body, str):
            yield clean(body)

        for key in ("@graph", "mainEntity"):
            if key in value:
                yield from ld_bodies(value[key])


def extract(article):
    url = safe_url(
        article["url"],
        article["source_feed"]
    )

    if not url:
        return ""

    try:
        raw, headers, final = fetch(
            url,
            MAX_HTML_BYTES,
            9
        )

        if not safe_url(
            final,
            article["source_feed"]
        ):
            return ""

        if "html" not in headers.get(
            "Content-Type", ""
        ).lower():
            return ""

        charset = (
            headers.get_content_charset()
            or "utf-8"
        )

        try:
            page = raw.decode(
                charset,
                errors="replace"
            )
        except LookupError:
            page = raw.decode(
                "utf-8",
                errors="replace"
            )

        parser = BodyParser()
        parser.feed(page)

        candidates = []

        for script in parser.scripts:
            try:
                candidates.extend(
                    ld_bodies(json.loads(script))
                )
            except (ValueError, TypeError):
                pass

        seen = set()
        paragraphs = []

        for paragraph in parser.paragraphs:
            token = re.sub(
                r"\s+",
                "",
                paragraph
            )

            if token not in seen:
                seen.add(token)
                paragraphs.append(paragraph)

            if len(paragraphs) >= 18:
                break

        if paragraphs:
            candidates.append(
                clean(" ".join(paragraphs))
            )

        return max(
            candidates,
            key=len,
            default=""
        )

    except Exception:
        return ""


def get_source(spec):
    name, feed, language = spec

    try:
        raw, _, _ = fetch(
            feed,
            MAX_RSS_BYTES,
            12
        )

        root = ET.fromstring(raw)

        nodes = (
            root.findall(".//item")
            or root.findall(
                "{http://www.w3.org/2005/Atom}entry"
            )
        )

        result = []

        for node in nodes[:100]:
            title = clean(
                field(
                    node,
                    "title",
                    "{http://www.w3.org/2005/Atom}title"
                ),
                300
            )

            link = field(node, "link")

            if not link:
                el = node.find(
                    "{http://www.w3.org/2005/Atom}link"
                )

                link = (
                    el.get("href", "")
                    if el is not None
                    else ""
                )

            published = date_from(
                field(
                    node,
                    "pubDate",
                    "published",
                    "updated",
                    "{http://www.w3.org/2005/Atom}published",
                    "{http://www.w3.org/2005/Atom}updated",
                    "{http://purl.org/dc/elements/1.1/}date"
                )
            )

            descriptions = [
                field(node, "description"),
                field(
                    node,
                    "{http://purl.org/rss/1.0/modules/content/}encoded"
                ),
                field(
                    node,
                    "{http://www.w3.org/2005/Atom}summary"
                ),
                field(
                    node,
                    "{http://www.w3.org/2005/Atom}content"
                ),
            ]

            detail = max(
                (clean(x) for x in descriptions),
                key=len,
                default=""
            )

            safe = safe_url(link, feed)

            if (
                not title
                or not safe
                or published is None
                or not (
                    NOW - WINDOW
                    <= published
                    <= NOW + dt.timedelta(minutes=5)
                )
            ):
                continue

            result.append({
                "title": title,
                "url": safe,
                "source": name,
                "source_feed": feed,
                "language": language,
                "category": category(
                    title + " " + detail
                ),
                "detail": detail,
                "detail_origin": "rss",
                "published": int(published.timestamp()),
                "published_iso": published.isoformat()
            })

        return name, result, None

    except Exception as ex:
        return (
            name,
            [],
            f"{type(ex).__name__}: {str(ex)[:120]}"
        )


def enrich(item):
    article = dict(item)

    if len(article["detail"]) >= MIN_RSS_DETAIL:
        return article, False

    body = extract(article)

    if (
        len(body)
        >= len(article["detail"]) + MIN_EXTRA_GAIN
    ):
        article["detail"] = body
        article["detail_origin"] = "article"

        article["category"] = category(
            article["title"] + " " + body
        )

        return article, True

    return article, False


def build():
    previous = {}

    if OUT.exists():
        try:
            previous = json.loads(
                OUT.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            pass

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=5
    ) as pool:
        gathered = list(
            pool.map(get_source, SOURCES)
        )

    unique = {}

    for _, items, _ in gathered:
        for a in items:
            if a["language"] != "bn":
                continue

            key = a["url"].split("?")[0]

            if (
                key not in unique
                or len(a["detail"])
                > len(unique[key]["detail"])
            ):
                unique[key] = a

    if not unique:
        for a in previous.get("articles", []):
            if (
                a.get("language") == "bn"
                and a.get("published", 0)
                >= int((NOW - WINDOW).timestamp())
            ):
                unique[
                    a["url"].split("?")[0]
                ] = a

    articles = sorted(
        unique.values(),
        key=lambda a: a["published"],
        reverse=True
    )[:MAX_ARTICLES]

    candidates = [
        (i, a)
        for i, a in enumerate(articles)
        if (
            len(a.get("detail", "")) < MIN_RSS_DETAIL
            and a.get("source_feed")
        )
    ][:MAX_ARTICLE_REQUESTS]

    extracted_count = 0

    if candidates:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=6
        ) as pool:
            enriched = list(
                pool.map(
                    enrich,
                    [a for _, a in candidates]
                )
            )

        for (i, _), (a, changed) in zip(
            candidates,
            enriched
        ):
            articles[i] = a
            extracted_count += int(changed)

    for a in articles:
        a.pop("source_feed", None)

    if not articles and previous.get("articles"):
        print(
            "Feeds unavailable; preserved previous data"
        )
        return

    payload = {
        "schema_version": 1,
        "collector_version": "3.0",
        "generated_at": NOW.isoformat(),
        "lookback_hours": 36,
        "max_report_chars": MAX_REPORT_CHARS,
        "count": len(articles),
        "articles_extracted": extracted_count,
        "sources": [
            {
                "source": name,
                "ok": bool(items),
                "count": len(items),
                "error": error
            }
            for name, items, error in gathered
        ],
        "articles": articles
    }

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print(
        f"News count: {len(articles)} | "
        f"New full articles: {extracted_count}"
    )

    print(
        "Detailed >= 520 chars:",
        sum(
            len(a["detail"]) >= 520
            for a in articles
        )
    )

    print("Saved:", OUT)


if __name__ == "__main__":
    build()
