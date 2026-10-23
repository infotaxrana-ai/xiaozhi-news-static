
#!/usr/bin/env python3
# Xiaozhi Serverless Bengali News Collector v2.0
# RSS + Article Extraction + Safe Fallback
# Python Standard Library Only
# No Render, VPS or API Key required

import concurrent.futures
import datetime as dt
import email.utils
import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urljoin, urlunparse


UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
WINDOW = dt.timedelta(hours=36)

OUTPUT = Path(__file__).resolve().parent / "data" / "news.json"

MAX_ARTICLES = 120
MAX_REPORT_CHARS = 1650
MAX_RSS_BYTES = 2_000_000
MAX_HTML_BYTES = 1_500_000
MAX_ARTICLE_REQUESTS = 45

MIN_RSS_DETAIL = 500
MIN_EXTRACTED_GAIN = 100

SOURCES = [
    (
        "প্রথম আলো",
        "https://www.prothomalo.com/feed/",
        "bn",
    ),
    (
        "জাগোনিউজ২৪",
        "https://www.jagonews24.com/rss/rss.xml",
        "bn",
    ),
    (
        "বিডি২৪লাইভ",
        "https://www.bd24live.com/feed",
        "bn",
    ),
    (
        "বিবিসি বাংলা",
        "https://feeds.bbci.co.uk/bengali/rss.xml",
        "bn",
    ),
    (
        "বাংলাদেশ টুডে",
        "https://thebangladeshtoday.com/feed/",
        "en",
    ),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; "
        "XiaozhiNewsCollector/2.0)"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/rss+xml,application/atom+xml,"
        "application/xml,text/xml,*/*"
    ),
    "Accept-Language": "bn-BD,bn;q=0.9,en;q=0.7",
}

BLOCKED_TAGS = {
    "script", "style", "nav", "footer",
    "header", "aside", "form", "svg",
    "noscript", "iframe", "button",
}


