"""
wm_api.py — тонкий клієнт до публічних Wikimedia/Wikipedia API.

Використовується іншими скриптами навички (fetch_pageviews.py, resolve_article.py,
wiki_trends.py). Не містить бізнес-логіки аналізу — лише мережеві виклики
та їх нормалізацію, щоб решта коду не дублювала retry/User-Agent/парсинг.

Публічні API, які тут використовуються (усі безкоштовні, без ключа):
- Pageviews REST API:
  https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/...
- MediaWiki Action API кожної мовної Вікіпедії (search, pageprops):
  https://<lang>.wikipedia.org/w/api.php
- Wikidata Action API (sitelinks — назви статті іншими мовами):
  https://www.wikidata.org/w/api.php

Wikimedia вимагає власного User-Agent з контактною інформацією — без цього
запити можуть отримувати 403/429. Значення нижче варто замінити на реальний
контакт проєкту, що використовує цю навичку.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

USER_AGENT = (
    "WikipediaTrendsSkill/1.0 "
    "(https://github.com/your-org/your-repo; contact: you@example.com) "
    "python-urllib"
)

# Базові URL можна підмінити через змінні середовища — це використовується
# у test/test_end_to_end.py, щоб прогнати весь пайплайн проти локального
# фейкового сервера замість реального Wikimedia API (напр. в офлайн-середовищі
# розробки без мережевого доступу). У продакшн-використанні ці змінні не
# потрібні — застосовуються значення за замовчуванням нижче.
PAGEVIEWS_BASE = os.environ.get(
    "WT_PAGEVIEWS_BASE", "https://wikimedia.org/api/rest_v1/metrics/pageviews"
)
MEDIAWIKI_BASE_TEMPLATE = os.environ.get(
    "WT_MEDIAWIKI_BASE_TEMPLATE", "https://{lang}.wikipedia.org/w/api.php"
)
WIKIDATA_BASE = os.environ.get(
    "WT_WIKIDATA_BASE", "https://www.wikidata.org/w/api.php"
)
# Дані per-article REST API доступні лише з липня 2015 — це важливе
# обмеження, яке треба враховувати в оцінці надійності трендів.
PAGEVIEWS_MIN_DATE = "2015070100"

MAX_RETRIES = 4
BACKOFF_SECONDS = 1.5


class WikimediaAPIError(RuntimeError):
    """Піднімається при мережевих помилках або неочікуваній відповіді API."""

    def __init__(self, message: str, status: Optional[int] = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


@dataclass
class ArticleRef:
    """Однозначне посилання на статтю в конкретній мовній Вікіпедії."""

    lang: str
    title: str  # канонічна назва (з пробілами, не url-encoded)
    qid: Optional[str] = None  # Wikidata Q-ідентифікатор, якщо відомий
    resolved_via: str = field(default="unknown")  # "search" | "sitelink" | "direct"


def _http_get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}" if params else url
    req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = e
            # 404 для pageviews часто означає "статті не існувало в цей період" —
            # це не транзієнтна помилка, ретраїти немає сенсу.
            if e.code == 404:
                raise WikimediaAPIError(
                    f"404 Not Found: {full_url}", status=404, url=full_url
                ) from e
            if e.code == 429 or e.code >= 500:
                time.sleep(BACKOFF_SECONDS * attempt)
                continue
            raise WikimediaAPIError(
                f"HTTP {e.code} під час запиту {full_url}: {e.reason}",
                status=e.code,
                url=full_url,
            ) from e
        except urllib.error.URLError as e:
            last_error = e
            time.sleep(BACKOFF_SECONDS * attempt)
            continue

    raise WikimediaAPIError(
        f"Не вдалося отримати відповідь після {MAX_RETRIES} спроб: {full_url} ({last_error})",
        url=full_url,
    )


def mediawiki_api(lang: str, params: dict[str, Any]) -> dict[str, Any]:
    """Запит до action API конкретної мовної Вікіпедії."""
    base = MEDIAWIKI_BASE_TEMPLATE.format(lang=lang)
    merged = {"format": "json", **params}
    return _http_get_json(base, merged)


def wikidata_api(params: dict[str, Any]) -> dict[str, Any]:
    merged = {"format": "json", **params}
    return _http_get_json(WIKIDATA_BASE, merged)


def pageviews_per_article(
    project: str,
    article_title: str,
    start: str,
    end: str,
    granularity: str = "monthly",
    access: str = "all-access",
    agent: str = "user",
) -> list[dict[str, Any]]:
    """
    Повертає список {"timestamp": "YYYYMMDDHH", "views": int} для однієї статті.

    project: наприклад "en.wikipedia", "pl.wikipedia", "uk.wikipedia"
    agent="user" відфільтровує ботів/краулерів — важливо для оцінки
    реального людського інтересу, а не активності скреперів.
    """
    if start < PAGEVIEWS_MIN_DATE:
        start = PAGEVIEWS_MIN_DATE
    encoded_title = urllib.parse.quote(article_title.replace(" ", "_"), safe="")
    url = (
        f"{PAGEVIEWS_BASE}/per-article/{project}/{access}/{agent}/"
        f"{encoded_title}/{granularity}/{start}/{end}"
    )
    data = _http_get_json(url, {})
    return data.get("items", [])


def find_qid_by_search(term: str, lang: str = "en") -> Optional[ArticleRef]:
    """
    Знаходить найбільш релевантну статтю мовою `lang` за пошуковим запитом
    та повертає її Wikidata QID (якщо є), щоб потім через sitelinks знайти
    відповідники іншими мовами.
    """
    search = mediawiki_api(
        lang,
        {
            "action": "query",
            "list": "search",
            "srsearch": term,
            "srlimit": 1,
        },
    )
    results = search.get("query", {}).get("search", [])
    if not results:
        return None
    title = results[0]["title"]

    props = mediawiki_api(
        lang,
        {
            "action": "query",
            "prop": "pageprops",
            "titles": title,
            "ppprop": "wikibase_item",
        },
    )
    pages = props.get("query", {}).get("pages", {})
    qid = None
    for _, page in pages.items():
        qid = page.get("pageprops", {}).get("wikibase_item")
        break

    return ArticleRef(lang=lang, title=title, qid=qid, resolved_via="search")


def get_sitelinks(qid: str) -> dict[str, str]:
    """
    Повертає {lang_code: article_title} для всіх мовних версій статті
    з даним Wikidata QID, напр. {"en": "Intermittent fasting", "pl": "Post przerywany"}.
    lang_code тут у форматі MediaWiki sitelinks ("plwiki" -> "pl").
    """
    data = wikidata_api(
        {
            "action": "wbgetentities",
            "ids": qid,
            "props": "sitelinks",
        }
    )
    entity = data.get("entities", {}).get(qid, {})
    sitelinks = entity.get("sitelinks", {})
    result: dict[str, str] = {}
    for site_key, info in sitelinks.items():
        if site_key.endswith("wiki") and not site_key.endswith(
            ("wikibooks", "wikisource", "wikinews", "wikiquote", "wikivoyage")
        ):
            lang = site_key[: -len("wiki")]
            # Спецвипадок: "commonswiki" тощо вже відфільтровано вище умовою,
            # але про всяк випадок перевіримо довжину коду мови.
            if 2 <= len(lang) <= 3 or "_" in lang:
                result[lang] = info["title"]
    return result


def resolve_article(term: str, langs: list[str], hint_lang: str = "en") -> dict[str, ArticleRef]:
    """
    Головна функція для крос-мовного пошуку статті.

    1. Шукає статтю мовою hint_lang (за замовчуванням англійська — там
       найповніший граф sitelinks) і дістає її Wikidata QID.
    2. Через QID отримує назви статті всіма доступними мовами одним запитом.
    3. Для мов, яких немає серед sitelinks, робить прямий пошук у тій мовній
       Вікіпедії напряму (fallback) — стаття може існувати без зв'язку
       у Wikidata або мати розбіжність даних.

    Повертає dict {lang: ArticleRef}. Мови, для яких статтю не знайдено,
    відсутні в результаті — виклик має явно повідомити про це користувачу,
    а не мовчки пропускати.
    """
    result: dict[str, ArticleRef] = {}

    hint_ref = find_qid_by_search(term, lang=hint_lang)
    sitelinks: dict[str, str] = {}
    if hint_ref and hint_ref.qid:
        sitelinks = get_sitelinks(hint_ref.qid)

    if hint_lang in langs and hint_ref:
        result[hint_lang] = hint_ref

    for lang in langs:
        if lang in result:
            continue
        if lang in sitelinks:
            result[lang] = ArticleRef(
                lang=lang,
                title=sitelinks[lang],
                qid=hint_ref.qid if hint_ref else None,
                resolved_via="sitelink",
            )
        else:
            direct = find_qid_by_search(term, lang=lang)
            if direct:
                direct.resolved_via = "direct"
                result[lang] = direct

    return result
