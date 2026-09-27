#!/usr/bin/env python3
"""
wiki_trends.py — виконує повний пайплайн одним викликом:
  1) визначити точні назви статті потрібними мовами (Wikidata sitelinks)
  2) завантажити помісячні перегляди за вказаний період (agent=user)
  3) порахувати тренд, надійність, викиди для кожної мови
  4) зібрати односторінковий PDF-звіт

Це найшвидший шлях для типового запиту типу "порівняй інтерес до X мовами
A і B за останні N років". Для нетипових сценаріїв (свій список назв
статей, кастомний формат звіту, повторне використання вже завантажених
даних) використовуйте окремі скрипти — resolve_article.py, fetch_pageviews.py,
trend_analysis.py, build_report.py — вони приймають/віддають ті самі
проміжні JSON-файли, тож пайплайн можна зібрати вручну з будь-якого кроку.

Приклад:
    python wiki_trends.py \
        --topic "intermittent fasting" \
        --langs pl,cs \
        --years-back 2 \
        --title "Інтервальне голодування: PL vs CS" \
        --workdir /tmp/wt_run1 \
        --out report.pdf
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True, help="Тема будь-якою мовою, напр. 'astronomy'")
    parser.add_argument("--langs", required=True, help="Коми-розділений список мовних кодів, напр. uk,pl,cs")
    parser.add_argument("--hint-lang", default="en")
    parser.add_argument("--years-back", type=float, default=2.0, help="Скільки років назад від сьогодні брати дані")
    parser.add_argument("--start", help="YYYY-MM-DD, перекриває --years-back")
    parser.add_argument("--end", help="YYYY-MM-DD, за замовчуванням сьогодні")
    parser.add_argument("--granularity", choices=["daily", "monthly"], default="monthly")
    parser.add_argument("--title", required=True, help="Заголовок PDF-звіту")
    parser.add_argument("--workdir", default=".", help="Директорія для проміжних JSON-файлів")
    parser.add_argument("--out", required=True, help="Шлях до фінального PDF")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    end_date = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    if args.start:
        start_date = dt.date.fromisoformat(args.start)
    else:
        start_date = end_date - dt.timedelta(days=int(args.years_back * 365.25))

    langs = [x.strip() for x in args.langs.split(",") if x.strip()]

    # 1) Резолвінг назв статей
    resolve_out = workdir / "resolved.json"
    p = run(
        [
            sys.executable,
            str(SCRIPT_DIR / "resolve_article.py"),
            args.topic,
            "--langs",
            ",".join(langs),
            "--hint-lang",
            args.hint_lang,
        ]
    )
    if p.returncode not in (0, 3):  # 3 = деякі мови не знайдено, але продовжуємо з рештою
        print("Помилка на етапі resolve_article.py:", p.stderr, file=sys.stderr)
        return 1
    resolved = json.loads(p.stdout)
    resolve_out.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")

    not_found = resolved.get("_not_found", [])
    if not_found:
        print(
            f"УВАГА: статтю не знайдено для мов: {not_found}. "
            "Звіт буде побудовано лише для решти мов.",
            file=sys.stderr,
        )

    series_args = [
        f"{lang}:{info['title']}"
        for lang, info in resolved.items()
        if lang != "_not_found"
    ]
    if not series_args:
        print("Жодної мови не вдалося розпізнати — зупиняюсь.", file=sys.stderr)
        return 2

    # 2) Завантаження переглядів
    data_out = workdir / "data.json"
    p = run(
        [
            sys.executable,
            str(SCRIPT_DIR / "fetch_pageviews.py"),
            "--series",
            *series_args,
            "--start",
            start_date.isoformat(),
            "--end",
            end_date.isoformat(),
            "--granularity",
            args.granularity,
            "--out",
            str(data_out),
        ]
    )
    if p.returncode not in (0, 2):
        print("Помилка на етапі fetch_pageviews.py:", p.stderr, file=sys.stderr)
        return 3

    # 3) Аналіз тренду
    analysis_out = workdir / "analysis.json"
    p = run(
        [
            sys.executable,
            str(SCRIPT_DIR / "trend_analysis.py"),
            "--in",
            str(data_out),
            "--out",
            str(analysis_out),
        ]
    )
    if p.returncode != 0:
        print("Помилка на етапі trend_analysis.py:", p.stderr, file=sys.stderr)
        return 4

    # 4) Звіт
    subtitle = f"{start_date.isoformat()} — {end_date.isoformat()}, теми: {args.topic}"
    p = run(
        [
            sys.executable,
            str(SCRIPT_DIR / "build_report.py"),
            "--analysis",
            str(analysis_out),
            "--title",
            args.title,
            "--subtitle",
            subtitle,
            "--out",
            args.out,
        ]
    )
    if p.returncode != 0:
        print("Помилка на етапі build_report.py:", p.stderr, file=sys.stderr)
        return 5

    print(f"Готово. Звіт: {args.out}", file=sys.stderr)
    print(f"Проміжні файли для повторного/уточненого аналізу: {workdir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
