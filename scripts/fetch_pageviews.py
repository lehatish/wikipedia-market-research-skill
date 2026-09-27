#!/usr/bin/env python3
"""
fetch_pageviews.py — завантажити часовий ряд переглядів статті(й) з
Wikimedia Pageviews API та зберегти у нормалізованому JSON.

Приклад:
    python fetch_pageviews.py \
        --series pl:"Post przerywany" cs:"Přerušovaný půst" \
        --start 2023-01-01 --end 2025-12-31 --granularity monthly \
        --out data.json

Формат вихідного файлу:
{
  "generated_at": "...",
  "granularity": "monthly",
  "start": "2023-01-01",
  "end": "2025-12-31",
  "series": {
    "pl": {
      "title": "Post przerywany",
      "project": "pl.wikipedia",
      "points": [{"date": "2023-01", "views": 12345}, ...],
      "warnings": []
    },
    ...
  }
}

Дати нормалізуються у "YYYY-MM" для monthly або "YYYY-MM-DD" для daily,
щоб решта пайплайну (аналіз, звіт) не залежала від формату Wikimedia
("YYYYMMDDHH").
"""

import argparse
import datetime as dt
import json
import sys

from wm_api import pageviews_per_article, WikimediaAPIError, PAGEVIEWS_MIN_DATE


def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def to_wm_timestamp(d: dt.date) -> str:
    return d.strftime("%Y%m%d") + "00"


def normalize_timestamp(ts: str, granularity: str) -> str:
    # ts має формат YYYYMMDDHH
    year, month, day = ts[0:4], ts[4:6], ts[6:8]
    if granularity == "monthly":
        return f"{year}-{month}"
    return f"{year}-{month}-{day}"


def parse_series_arg(entry: str) -> tuple[str, str]:
    """'pl:Post przerywany' -> ('pl', 'Post przerywany')"""
    if ":" not in entry:
        raise ValueError(
            f"Очікував формат lang:Назва статті, отримав: {entry!r}"
        )
    lang, title = entry.split(":", 1)
    return lang.strip(), title.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--series",
        nargs="+",
        required=True,
        help="Один або кілька записів формату lang:Точна_назва_статті "
        "(назви бажано брати з виводу resolve_article.py)",
    )
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--granularity", choices=["daily", "monthly"], default="monthly"
    )
    parser.add_argument(
        "--access",
        choices=["all-access", "desktop", "mobile-app", "mobile-web"],
        default="all-access",
    )
    parser.add_argument(
        "--agent",
        choices=["all-agents", "user", "spider"],
        default="user",
        help="'user' відфільтровує ботів/краулерів (рекомендовано для оцінки людського інтересу)",
    )
    parser.add_argument("--out", required=True, help="Шлях до вихідного JSON-файлу")
    args = parser.parse_args()

    start_date = parse_date(args.start)
    end_date = parse_date(args.end)
    if start_date > end_date:
        print("Помилка: --start пізніше за --end", file=sys.stderr)
        return 1

    start_ts = to_wm_timestamp(start_date)
    end_ts = to_wm_timestamp(end_date)

    result = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "granularity": args.granularity,
        "start": args.start,
        "end": args.end,
        "series": {},
    }

    had_error = False
    for entry in args.series:
        lang, title = parse_series_arg(entry)
        project = f"{lang}.wikipedia"
        warnings = []
        if start_ts < PAGEVIEWS_MIN_DATE:
            warnings.append(
                "Запитаний початковий період раніше за липень 2015 — "
                "Pageviews API не має даних до цього, тому ряд обрізано."
            )
        try:
            items = pageviews_per_article(
                project=project,
                article_title=title,
                start=start_ts,
                end=end_ts,
                granularity=args.granularity,
                access=args.access,
                agent=args.agent,
            )
        except WikimediaAPIError as e:
            had_error = True
            result["series"][lang] = {
                "title": title,
                "project": project,
                "points": [],
                "warnings": warnings + [f"Помилка запиту: {e}"],
            }
            continue

        points = [
            {
                "date": normalize_timestamp(it["timestamp"], args.granularity),
                "views": it["views"],
            }
            for it in items
        ]
        if not points:
            warnings.append(
                "Дані відсутні за весь період — можливо, невірна назва статті "
                "або стаття не існувала в ці роки. Перевірте через resolve_article.py."
            )
        result["series"][lang] = {
            "title": title,
            "project": project,
            "points": points,
            "warnings": warnings,
        }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Збережено: {args.out}", file=sys.stderr)
    return 2 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
