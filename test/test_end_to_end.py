"""
test_end_to_end.py — прогонить ПОВНИЙ пайплайн (resolve -> fetch -> analyze
-> report) через wiki_trends.py, підмінивши реальні Wikimedia API на
локальний фейковий HTTP-сервер із передбачуваними відповідями.

Це потрібно, бо в середовищі розробки цієї навички не було мережевого
доступу до wikimedia.org / wikipedia.org / wikidata.org — тож саме так
перевірено, що весь ланцюжок скриптів (аргументи командного рядка,
формати проміжних JSON, коди повернення subprocess) насправді працює
разом, а не лише що кожен скрипт окремо синтаксично правильний.

Запуск:
    cd wikipedia-trends/test && python3 test_end_to_end.py
"""

import http.server
import json
import math
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent / "scripts"
FAKE_TITLES = {
    "en": "Intermittent fasting",
    "pl": "Post przerywany",
    "cs": "Přerušovaný půst",
}
QID = "Q1148393"


def make_handler():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # тихо, щоб не засмічувати вивід тесту

        def _send_json(self, payload, status=200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            path = parsed.path

            # --- MediaWiki API: /mw/{lang}/api.php ---
            if path.startswith("/mw/") and path.endswith("/api.php"):
                lang = path.split("/")[2]
                action = qs.get("action", [""])[0]
                if action == "query" and "list" in qs:
                    title = FAKE_TITLES.get(lang, f"{lang} test article")
                    self._send_json({"query": {"search": [{"title": title}]}})
                    return
                if action == "query" and "pageprops" in qs.get("prop", [""])[0]:
                    self._send_json(
                        {
                            "query": {
                                "pages": {
                                    "1": {"pageprops": {"wikibase_item": QID}}
                                }
                            }
                        }
                    )
                    return
                self._send_json({"error": "unhandled mw action"}, status=400)
                return

            # --- Wikidata API: /wikidata/api.php ---
            if path == "/wikidata/api.php":
                sitelinks = {
                    f"{lang}wiki": {"title": title}
                    for lang, title in FAKE_TITLES.items()
                }
                self._send_json({"entities": {QID: {"sitelinks": sitelinks}}})
                return

            # --- Pageviews REST API: /pageviews/per-article/... ---
            if path.startswith("/pageviews/per-article/"):
                parts = path[len("/pageviews/per-article/"):].split("/")
                # project, access, agent, article, granularity, start, end
                project, _access, _agent, _article, granularity, start, end = parts
                lang = project.split(".")[0]
                base_views = {"en": 5000, "pl": 2000, "cs": 500}.get(lang, 100)
                items = []
                # Генеруємо просту зростаючу серію по місяцях між start/end.
                y0, m0 = int(start[0:4]), int(start[4:6])
                y1, m1 = int(end[0:4]), int(end[4:6])
                i = 0
                y, m = y0, m0
                while (y, m) <= (y1, m1):
                    views = int(base_views * (1.02 ** i))
                    items.append({"timestamp": f"{y:04d}{m:02d}0100", "views": views})
                    i += 1
                    m += 1
                    if m > 12:
                        m = 1
                        y += 1
                self._send_json({"items": items})
                return

            self._send_json({"error": f"unknown path {path}"}, status=404)

    return Handler


def run_pipeline() -> int:
    server = http.server.HTTPServer(("127.0.0.1", 0), make_handler())
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    env = os.environ.copy()
    env["WT_MEDIAWIKI_BASE_TEMPLATE"] = f"http://127.0.0.1:{port}/mw/{{lang}}/api.php"
    env["WT_WIKIDATA_BASE"] = f"http://127.0.0.1:{port}/wikidata/api.php"
    env["WT_PAGEVIEWS_BASE"] = f"http://127.0.0.1:{port}/pageviews"

    workdir = Path("/tmp/wt_e2e_test")
    workdir.mkdir(exist_ok=True)
    out_pdf = workdir / "report.pdf"

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "wiki_trends.py"),
        "--topic", "intermittent fasting",
        "--langs", "pl,cs",
        "--hint-lang", "en",
        "--start", "2023-01-01",
        "--end", "2024-12-01",
        "--granularity", "monthly",
        "--title", "E2E test report",
        "--workdir", str(workdir),
        "--out", str(out_pdf),
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    print("STDOUT:\n", result.stdout)
    print("STDERR:\n", result.stderr)

    server.shutdown()

    assert result.returncode == 0, f"Пайплайн завершився з кодом {result.returncode}"
    assert out_pdf.exists() and out_pdf.stat().st_size > 1000, "PDF не створено або він порожній"

    analysis = json.loads((workdir / "analysis.json").read_text(encoding="utf-8"))
    for lang in ("pl", "cs"):
        assert lang in analysis["analyses"], f"Немає аналізу для {lang}"
        a = analysis["analyses"][lang]
        assert a["n_periods"] == 24, f"Очікував 24 періоди для {lang}, отримав {a['n_periods']}"
        assert a["monthly_growth_pct"] > 0, f"Очікував позитивне зростання для {lang} (синтетичні дані зростають)"

    resolved = json.loads((workdir / "resolved.json").read_text(encoding="utf-8"))
    assert resolved["pl"]["title"] == FAKE_TITLES["pl"]
    assert resolved["cs"]["title"] == FAKE_TITLES["cs"]
    assert resolved["pl"]["resolved_via"] == "sitelink"

    print("\nOK: повний пайплайн (resolve -> fetch -> analyze -> report) "
          "відпрацював коректно проти фейкового API-сервера.")
    return 0


if __name__ == "__main__":
    sys.exit(run_pipeline())
