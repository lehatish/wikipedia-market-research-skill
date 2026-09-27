#!/usr/bin/env python3
"""
resolve_article.py — знайти точні назви статті у декількох мовних Вікіпедіях
за темою, вказаною будь-якою мовою.

Навіщо це окремий крок: назва статті майже завжди відрізняється між мовами
("Intermittent fasting" en -> "Post przerywany" pl -> "Přerušovaný půst" cs),
а Pageviews API вимагає точної назви сторінки. Пряме вгадування назви
призводить до 404 або (гірше) до тихого збігу з іншою статтею.

Приклад використання:
    python resolve_article.py "intermittent fasting" --langs pl,cs,en

Вивід — JSON у stdout:
{
  "pl": {"title": "Post przerywany", "resolved_via": "sitelink", "qid": "Q..."},
  "cs": {"title": "Přerušovaný půst", "resolved_via": "sitelink", "qid": "Q..."},
  "en": {"title": "Intermittent fasting", "resolved_via": "search", "qid": "Q..."},
  "_not_found": []
}

Якщо для якоїсь мови статтю не знайдено — вона потрапляє у "_not_found".
Агент має явно повідомити про це користувачу, а не мовчки продовжувати
без цієї мови.
"""

import argparse
import json
import sys

from wm_api import resolve_article, WikimediaAPIError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("term", help="Тема запитом будь-якою мовою, напр. 'intermittent fasting'")
    parser.add_argument(
        "--langs",
        required=True,
        help="Коми-розділений список мовних кодів Вікіпедії, напр. pl,cs,uk",
    )
    parser.add_argument(
        "--hint-lang",
        default="en",
        help="Мова, якою робиться початковий пошук для визначення Wikidata QID (за замовчуванням en)",
    )
    args = parser.parse_args()

    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    if not langs:
        print(json.dumps({"error": "Не вказано жодної мови в --langs"}), file=sys.stderr)
        return 1

    try:
        resolved = resolve_article(args.term, langs, hint_lang=args.hint_lang)
    except WikimediaAPIError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2

    out = {}
    for lang, ref in resolved.items():
        out[lang] = {"title": ref.title, "resolved_via": ref.resolved_via, "qid": ref.qid}

    not_found = [l for l in langs if l not in resolved]
    out["_not_found"] = not_found

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not not_found else 3  # ненульовий код, якщо щось не знайдено — але результат все одно виведено


if __name__ == "__main__":
    sys.exit(main())