def clean(value, limit=MAX_REPORT_CHARS):
    value = str(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()[:limit]


def category(text):
    text = str(text or "").lower()

    rules = [
        (
            "রাজনীতি",
            r"রাজনীতি|নির্বাচন|সংসদ|ভোট",
        ),
        (
            "খেলাধুলা",
            r"ক্রিকেট|ফুটবল|খেলাধুলা|ম্যাচ|"
            r"বিশ্বকাপ|বিসিবি",
        ),
        (
            "প্রযুক্তি",
            r"প্রযুক্তি|বিজ্ঞান|কৃত্রিম বুদ্ধিমত্তা|"
            r"স্মার্টফোন|ইন্টারনেট|এআই",
        ),
        (
            "অর্থনীতি",
            r"অর্থনীতি|বাণিজ্য|ব্যবসা|শেয়ারবাজার|"
            r"ব্যাংক|মূল্যস্ফীতি",
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

    for name, pattern in rules:
        if re.search(pattern, text):
            return name

    return "বাংলাদেশ"


def parse_date(value):
    if not value:
        return None

    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        pass

    try:
        parsed = dt.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def field(node, *names):
    for name in names:
        target = node.find(name)
        if target is not None:
            text = "".join(target.itertext()).strip()
            if text:
                return text
    return ""


def safe_article_url(url, source_url):
    try:
        target = urlparse(url)
        source = urlparse(source_url)

        if target.scheme != "https":
            return ""

        if not target.hostname or not source.hostname:
            return ""

        host = target.hostname.lower()
        allowed = source.hostname.lower()

        # Allow only the RSS source domain/subdomains.
        if host != allowed and not host.endswith("." + allowed):
            # www.example.com versus example.com
            root = allowed.removeprefix("www.")
            if host != root and not host.endswith("." + root):
                return ""

        if target.port not in (None, 443):
            return ""

        if target.username or target.password:
            return ""

        if target.path.lower().endswith(
            (".pdf", ".mp3", ".mp4", ".zip")
        ):
            return ""

        return urlunparse((
            "https",
            target.netloc,
            target.path or "/",
            "",
            target.query,
            "",
        ))
    except ValueError:
        return ""


def download(url, max_bytes, timeout=10):
    request = urllib.request.Request(
        url,
        headers=HEADERS,
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as response:

        final_url = response.geturl()
        parsed = urlparse(final_url)

        if parsed.scheme != "https":
            raise ValueError("Non-HTTPS redirect")

        content = response.read(max_bytes + 1)

        if len(content) > max_bytes:
            raise ValueError("Response too large")

        return content, response.headers, final_url


class ArticleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.paragraphs = []
        self.current = []
        self.in_p = 0
        self.jsonld = []
        self.in_jsonld = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        self.stack.append(tag)

        if tag == "script" and (
            "ld+json" in attrs.get("type", "").lower()
        ):
            self.in_jsonld = True
            self.jsonld.append("")

        if tag == "p":
            self.in_p += 1
            if self.in_p == 1:
                self.current = []

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "p" and self.in_p:
            self.in_p -= 1
            if self.in_p == 0:
                paragraph = clean(
                    "".join(self.current),
                    2000,
                )
                if len(paragraph) >= 35:
                    self.paragraphs.append(paragraph)
                self.current = []

        if tag == "script":
            self.in_jsonld = False

        if tag in self.stack:
            index = len(self.stack) - 1 - (
                self.stack[::-1].index(tag)
            )
            self.stack = self.stack[:index]

    def handle_data(self, data):
        if self.in_jsonld and self.jsonld:
            self.jsonld[-1] += data

        if self.in_p and not any(
            tag in BLOCKED_TAGS
            for tag in self.stack
        ):
            self.current.append(data)


def jsonld_bodies(obj):
    bodies = []

    if isinstance(obj, list):
        for item in obj:
            bodies.extend(jsonld_bodies(item))

    elif isinstance(obj, dict):
        article_body = obj.get("articleBody")

        if isinstance(article_body, str):
            value = clean(
                article_body,
                MAX_REPORT_CHARS,
            )
            if value:
                bodies.append(value)

        for key in ("@graph", "mainEntity"):
            if key in obj:
                bodies.extend(
                    jsonld_bodies(obj[key])
                )

    return bodies


def extract_article(url, source_url):
    safe = safe_article_url(
        url,
        source_url,
    )

    if not safe:
        return "", "URL not allowed"

    try:
        raw, headers, final_url = download(
            safe,
            MAX_HTML_BYTES,
            timeout=8,
        )

        if not safe_article_url(
            final_url,
            source_url,
        ):
            return "", "Redirect domain not allowed"

        content_type = headers.get(
            "Content-Type", ""
        ).lower()

        if "html" not in content_type:
            return "", "Not an HTML page"

        charset = headers.get_content_charset() or "utf-8"

        try:
            page = raw.decode(
                charset,
                errors="replace",
            )
        except LookupError:
            page = raw.decode(
                "utf-8",
                errors="replace",
            )

        parser = ArticleHTMLParser()
        parser.feed(page)

        candidates = []

        for script in parser.jsonld:
            try:
                obj = json.loads(script)
                candidates.extend(
                    jsonld_bodies(obj)
                )
            except (ValueError, TypeError):
                pass

        seen = set()
        paragraphs = []

        for paragraph in parser.paragraphs:
            normalized = re.sub(
                r"\s+",
                "",
                paragraph,
            )

            if normalized in seen:
                continue

            seen.add(normalized)
            paragraphs.append(paragraph)

            if len(paragraphs) >= 16:
                break

        if paragraphs:
            candidates.append(
                clean(
                    " ".join(paragraphs),
                    MAX_REPORT_CHARS,
                )
            )

        if not candidates:
            return "", "No article text found"

        best = max(
            candidates,
            key=len,
        )

        return best, None

    except Exception as exc:
        return "", (
            f"{type(exc).__name__}: "
            f"{str(exc)[:100]}"
        )


def get_source(source):
    name, url, language = source

    try:
        raw, _, _ = download(
            url,
            MAX_RSS_BYTES,
            timeout=12,
        )

        root = ET.fromstring(raw)

        items = root.findall(".//item")

        if not items:
            items = root.findall(
                "{http://www.w3.org/2005/Atom}entry"
            )

        articles = []

        for node in items[:100]:
            title = clean(
                field(
                    node,
                    "title",
                    "{http://www.w3.org/2005/Atom}title",
                ),
                300,
            )

            link = field(node, "link")

            if not link:
                element = node.find(
                    "{http://www.w3.org/2005/Atom}link"
                )
                if element is not None:
                    link = element.get("href", "")

            date = parse_date(
                field(
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
                field(node, "description"),
                field(
                    node,
                    "{http://purl.org/rss/1.0/modules/content/}encoded",
                ),
                field(
                    node,
                    "{http://www.w3.org/2005/Atom}summary",
                ),
                field(
                    node,
                    "{http://www.w3.org/2005/Atom}content",
                ),
            ]

            detail = max(
                (
                    clean(x, MAX_REPORT_CHARS)
                    for x in descriptions
                ),
                key=len,
                default="",
            )

            safe_link = safe_article_url(
                link,
                url,
            )

            if not title or not safe_link or not date:
                continue

            if not (
                NOW - WINDOW <= date
                <= NOW + dt.timedelta(minutes=5)
            ):
                continue

            articles.append({
                "title": title,
                "url": safe_link,
                "source": name,
                "source_feed": url,
                "host": urlparse(safe_link).hostname,
                "language": language,
                "category": category(
                    title + " " + detail
                ),
                "detail": detail,
                "detail_origin": "rss",
                "published": int(
                    date.timestamp()
                ),
                "published_iso": date.isoformat(),
            })

        return name, articles, None

    except Exception as exc:
        return (
            name,
            [],
            f"{type(exc).__name__}: "
            f"{str(exc)[:150]}",
        )


def enrich_article(article):
    article = dict(article)

    if len(article.get("detail", "")) >= MIN_RSS_DETAIL:
        return article, False

    extracted, error = extract_article(
        article["url"],
        article["source_feed"],
    )

    if (
        extracted
        and len(extracted) >= (
            len(article.get("detail", ""))
            + MIN_EXTRACTED_GAIN
        )
    ):
        article["detail"] = extracted[
            :MAX_REPORT_CHARS
        ]
        article["detail_origin"] = "article"

        article["category"] = category(
            article["title"] + " "
            + article["detail"]
        )

        return article, True

    return article, False


def build():
    old = {}

    if OUTPUT.exists():
        try:
            old = json.loads(
                OUTPUT.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            pass

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=5
    ) as pool:
        gathered = list(
            pool.map(
                get_source,
                SOURCES,
            )
        )

    unique = {}

    # Preserve Bengali-only behavior of old News Radio.
    for _, articles, _ in gathered:
        for article in articles:
            if article["language"] != "bn":
                continue

            key = article["url"].split("?")[0]

            previous = unique.get(key)

            if (
                not previous
                or len(article["detail"])
                > len(previous["detail"])
            ):
                unique[key] = article

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

    # Only fetch a bounded number of full pages per run.
    candidates = [
        (index, article)
        for index, article in enumerate(articles)
        if (
            len(article.get("detail", ""))
            < MIN_RSS_DETAIL
            and article.get("source_feed")
        )
    ][:MAX_ARTICLE_REQUESTS]

    extracted_count = 0

    if candidates:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=4
        ) as pool:
            results = list(
                pool.map(
                    enrich_article,
                    [a for _, a in candidates],
                )
            )

        for (index, _), (article, improved) in zip(
            candidates,
            results,
        ):
            articles[index] = article

            if improved:
                extracted_count += 1

    # Remove the internal source_feed field.
    for article in articles:
        article.pop(
            "source_feed",
            None,
        )

    status = [
        {
            "source": name,
            "ok": bool(items),
            "count": len(items),
            "error": error,
        }
        for name, items, error in gathered
    ]

    payload = {
        "schema_version": 1,
        "collector_version": "2.0",
        "generated_at": NOW.isoformat(),
        "lookback_hours": 36,
        "max_report_chars": MAX_REPORT_CHARS,
        "count": len(articles),
        "fresh_results": sum(
            len(item[1])
            for item in gathered
        ),
        "articles_extracted": extracted_count,
        "sources": status,
        "articles": articles,
    }

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not articles and old.get("articles"):
        print(
            "All feeds unavailable; "
            "preserving previous JSON."
        )
        return

    OUTPUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"News count: {len(articles)} | "
        f"Article extraction success: {extracted_count}"
    )

    print(
        "News JSON saved:",
        OUTPUT,
    )


if __name__ == "__main__":
    build()
