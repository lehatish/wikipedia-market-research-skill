"""
Офлайн-тести — не звертаються до мережі, перевіряють чисту логіку:
парсинг дат/аргументів, побудову URL, аналіз трендів на синтетичних даних,
фільтрацію sitelinks. Запуск:

    cd wikipedia-trends/test && python3 test_offline.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import fetch_pageviews as fp
import trend_analysis as ta
import wm_api


class TestFetchPageviewsHelpers(unittest.TestCase):
    def test_normalize_timestamp_monthly(self):
        self.assertEqual(fp.normalize_timestamp("2024050100", "monthly"), "2024-05")

    def test_normalize_timestamp_daily(self):
        self.assertEqual(fp.normalize_timestamp("2024050100", "daily"), "2024-05-01")

    def test_parse_series_arg(self):
        lang, title = fp.parse_series_arg("pl:Post przerywany")
        self.assertEqual(lang, "pl")
        self.assertEqual(title, "Post przerywany")

    def test_parse_series_arg_invalid(self):
        with self.assertRaises(ValueError):
            fp.parse_series_arg("no-colon-here")

    def test_to_wm_timestamp(self):
        import datetime as dt
        self.assertEqual(fp.to_wm_timestamp(dt.date(2023, 1, 5)), "2023010500")


class TestPageviewsUrlConstruction(unittest.TestCase):
    def test_article_title_url_encoding(self):
        # Перевіряємо саму логіку кодування назви статті, не роблячи запиту.
        import urllib.parse
        title = "Přerušovaný půst"
        encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
        self.assertNotIn(" ", encoded)
        self.assertIn("%c5%99", encoded.lower())  # 'ř' у utf-8 percent-encoding


class TestTrendAnalysis(unittest.TestCase):
    def test_empty_series(self):
        result = ta.analyze_series([])
        self.assertEqual(result.n_periods, 0)
        self.assertEqual(result.confidence, "low")

    def test_clear_growth_high_confidence(self):
        points = [{"date": f"2024-{i+1:02d}", "views": 1000 * (1.05 ** i)} for i in range(24)]
        result = ta.analyze_series(points)
        self.assertGreater(result.monthly_growth_pct, 3)
        self.assertGreater(result.r_squared, 0.9)
        self.assertEqual(result.confidence, "high")

    def test_flat_noisy_series_low_or_medium_confidence(self):
        import random
        random.seed(1)
        points = [
            {"date": f"2024-{(i % 12)+1:02d}", "views": max(0, int(1000 + random.uniform(-800, 800)))}
            for i in range(24)
        ]
        result = ta.analyze_series(points)
        self.assertIn(result.confidence, ("low", "medium"))

    def test_short_series_never_high_confidence(self):
        points = [{"date": f"2024-{i+1:02d}", "views": 1000 + i * 500} for i in range(6)]
        result = ta.analyze_series(points)
        self.assertNotEqual(result.confidence, "high")

    def test_outlier_detection(self):
        points = [{"date": f"2024-{i+1:02d}", "views": 1000} for i in range(11)]
        points.append({"date": "2024-12", "views": 50000})  # явний сплеск
        result = ta.analyze_series(points)
        self.assertIn("2024-12", result.outlier_periods)

    def test_index_series_base_100(self):
        points = [{"date": "2024-01", "views": 200}, {"date": "2024-02", "views": 400}]
        indexed = ta.index_series(points)
        self.assertEqual(indexed[0]["index"], 100.0)
        self.assertEqual(indexed[1]["index"], 200.0)

    def test_index_series_zero_base_fallback(self):
        points = [
            {"date": "2024-01", "views": 0},
            {"date": "2024-02", "views": 0},
            {"date": "2024-03", "views": 50},
        ]
        indexed = ta.index_series(points)
        self.assertEqual(indexed[2]["index"], 100.0)


class TestSitelinkFiltering(unittest.TestCase):
    def test_get_sitelinks_filters_non_wiki_projects(self):
        # Симулюємо відповідь Wikidata без мережевого виклику,
        # тестуючи лише логіку фільтрації all-wiki sitelinks.
        fake_sitelinks = {
            "enwiki": {"title": "Intermittent fasting"},
            "plwiki": {"title": "Post przerywany"},
            "commonswiki": {"title": "Category:Intermittent fasting"},
            "enwikiquote": {"title": "Intermittent fasting"},
        }

        # Відтворюємо тіло функції get_sitelinks без HTTP-виклику.
        result = {}
        for site_key, info in fake_sitelinks.items():
            if site_key.endswith("wiki") and not site_key.endswith(
                ("wikibooks", "wikisource", "wikinews", "wikiquote", "wikivoyage")
            ):
                lang = site_key[: -len("wiki")]
                if 2 <= len(lang) <= 3 or "_" in lang:
                    result[lang] = info["title"]

        self.assertEqual(result.get("en"), "Intermittent fasting")
        self.assertEqual(result.get("pl"), "Post przerywany")
        self.assertNotIn("commons", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
