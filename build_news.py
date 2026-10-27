
#!/usr/bin/env python3
"""Xiaozhi multi-source Bengali news collector v4 - safe fixes."""

import concurrent.futures as cf
import datetime as dt
import email.utils
import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

OUT = Path(__file__).resolve().parent / "data" / "news.json"

NOW = dt.datetime.now(dt.timezone.utc)
LOOKBACK = dt.timedelta(hours=36)

MAX_STORIES = 120
MAX_TEXT = 2400

SOURCES = [
    (
        "প্রথম আলো",
        "https://www.prothomalo.com/feed/",
        "bn",
        ("prothomalo.com",)
    ),
    (
        "জাগোনিউজ২৪",
        "https://www.jagonews24.com/rss/rss.xml",
        "bn",
        ("jagonews24.com",)
    ),
    (
        "বিডি২৪লাইভ",
        "https://www.bd24live.com/feed",
        "en",
        ("bd24live.com",)
    ),
    (
        "বিবিসি বাংলা",
        "https://feeds.bbci.co.uk/bengali/rss.xml",
        "bn",
        ("bbc.com", "bbc.co.uk", "bbci.co.uk")
    ),
    (
        "বাংলাদেশ টুডে",
        "https://thebangladeshtoday.com/feed/",
        "en",
        ("thebangladeshtoday.com",)
    ),
]

HEADERS = {
    "User-Agent": "XiaozhiNewsReader/4.0 (+RSS)",
    "Accept": "text/html,application/rss+xml,application/xml,*/*",
    "Accept-Language": "bn,en;q=0.8",
}

BAD = {
    "script", "style", "nav", "footer",
    "header", "aside", "form", "svg",
    "noscript", "iframe", "button"
}


def tidy(value, limit=MAX_TEXT):
    value = html.unescape(
        re.sub(r"<[^>]*>", " ", str(value or ""))
    )
    return re.sub(
        r"\s+", " ", value
    ).strip()[:limit]


def allowed(url, domains):
    try:
        p = urlparse(url)

        if (
            p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.port not in (None, 443)
        ):
            return False

        host = p.hostname.lower()

        return any(
            host == domain
            or host.endswith("." + domain)
            for domain in domains
        )

    except (ValueError, TypeError):
        return False


def get(url, cap=3_500_000, timeout=12, domains=None):
    if domains is not None and not allowed(url, domains):
        raise ValueError("host_not_allowed")

    request = urllib.request.Request(
        url, headers=HEADERS
    )

    with urllib.request.urlopen(
        request, timeout=timeout
    ) as response:

        final = response.geturl()

        if domains is not None and not allowed(final, domains):
            raise ValueError("redirect_not_allowed")

        if urlparse(final).scheme != "https":
            raise ValueError("insecure_redirect")

        data = response.read(cap + 1)

        if len(data) > cap:
            raise ValueError("response_too_large")

        return data, response.headers, final


def field(node, *names):
    for name in names:
        item = node.find(name)

        if item is not None:
            value = "".join(item.itertext()).strip()

            if value:
                return value

    return ""


def published(value):
    if not value:
        return None

    try:
        d = email.utils.parsedate_to_datetime(value)

    except (ValueError, TypeError):
        try:
            d = dt.datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            return None

    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)

    return d.astimezone(dt.timezone.utc)


def category(text):
    patterns = [
        (
            "খেলাধুলা",
            r"ক্রিকেট|ফুটবল|খেলা|বিশ্বকাপ|sports|cricket|football"
        ),
        (
            "প্রযুক্তি",
            r"প্রযুক্তি|বিজ্ঞান|এআই|technology|artificial intelligence"
        ),
        (
            "অর্থনীতি",
            r"ব্যাংক|অর্থনীতি|ব্যবসা|economy|business|inflation"
        ),
        (
            "রাজনীতি",
            r"রাজনীতি|নির্বাচন|সংসদ|election|politics"
        ),
        (
            "আন্তর্জাতিক",
            r"আন্তর্জাতিক|বিশ্ব|যুক্তরাষ্ট্র|রাশিয়া|গাজা|international|world"
        ),
    ]

    for name, pattern in patterns:
        if re.search(pattern, text, re.I):
            return name

    return "বাংলাদেশ"


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.stack = []
        self.paragraphs = []
        self.buffer = []
        self.jsonld = []

        self.in_script = False
        self.in_paragraph = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.stack.append(tag)

        if (
            tag == "script"
            and "ld+json" in str(
                attrs.get("type", "")
            ).lower()
        ):
            self.jsonld.append("")
            self.in_script = True

        if tag == "p" and not self.in_paragraph:
            self.in_paragraph = True
            self.buffer = []

    def handle_data(self, data):
        if self.in_script and self.jsonld:
            self.jsonld[-1] += data

        if (
            self.in_paragraph
            and not any(
                tag in BAD for tag in self.stack
            )
        ):
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.in_paragraph:
            paragraph = tidy(
                " ".join(self.buffer), 1800
            )

            if len(paragraph) >= 45:
                self.paragraphs.append(paragraph)

            self.buffer = []
            self.in_paragraph = False

        if tag == "script":
            self.in_script = False

        if tag in self.stack:
            index = (
                len(self.stack) - 1
                - self.stack[::-1].index(tag)
            )

            self.stack = self.stack[:index]


