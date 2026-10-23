
#!/usr/bin/env python3
"""Xiaozhi Bengali News Collector v3.1 - Standard Library Only."""

import concurrent.futures as futures
import datetime as dt
import email.utils
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "news.json"

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
WINDOW = dt.timedelta(hours=36)

MAX_ARTICLES = 120
MAX_REPORT_CHARS = 2400
MIN_EXTRACT_GAIN = 80
MAX_ARTICLE_REQUESTS = 100

SOURCES = [
    ("প্রথম আলো", "https://www.prothomalo.com/feed/", "bn"),
    ("জাগোনিউজ২৪", "https://www.jagonews24.com/rss/rss.xml", "bn"),
    ("বিডি২৪লাইভ", "https://www.bd24live.com/feed", "bn"),
    ("বিবিসি বাংলা", "https://feeds.bbci.co.uk/bengali/rss.xml", "bn"),
    ("বাংলাদেশ টুডে", "https://thebangladeshtoday.com/feed/", "en"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/rss+xml,application/xml,*/*"
    ),
    "Accept-Language": "bn-BD,bn;q=0.9,en;q=0.5",
}

BAD_TAGS = {
    "script", "style", "nav", "footer",
    "header", "aside", "form", "svg",
    "noscript", "iframe", "button",
}

BAD_HINTS = re.compile(
    r"comment|related|share|social|advert|promo|"
    r"cookie|menu|sidebar|footer|author-bio|breadcrumb",
    re.I,
)

GOOD_HINTS = re.compile(
    r"article|story|post-content|entry-content|"
    r"news-content|article-body|story-body|"
    r"content-body|main-content",
    re.I,
)


def clean(text, limit=MAX_REPORT_CHARS):
    text = html.unescape(
        re.sub(r"<[^>]+>", " ", str(text or ""))
    )
    return re.sub(r"\s+", " ", text).strip()[:limit]


def classification(text):
    text = text.lower()

    rules = [
        ("রাজনীতি", r"রাজনীতি|নির্বাচন|সংসদ|ভোট"),
        (
            "খেলাধুলা",
            r"ক্রিকেট|ফুটবল|খেলাধুলা|ম্যাচ|বিশ্বকাপ|বিসিবি",
        ),
        (
            "প্রযুক্তি",
            r"প্রযুক্তি|বিজ্ঞান|কৃত্রিম বুদ্ধিমত্তা|"
            r"স্মার্টফোন|ইন্টারনেট|এআই",
        ),
        (
            "অর্থনীতি",
            r"অর্থনীতি|বাণিজ্য|ব্যবসা|"
            r"শেয়ারবাজার|ব্যাংক|মূল্যস্ফীতি",
        ),
        (
            "আন্তর্জাতিক",
            r"আন্তর্জাতিক|বিশ্ব|যুক্তরাষ্ট্র|"
            r"রাশিয়া|চীন|গাজা|ইসরায়েল",
        ),
        (
            "বিনোদন",
            r"বিনোদন|সিনেমা|অভিনেতা|"
            r"অভিনেত্রী|চলচ্চিত্র",
        ),
    ]

    for title, pattern in rules:
        if re.search(pattern, text):
            return title

    return "বাংলাদেশ"


def parsed_date(value):
    if not value:
        return None

    try:
        result = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            result = dt.datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)

    return result.astimezone(UTC)


def text_of(node, *names):
    for name in names:
        item = node.find(name)

        if item is not None:
            value = "".join(item.itertext()).strip()
            if value:
                return value

    return ""


def safe_link(link, feed):
    try:
        u = urlparse(link)
        f = urlparse(feed)

        if (
            u.scheme != "https"
            or not u.hostname
            or not f.hostname
        ):
            return ""

        if (
            u.port not in (None, 443)
            or u.username
            or u.password
        ):
            return ""

        root = f.hostname.lower().removeprefix("www.")
        hostname = u.hostname.lower()

        if (
            hostname != root
            and not hostname.endswith("." + root)
        ):
            return ""

        if u.path.lower().endswith(
            (".pdf", ".zip", ".mp4", ".mp3")
        ):
            return ""

        return urlunparse((
            "https",
            u.netloc,
            u.path or "/",
            "",
            u.query,
            "",
        ))

    except (ValueError, TypeError):
        return ""


def fetch(url, cap, timeout=12):
    request = urllib.request.Request(
        url,
        headers=HEADERS,
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as response:

        raw = response.read(cap + 1)

        if len(raw) > cap:
            raise ValueError("Response too large")

        if urlparse(response.geturl()).scheme != "https":
            raise ValueError("Insecure redirect")

        return (
            raw,
            response.headers,
            response.geturl(),
        )


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.stack = []
        self.in_p = False
        self.pieces = []
        self.paragraphs = []
        self.jsonld = []
        self.jsonld_active = False
        self.meta = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = dict(attrs)

        hint = " ".join(
            str(attributes.get(k) or "")
            for k in (
                "class",
                "id",
                "itemprop",
                "role",
            )
        )

        self.stack.append((tag, hint))

        if (
            tag == "script"
            and "ld+json" in str(
                attributes.get("type", "")
            ).lower()
        ):
            self.jsonld.append("")
            self.jsonld_active = True

        if tag == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or ""
            ).lower()

            if key in (
                "og:description",
                "description",
                "twitter:description",
            ):
                self.meta.append(
                    clean(attributes.get("content", ""))
                )

        if tag == "p" and not self.in_p:
            self.in_p = True
            self.pieces = []

    def handle_data(self, data):
        if self.jsonld_active and self.jsonld:
            self.jsonld[-1] += data

        if self.in_p and not any(
            tag in BAD_TAGS or BAD_HINTS.search(hint)
            for tag, hint in self.stack
        ):
            self.pieces.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "p" and self.in_p:
            paragraph = clean(
                " ".join(self.pieces),
                1500,
            )

            if len(paragraph) >= 35:
                good = any(
                    t == "article" or GOOD_HINTS.search(h)
                    for t, h in self.stack
                )

                self.paragraphs.append(
                    (paragraph, good)
                )

            self.in_p = False
            self.pieces = []

        if tag == "script":
            self.jsonld_active = False

        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def jsonld_articles(obj):
    if isinstance(obj, list):
        for item in obj:
            yield from jsonld_articles(item)

    elif isinstance(obj, dict):
        kinds = obj.get("@type", [])

        if isinstance(kinds, str):
            kinds = [kinds]

        if any(
            isinstance(k, str)
            and (
                "Article" in k
                or k == "NewsArticle"
            )
            for k in kinds
        ):
            body = obj.get("articleBody")

            if isinstance(body, str):
                yield clean(body)

        for key in (
            "@graph",
            "mainEntity",
            "mainEntityOfPage",
        ):
            if key in obj:
                yield from jsonld_articles(obj[key])


def extract_article(article):
    url = safe_link(
        article["url"],
        article["source_feed"],
    )

    if not url:
        return "", "url_rejected"

    try:
        raw, headers, final_url = fetch(
            url,
            1_500_000,
            timeout=9,
        )

        if not safe_link(
            final_url,
            article["source_feed"],
        ):
            return "", "redirect_rejected"

        if "html" not in headers.get(
            "Content-Type", ""
        ).lower():
            return "", "not_html"

        encoding = (
            headers.get_content_charset()
            or "utf-8"
        )

        try:
            page = raw.decode(
                encoding,
                errors="replace",
            )
        except LookupError:
            page = raw.decode(
                "utf-8",
                errors="replace",
            )

        parser = ArticleParser()
        parser.feed(page)

        candidates = []

        # Extract publisher's structured NewsArticle body.
        for item_json in parser.jsonld:
            try:
                candidates.extend(
                    jsonld_articles(
                        json.loads(item_json)
                    )
                )
            except (ValueError, TypeError):
                pass

        # First prefer article-specific paragraphs.
        # Then use general paragraphs as fallback.
        for preferred in (True, False):
            seen = set()
            paragraphs = []

            for paragraph, good in parser.paragraphs:
                if preferred and not good:
                    continue

                key = re.sub(
                    r"\s+",
                    "",
                    paragraph,
                )

                if key not in seen:
                    seen.add(key)
                    paragraphs.append(paragraph)

                if len(paragraphs) >= 18:
                    break

            if paragraphs:
                candidates.append(
                    clean(" ".join(paragraphs))
                )

        candidates.extend(parser.meta)

        best = max(
            candidates,
            key=len,
            default="",
        )

        if len(best) < 120:
            return "", "no_article_body"

        return best, "ok"

    except Exception as error:
        return (
            "",
            "fetch_" + type(error).__name__,
        )


def load_feed(spec):
    name, feed, language = spec

    try:
        raw, _, _ = fetch(
            feed,
            2_000_000,
        )

        root = ET.fromstring(raw)

        items = (
            root.findall(".//item")
            or root.findall(
                "{http://www.w3.org/2005/Atom}entry"
            )
        )

        rows = []

        for node in items[:100]:
            title = clean(
                text_of(
                    node,
                    "title",
                    "{http://www.w3.org/2005/Atom}title",
                ),
                300,
            )

            link = text_of(node, "link")

            if not link:
                element = node.find(
                    "{http://www.w3.org/2005/Atom}link"
                )

                link = (
                    element.get("href", "")
                    if element is not None
                    else ""
                )

            published = parsed_date(
                text_of(
                    node,
                    "pubDate",
                    "published",
                    "updated",
                    "{http://www.w3.org/2005/Atom}published",
                    "{http://www.w3.org/2005/Atom}updated",
                    "{http://purl.org/dc/elements/1.1/}date",
                )
            )

            descriptions = [
                text_of(node, "description"),
                text_of(
                    node,
                    "{http://purl.org/rss/1.0/modules/content/}encoded",
                ),
                text_of(
                    node,
                    "{http://www.w3.org/2005/Atom}summary",
                ),
                text_of(
                    node,
                    "{http://www.w3.org/2005/Atom}content",
                ),
            ]

            detail = max(
                (clean(x) for x in descriptions),
                key=len,
                default="",
            )

            safe = safe_link(link, feed)

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

            rows.append({
                "title": title,
                "url": safe,
                "source": name,
                "source_feed": feed,
                "language": language,
                "category": classification(
                    title + " " + detail
                ),
                "detail": detail,
                "detail_origin": "rss",
                "published": int(
                    published.timestamp()
                ),
                "published_iso": published.isoformat(),
            })

        return name, rows, None

    except Exception as error:
        return (
            name,
            [],
            type(error).__name__
            + ": "
            + str(error)[:100],
        )


def enrich(article):
    previous = article.get(
        "detail",
        "",
    )

    body, status = extract_article(article)

    if len(body) >= len(previous) + MIN_EXTRACT_GAIN:
        article = dict(article)

        article["detail"] = body
        article["detail_origin"] = "article"
        article["category"] = classification(
            article["title"] + " " + body
        )

        return article, True, "improved"

    return (
        article,
        False,
        status if status != "ok" else "not_longer",
    )


def build():
    old = {}

    if OUTPUT.exists():
        try:
            old = json.loads(
                OUTPUT.read_text(
                    encoding="utf-8"
                )
            )
        except (ValueError, OSError):
            pass

    with futures.ThreadPoolExecutor(
        max_workers=5
    ) as pool:
        gathered = list(
            pool.map(
                load_feed,
                SOURCES,
            )
        )

    unique = {}

    for _, articles, _ in gathered:
        for article in articles:
            if article["language"] != "bn":
                continue

            key = article["url"].split("?")[0]

            if (
                key not in unique
                or len(article["detail"])
                > len(unique[key]["detail"])
            ):
                unique[key] = article

    # Preserve previously available fresh news
    # when all sources fail temporarily.
    if not unique:
        for article in old.get("articles", []):
            if (
                article.get("language") == "bn"
                and article.get("published", 0)
                >= int((NOW - WINDOW).timestamp())
            ):
                unique[
                    article["url"].split("?")[0]
                ] = article

    articles = sorted(
        unique.values(),
        key=lambda item: item["published"],
        reverse=True,
    )[:MAX_ARTICLES]

    candidates = [
        (index, article)
        for index, article in enumerate(articles)
        if (
            len(article.get("detail", "")) < 1800
            and article.get("source_feed")
        )
    ][:MAX_ARTICLE_REQUESTS]

    statuses = Counter()
    improved_count = 0

    if candidates:
        with futures.ThreadPoolExecutor(
            max_workers=8
        ) as pool:
            results = list(
                pool.map(
                    enrich,
                    [a for _, a in candidates],
                )
            )

        for (index, _), (article, success, status) in zip(
            candidates,
            results,
        ):
            articles[index] = article
            improved_count += int(success)
            statuses[status] += 1

    for article in articles:
        article.pop(
            "source_feed",
            None,
        )

    if not articles and old.get("articles"):
        print(
            "Feeds unavailable; preserving existing news.json"
        )
        return

    payload = {
        "schema_version": 1,
        "collector_version": "3.1",
        "generated_at": NOW.isoformat(),
        "lookback_hours": 36,
        "max_report_chars": MAX_REPORT_CHARS,
        "count": len(articles),
        "articles_extracted": improved_count,
        "extraction_attempts": len(candidates),
        "extraction_diagnostics": dict(statuses),
        "sources": [
            {
                "source": name,
                "ok": bool(items),
                "count": len(items),
                "error": error,
            }
            for name, items, error in gathered
        ],
        "articles": articles,
    }

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print("NEWS COLLECTOR v3.1")
    print("Stories:", len(articles))
    print("Extraction attempts:", len(candidates))
    print("Articles improved:", improved_count)

    print(
        "Details >= 520 chars:",
        sum(
            len(a["detail"]) >= 520
            for a in articles
        )
    )

    print(
        "Extraction diagnostic:",
        dict(statuses),
    )

    print("Saved:", OUTPUT)


if __name__ == "__main__":
    build()
