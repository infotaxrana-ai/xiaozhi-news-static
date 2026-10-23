
#!/usr/bin/env python3
"""Xiaozhi free offline English-to-Bengali news translation."""

import collections
import datetime as dt
import json
import re
import tempfile
import urllib.request
from pathlib import Path

import build_news as collector

ROOT = Path(__file__).resolve().parent
NEWS = ROOT / "data" / "news.json"

MODEL_URL = (
    "https://data.argosopentech.com/argospm/v1/"
    "translate-en_bn-1_9.argosmodel"
)

MAX_ENGLISH = 10
MAX_PER_SOURCE = 6
MAX_DETAIL = 1400
MAX_TOTAL = 120


def bengali_enough(value, minimum=3):
    return sum(
        "\u0980" <= ch <= "\u09ff"
        for ch in value
    ) >= minimum


def download_and_install_model():
    import argostranslate.package
    import argostranslate.translate

    languages = argostranslate.translate.get_installed_languages()

    english = next(
        (x for x in languages if x.code == "en"), None
    )
    bengali = next(
        (x for x in languages if x.code == "bn"), None
    )

    if english and bengali:
        try:
            return english.get_translation(bengali)
        except Exception:
            pass

    print("OFFLINE MODEL: downloading en -> bn", flush=True)

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "en_bn.argosmodel"

        request = urllib.request.Request(
            MODEL_URL,
            headers={"User-Agent": "XiaozhiNews/1.0"},
        )

        with urllib.request.urlopen(
            request, timeout=120
        ) as response, path.open("wb") as output:
            total = 0

            while True:
                chunk = response.read(1024 * 1024)

                if not chunk:
                    break

                total += len(chunk)

                if total > 85_000_000:
                    raise RuntimeError("Translation model too large")

                output.write(chunk)

        if total < 1_000_000:
            raise RuntimeError("Translation model incomplete")

        argostranslate.package.install_from_path(
            str(path)
        )

    languages = argostranslate.translate.get_installed_languages()

    english = next(
        (x for x in languages if x.code == "en"), None
    )
    bengali = next(
        (x for x in languages if x.code == "bn"), None
    )

    if not english or not bengali:
        raise RuntimeError("English/Bengali language not installed")

    return english.get_translation(bengali)


def split_text(value, limit=350):
    """Split paragraphs into small pieces for stable CPU inference."""
    words = value.split()
    chunks = []
    current = ""

    for word in words:
        if len(current) + len(word) + 1 > limit and current:
            chunks.append(current)
            current = ""

        if len(word) > limit:
            if current:
                chunks.append(current)
                current = ""

            for i in range(0, len(word), limit):
                chunks.append(word[i:i + limit])
        else:
            current = (current + " " + word).strip()

    if current:
        chunks.append(current)

    return chunks


def convert(translator, value, max_chars):
    value = re.sub(
        r"\s+", " ", str(value or "")
    ).strip()[:max_chars]

    output = []

    for chunk in split_text(value):
        translated = translator.translate(chunk)

        if not isinstance(translated, str):
            raise ValueError("Invalid translated response")

        output.append(translated.strip())

    return re.sub(r"\s+", " ", " ".join(output)).strip()


def main():
    original_bytes = NEWS.read_bytes()
    original = json.loads(original_bytes)

    current = original.get("articles", [])

    if not current:
        raise RuntimeError("Existing Bengali news missing")

    english_sources = [
        source
        for source in collector.SOURCES
        if source[2] == "en"
    ]

    candidates = []

    for source in english_sources:
        name = source[0]

        try:
            _, articles, error, total = collector.rss(source)

            print(
                f"ENGLISH SOURCE: {name}, "
                f"RSS={total}, valid={len(articles)}, "
                f"error={error}",
                flush=True,
            )

            articles.sort(
                key=lambda x: x["published"],
                reverse=True,
            )

            candidates.extend(articles[:MAX_PER_SOURCE])

        except Exception as error:
            print(
                f"ENGLISH SOURCE ERROR {name}: {error}",
                flush=True,
            )

    candidates.sort(
        key=lambda x: x["published"],
        reverse=True,
    )

    selected = []
    seen = set()

    for article in candidates:
        key = article["url"].split("?")[0]

        if key not in seen:
            seen.add(key)
            selected.append(article)

        if len(selected) >= MAX_ENGLISH:
            break

    if not selected:
        print("OFFLINE TRANSLATION: no English news")
        print("Existing Bengali news preserved")
        return

    # Model setup must succeed before altering JSON.
    try:
        translator = download_and_install_model()
    except Exception as error:
        print(
            "OFFLINE MODEL FAILED:",
            repr(error),
            flush=True,
        )
        print("Existing Bengali news preserved")
        return

    translated = []
    failures = collections.Counter()

    for article in selected:
        try:
            bn_title = convert(
                translator,
                article["title"],
                300,
            )

            bn_detail = convert(
                translator,
                article["detail"],
                MAX_DETAIL,
            )

            if (
                not bengali_enough(bn_title, 3)
                or not bengali_enough(bn_detail, 15)
                or len(bn_detail) < 70
            ):
                failures["poor_or_incomplete_translation"] += 1
                continue

            result = dict(article)

            result["title"] = bn_title
            result["detail"] = bn_detail
            result["language"] = "bn"
            result["detail_origin"] = "argos_offline_en_bn"
            result["category"] = collector.category(
                bn_title + " " + bn_detail
            )

            translated.append(result)

            print(
                f"TRANSLATED: {result['source']}, "
                f"chars={len(bn_detail)}",
                flush=True,
            )

        except Exception as error:
            failures[type(error).__name__] += 1

    print(
        "OFFLINE TRANSLATION:",
        f"attempted={len(selected)},",
        f"success={len(translated)},",
        f"failed={dict(failures)}",
        flush=True,
    )

    if not translated:
        print("Existing Bengali news preserved")
        return

    # Reserve space for successful translations.
    english_urls = {
        a["url"].split("?")[0]
        for a in translated
    }

    bengali_articles = [
        a for a in current
        if (
            a.get("language", "bn") == "bn"
            and a.get("url", "").split("?")[0]
            not in english_urls
        )
    ]

    bengali_articles.sort(
        key=lambda a: a.get("published", 0),
        reverse=True,
    )

    final = bengali_articles[
        :max(0, MAX_TOTAL - len(translated))
    ] + translated

    final.sort(
        key=lambda a: a.get("published", 0),
        reverse=True,
    )

    result = dict(original)
    result["articles"] = final
    result["count"] = len(final)

    result["offline_translation"] = {
        "model": "argos-en-bn-1.9",
        "attempted": len(selected),
        "translated": len(translated),
        "failures": dict(failures),
        "generated_at": dt.datetime.now(
            dt.timezone.utc
        ).isoformat(),
    }

    # Preserve collector-generated timestamp and schema
    # for prepare_news_radio.py compatibility.
    new_content = (
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    ).encode("utf-8")

    NEWS.write_bytes(new_content)

    print(
        "FINAL SOURCE DISTRIBUTION:",
        dict(collections.Counter(
            a["source"] for a in final
        )),
        flush=True,
    )

    print("OFFLINE TRANSLATION PUBLISHED", flush=True)


if __name__ == "__main__":
    main()
