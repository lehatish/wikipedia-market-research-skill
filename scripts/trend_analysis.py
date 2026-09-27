#!/usr/bin/env python3
"""
trend_analysis.py — рахує тренд, довіру до нього та порівнює кілька рядів.

Це серце навички: саме тут вирішується "чи справді зростає інтерес, і
наскільки цьому можна довіряти", а не просто малюється лінія.

Може використовуватись і як бібліотека (import trend_analysis), і як CLI:
    python trend_analysis.py --in data.json --out analysis.json

Методологія (докладніше — references/methodology.md):

1. Тренд рахується лінійною регресією log(views + 1) від номера періоду.
   Лог-шкала обрана тому, що інтерес зазвичай змінюється мультиплікативно
   (у %, а не в абсолютних переглядах), і так відсоток зростання на місяць
   дорівнює приблизно slope * 100.
2. Надійність тренду — комбінація:
   - R² регресії (наскільки добре пряма лінія пояснює дані)
   - довжини ряду (кількість періодів; менше 12 місяців — це вже "рано робити висновки")
   - частки "викидів" (місяців зі сплеском, що сильно відхиляється від тренду —
     ознака вірусної новини, а не стійкого інтересу)
   - коефіцієнта варіації (наскільки "шумний" ряд відносно свого середнього)
3. Порівняння рядів між собою робиться на індексованих значеннях
   (перший період = 100), бо абсолютна кількість переглядів залежить від
   розміру мовної аудиторії й порівнювати її напряму некоректно.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class TrendResult:
    n_periods: int
    monthly_growth_pct: float  # приблизний % зміни за період (місяць/день) за регресією
    total_change_pct: float  # % зміни між середнім першої і останньої третини ряду
    r_squared: float
    coefficient_of_variation: float
    outlier_periods: list[str]  # дати періодів, визнаних сплесками
    outlier_share: float
    confidence: str  # "high" | "medium" | "low"
    confidence_reasons: list[str]
    warnings: list[str]


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Проста лінійна регресія. Повертає (slope, intercept, r_squared)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0, 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        return 0.0, mean_y, 0.0
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    # R^2
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        r_squared = 1.0 if all(abs(y - mean_y) < 1e-9 for y in ys) else 0.0
    else:
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        r_squared = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r_squared


def analyze_series(points: list[dict]) -> TrendResult:
    """
    points: [{"date": "2023-01", "views": 123}, ...] відсортовані за датою.
    """
    warnings: list[str] = []
    points = sorted(points, key=lambda p: p["date"])
    n = len(points)

    if n == 0:
        return TrendResult(
            n_periods=0,
            monthly_growth_pct=0.0,
            total_change_pct=0.0,
            r_squared=0.0,
            coefficient_of_variation=0.0,
            outlier_periods=[],
            outlier_share=0.0,
            confidence="low",
            confidence_reasons=["Немає даних для аналізу."],
            warnings=["Порожній ряд даних."],
        )

    views = [p["views"] for p in points]
    dates = [p["date"] for p in points]
    xs = list(range(n))
    log_ys = [math.log(v + 1) for v in views]

    slope, _intercept, r_squared = _linreg(xs, log_ys)
    # slope у log-шкалі ~= відносна зміна за період; переводимо у %
    monthly_growth_pct = (math.exp(slope) - 1) * 100

    mean_views = statistics.mean(views)
    stdev_views = statistics.pstdev(views) if n > 1 else 0.0
    cv = (stdev_views / mean_views) if mean_views > 0 else 0.0

    # Порівняння "початок vs кінець": середнє першої третини vs останньої третини.
    third = max(1, n // 3)
    start_avg = statistics.mean(views[:third])
    end_avg = statistics.mean(views[-third:])
    if start_avg > 0:
        total_change_pct = (end_avg - start_avg) / start_avg * 100
    else:
        total_change_pct = float("inf") if end_avg > 0 else 0.0
        warnings.append(
            "На початку періоду перегляди були нульовими — відсоток зростання "
            "від нуля не має змістовної інтерпретації, дивіться абсолютні цифри."
        )

    # Виявлення викидів: точки, що відхиляються від медіани більш ніж на
    # 3 медіанних абсолютних відхилення (MAD) — стійкіше до самих же
    # викидів, ніж звичайне стандартне відхилення.
    median_v = statistics.median(views)
    abs_devs = [abs(v - median_v) for v in views]
    mad = statistics.median(abs_devs) if abs_devs else 0.0
    outlier_periods = []
    if mad > 0:
        for d, v in zip(dates, views):
            if abs(v - median_v) / mad > 3:
                outlier_periods.append(d)
    elif stdev_views > 0:
        # MAD=0 трапляється, коли більшість значень однакові (типово для
        # рідкісних тем з малою аудиторією) — тоді єдиний спосіб виявити
        # різкий сплеск це порівняти зі стандартним відхиленням.
        for d, v in zip(dates, views):
            if abs(v - median_v) / stdev_views > 3:
                outlier_periods.append(d)
    outlier_share = len(outlier_periods) / n if n else 0.0

    # --- Оцінка довіри до тренду ---
    reasons = []
    score = 0  # 0..3, потім мапимо на high/medium/low

    if n >= 24:
        score += 1
        reasons.append(f"Достатньо довгий ряд ({n} періодів).")
    elif n >= 12:
        reasons.append(
            f"Ряд середньої довжини ({n} періодів) — тренд орієнтовний, "
            "бажано перевірити ще через кілька місяців."
        )
    else:
        reasons.append(
            f"Короткий ряд ({n} періодів, менше року) — робити висновки про "
            "тренд зарано, це радше знімок поточного стану."
        )

    if r_squared >= 0.5:
        score += 1
        reasons.append(f"Лінія тренду добре пояснює дані (R²={r_squared:.2f}).")
    else:
        reasons.append(
            f"Дані сильно розкидані навколо лінії тренду (R²={r_squared:.2f}) — "
            "напрям зростання/спадання менш очевидний, ніж здається на графіку."
        )

    if outlier_share <= 0.1:
        score += 1
    else:
        reasons.append(
            f"{len(outlier_periods)} з {n} періодів — це різкі сплески "
            "(можливо, вірусні новини), вони могли викривити оцінку тренду."
        )

    if cv > 1.5:
        reasons.append(
            f"Дуже високий розкид даних (CV={cv:.2f}) відносно середнього — "
            "ряд дуже шумний."
        )

    if score >= 3:
        confidence = "high"
    elif score >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    if n < 12:
        # Короткий ряд ніколи не варто маркувати як high confidence.
        confidence = "low" if confidence == "low" else "medium"

    return TrendResult(
        n_periods=n,
        monthly_growth_pct=round(monthly_growth_pct, 2),
        total_change_pct=round(total_change_pct, 2) if math.isfinite(total_change_pct) else total_change_pct,
        r_squared=round(r_squared, 3),
        coefficient_of_variation=round(cv, 3),
        outlier_periods=outlier_periods,
        outlier_share=round(outlier_share, 3),
        confidence=confidence,
        confidence_reasons=reasons,
        warnings=warnings,
    )


def index_series(points: list[dict]) -> list[dict]:
    """Індексує ряд так, щоб перше значення = 100 — для порівняння форми
    трендів між мовами/темами з дуже різною абсолютною аудиторією."""
    points = sorted(points, key=lambda p: p["date"])
    if not points:
        return []
    base = points[0]["views"]
    if base == 0:
        # Знайти перше ненульове значення, інакше індексація на нулі безглузда.
        nonzero = next((p["views"] for p in points if p["views"] > 0), None)
        if nonzero is None:
            return [{"date": p["date"], "index": 0.0} for p in points]
        base = nonzero
    return [
        {"date": p["date"], "index": round(p["views"] / base * 100, 1)}
        for p in points
    ]


def compare_series(all_series: dict[str, list[dict]]) -> dict:
    """
    all_series: {label: points} — label може бути мовним кодом або назвою теми.
    Повертає індивідуальні TrendResult + ранжування за monthly_growth_pct.
    """
    analyses = {}
    for label, points in all_series.items():
        analyses[label] = analyze_series(points)

    ranking = sorted(
        analyses.items(),
        key=lambda kv: kv[1].monthly_growth_pct,
        reverse=True,
    )
    return {
        "analyses": {label: asdict(res) for label, res in analyses.items()},
        "ranking": [label for label, _ in ranking],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="infile", required=True, help="JSON від fetch_pageviews.py")
    parser.add_argument("--out", required=True, help="Куди зберегти результат аналізу")
    args = parser.parse_args()

    with open(args.infile, encoding="utf-8") as f:
        data = json.load(f)

    series_points = {
        label: info["points"] for label, info in data.get("series", {}).items()
    }
    comparison = compare_series(series_points)

    for label, info in data.get("series", {}).items():
        comparison["analyses"][label]["indexed_points"] = index_series(info["points"])
        comparison["analyses"][label]["source_warnings"] = info.get("warnings", [])
        comparison["analyses"][label]["title"] = info.get("title")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2)

    print(f"Збережено аналіз: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
