#!/usr/bin/env python3
"""
build_report.py — зібрати односторінковий PDF-звіт із графіком(-ами) та
короткими висновками з JSON, який виготовив trend_analysis.py.

Навмисно використовує лише matplotlib (без reportlab/fpdf), щоб не
додавати зайвих залежностей: PDF — це просто matplotlib Figure,
збережена через savefig(..., format="pdf").

Приклад:
    python build_report.py --analysis analysis.json --raw data.json \
        --title "Post przerywany: PL vs CS" \
        --out report.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap

import matplotlib

matplotlib.use("Agg")  # без дисплея — рендеримо напряму у файл
import matplotlib.pyplot as plt


CONFIDENCE_LABELS_UK = {
    "high": "Висока довіра",
    "medium": "Помірна довіра",
    "low": "Низька довіра",
}


def _fmt_pct(x) -> str:
    if x is None:
        return "н/д"
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return "н/д (базове значення = 0)"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.1f}%"


def build_report(analysis: dict, title: str, out_path: str, subtitle: str = "") -> None:
    analyses = analysis["analyses"]
    labels = list(analyses.keys())

    fig = plt.figure(figsize=(8.27, 11.69))  # A4 портрет, для друку/шерингу
    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.97)
    if subtitle:
        fig.text(0.5, 0.945, subtitle, ha="center", fontsize=10, color="#555555")

    # --- Верхня частина: графік індексованих трендів (порівнянних між рядами) ---
    ax_chart = fig.add_axes([0.09, 0.55, 0.85, 0.36])
    for label in labels:
        idx_points = analyses[label].get("indexed_points", [])
        if not idx_points:
            continue
        xs = [p["date"] for p in idx_points]
        ys = [p["index"] for p in idx_points]
        display_title = analyses[label].get("title") or label
        ax_chart.plot(xs, ys, marker="o", markersize=3, linewidth=1.6, label=f"{label}: {display_title}")

    ax_chart.set_ylabel("Індекс (перший період = 100)")
    ax_chart.set_title("Динаміка інтересу (індексовано для порівнянності)", fontsize=11)
    ax_chart.legend(fontsize=8, loc="upper left")
    ax_chart.tick_params(axis="x", rotation=60, labelsize=7)
    ax_chart.grid(alpha=0.3)

    # --- Таблиця ключових метрик ---
    ax_table = fig.add_axes([0.09, 0.36, 0.85, 0.15])
    ax_table.axis("off")
    col_labels = ["Ряд", "К-ть\nперіодів", "Зростання\n/період", "Зміна\nпочаток→кінець", "R²", "Довіра"]
    rows = []
    ranking = analysis.get("ranking", labels)
    for label in ranking:
        a = analyses[label]
        rows.append(
            [
                label,
                str(a["n_periods"]),
                _fmt_pct(a["monthly_growth_pct"]),
                _fmt_pct(a["total_change_pct"]),
                f"{a['r_squared']:.2f}",
                CONFIDENCE_LABELS_UK.get(a["confidence"], a["confidence"]),
            ]
        )
    table = ax_table.table(cellText=rows, colLabels=col_labels, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.6)

    # --- Текстовий блок: висновки та застереження ---
    ax_text = fig.add_axes([0.09, 0.03, 0.85, 0.30])
    ax_text.axis("off")

    lines: list[str] = []
    lines.append("Ключові спостереження:")
    for label in ranking:
        a = analyses[label]
        growth_desc = _fmt_pct(a["monthly_growth_pct"])
        lines.append(f"• {label} ({a.get('title', '')}): ~{growth_desc} за період, {CONFIDENCE_LABELS_UK.get(a['confidence'])}.")

    lines.append("")
    lines.append("Застереження та припущення:")
    lines.append(
        "• Перегляди Wikipedia відображають цікавість читати про тему, "
        "а не готовність платити за продукт — це сигнал для подальшої перевірки, а не остаточний доказ попиту."
    )
    lines.append(
        "• Дані Pageviews API доступні лише з липня 2015 і відфільтровані від ботів (agent=user)."
    )
    seen_warnings = set()
    for label in ranking:
        for w in analyses[label].get("confidence_reasons", []) + analyses[label].get("warnings", []) + analyses[label].get("source_warnings", []):
            if w not in seen_warnings:
                lines.append(f"• [{label}] {w}")
                seen_warnings.add(w)

    wrapped = []
    for line in lines:
        if line.startswith("•"):
            wrapped.extend(textwrap.wrap(line, width=100, subsequent_indent="   "))
        else:
            wrapped.append(line)

    ax_text.text(
        0, 1, "\n".join(wrapped), fontsize=8.5, va="top", ha="left", wrap=True,
        family="DejaVu Sans",
    )

    fig.savefig(out_path, format="pdf")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", required=True, help="JSON від trend_analysis.py")
    parser.add_argument("--title", required=True, help="Заголовок звіту")
    parser.add_argument("--subtitle", default="", help="Необов'язковий підзаголовок (напр. період і мови)")
    parser.add_argument("--out", required=True, help="Шлях до вихідного PDF")
    args = parser.parse_args()

    with open(args.analysis, encoding="utf-8") as f:
        analysis = json.load(f)

    build_report(analysis, args.title, args.out, subtitle=args.subtitle)
    print(f"Звіт збережено: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