def ld_text(obj):
    if isinstance(obj, list):
        for item in obj:
            yield from ld_text(item)

    elif isinstance(obj, dict):
        kinds = obj.get("@type", [])

        if isinstance(kinds, str):
            kinds = [kinds]

        if (
            any("Article" in str(x) for x in kinds)
            and isinstance(obj.get("articleBody"), str)
        ):
            yield tidy(obj["articleBody"])

        for key in ("@graph", "mainEntity"):
            if key in obj:
                yield from ld_text(obj[key])


def full_text(article, domains):
    try:
        raw, headers, _ = get(
            article["url"],
            domains=domains,
            timeout=9
        )

        if "html" not in headers.get(
            "Content-Type", ""
        ).lower():
            return "", "not_html"

        charset = (
            headers.get_content_charset()
            or "utf-8"
        )

        try:
            page = raw.decode(
                charset, errors="replace"
            )
        except LookupError:
            page = raw.decode(
                "utf-8", errors="replace"
            )

        parser = ArticleHTML()
        parser.feed(page)

        paragraphs = []
        seen = set()

        for paragraph in parser.paragraphs:
            key = re.sub(r"\s+", "", paragraph)

            if key not in seen:
                seen.add(key)
                paragraphs.append(paragraph)

            if len(paragraphs) >= 18:
                break

        candidates = [
            tidy(" ".join(paragraphs))
        ]

        for script in parser.jsonld:
            try:
                candidates.extend(
                    ld_text(json.loads(script))
                )
            except (ValueError, TypeError):
                pass

        result = max(
            candidates, key=len, default=""
        )

        return (
            result,
            "ok" if len(result) >= 120
            else "no_article_text"
        )

    except urllib.error.HTTPError as error:
        return "", f"http_{error.code}"

    except urllib.error.URLError as error:
        return "", (
            "network_"
            + type(error.reason).__name__
        )

    except ValueError as error:
        return "", str(error)

    except Exception as error:
        return "", (
            "parse_"
            + type(error).__name__
        )


def rss(spec):
    name, url, language, domains = spec

    try:
        content, _, _ = get(
            url,
            cap=2_000_000,
            timeout=12
        )

        root = ET.fromstring(content)

        nodes = (
            root.findall(".//item")
            or root.findall(
                "{http://www.w3.org/2005/Atom}entry"
            )
        )

        results = []

        for node in nodes[:120]:
            title = tidy(
                field(
                    node,
                    "title",
                    "{http://www.w3.org/2005/Atom}title"
                ),
                300
            )

            link = field(node, "link")

            if not link:
                x = node.find(
                    "{http://www.w3.org/2005/Atom}link"
                )

                link = (
                    x.get("href", "")
                    if x is not None else ""
                )

            stamp = published(
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
                tidy(field(node, key))
                for key in (
                    "description",
                    "{http://purl.org/rss/1.0/modules/content/}encoded",
                    "{http://www.w3.org/2005/Atom}summary",
                    "{http://www.w3.org/2005/Atom}content"
                )
            ]

            detail = max(
                descriptions, key=len
            )

            if (
                not title
                or not allowed(link, domains)
                or stamp is None
                or not (
                    NOW - LOOKBACK
                    <= stamp
                    <= NOW + dt.timedelta(minutes=5)
                )
            ):
                continue

            results.append({
                "title": title,
                "url": link,
                "source": name,
                "language": language,
                "category": category(title + " " + detail),
                "detail": detail,
                "detail_origin": "rss",
                "published": int(stamp.timestamp()),
                "published_iso": stamp.isoformat(),
            })

        return spec, results, None, len(nodes)

    except Exception as error:
        return (
            spec,
            [],
            f"{type(error).__name__}: {str(error)[:100]}",
            0
        )


def build():
    previous = {}

    if OUT.exists():
        try:
            previous = json.loads(
                OUT.read_text(encoding="utf-8")
            )
        except (ValueError, OSError):
            pass

    with cf.ThreadPoolExecutor(
        max_workers=5
    ) as pool:
        results = list(
            pool.map(rss, SOURCES)
        )

    reports = []
    groups = {}

    extraction_stats = Counter()
    translation_stats = Counter()

    for spec, articles, error, count in results:
        name = spec[0]
        groups[name] = articles

        reports.append({
            "source": name,
            "ok": error is None,
            "rss_items": count,
            "recent": len(articles),
            "error": error
        })

    # Enrich genuinely short articles.
    # Preserve RSS detail for sources blocking article HTML.
    jobs = []

    for spec in SOURCES:
        articles = groups[spec[0]]

        for article in articles:
            if spec[0] == "জাগোনিউজ২৪":
                extraction_stats["rss_only_jagonews"] += 1
                continue

            if len(article["detail"]) < 1800:
                jobs.append(
                    (article, spec[3])
                )

    jobs = sorted(
        jobs,
        key=lambda x: len(x[0]["detail"])
    )[:90]

    def enrich(job):
        article, domains = job

        body, status = full_text(
            article, domains
        )

        if len(body) >= len(article["detail"]) + 100:
            article["detail"] = body
            article["detail_origin"] = "article"

            article["category"] = category(
                article["title"] + " " + body
            )

            return "improved"

        return (
            status
            if status != "ok"
            else "not_longer"
        )

    with cf.ThreadPoolExecutor(
        max_workers=6
    ) as pool:
        for status in pool.map(enrich, jobs):
            extraction_stats[status] += 1

    # English sources are handled by the existing
    # free offline Argos translator in the next
    # GitHub Actions workflow step.
    # Never publish untranslated English articles here.
    for spec in SOURCES:
        name, _, language, _ = spec

        if language == "en":
            groups[name] = []

    translation_stats["delegated_to_offline_argos"] = 1

    # Balanced selection, maximum 28 per source first.
    selected = []
    used = set()

    for name, _, _, _ in SOURCES:
        items = sorted(
            groups[name],
            key=lambda item: item["published"],
            reverse=True
        )

        count = 0

        for article in items:
            key = article["url"].split("?")[0]

            if key in used:
                continue

            selected.append(article)
            used.add(key)
            count += 1

            if count >= 28:
                break

    remaining = sorted(
        (
            article
            for group in groups.values()
            for article in group
            if article["url"].split("?")[0] not in used
        ),
        key=lambda item: item["published"],
        reverse=True
    )

    selected.extend(
        remaining[
            :max(0, MAX_STORIES - len(selected))
        ]
    )

    selected = sorted(
        selected,
        key=lambda item: item["published"],
        reverse=True
    )[:MAX_STORIES]

    # Preserve valid fresh Bengali articles if feeds fail.
    if not selected and previous.get("articles"):
        selected = [
            article
            for article in previous["articles"]
            if (
                article.get("language") == "bn"
                and article.get("published", 0)
                >= int((NOW - LOOKBACK).timestamp())
            )
        ]

    if not selected:
        raise RuntimeError(
            "No valid fresh news; previous JSON untouched"
        )

    payload = {
        "schema_version": 1,
        "collector_version": "4.0",
        "generated_at": NOW.isoformat(),
        "lookback_hours": 36,
        "max_report_chars": MAX_TEXT,
        "count": len(selected),
        "articles_extracted": extraction_stats["improved"],
        "extraction_diagnostics": dict(extraction_stats),
        "translation_diagnostics": dict(translation_stats),
        "sources": reports,
        "articles": selected
    }

    OUT.parent.mkdir(
        parents=True, exist_ok=True
    )

    OUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print("NEWS COLLECTOR v4.0")
    print("Sources:", reports)
    print("Extract:", dict(extraction_stats))
    print("Translation:", dict(translation_stats))

    print(
        "Final:",
        dict(
            Counter(
                article["source"]
                for article in selected
            )
        )
    )


if __name__ == "__main__":
    build()
