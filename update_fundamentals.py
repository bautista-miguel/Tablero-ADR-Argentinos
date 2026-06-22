
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


API_URL = "https://www.alphavantage.co/query"
ROOT = Path(__file__).resolve().parent
TARGETS_FILE = ROOT / "price_targets.csv"
CACHE_FILE = ROOT / "fundamentals_cache.csv"

MAX_DAILY_REQUESTS = 25
OVERVIEW_REQUESTS = 15
CALENDAR_REQUESTS = 1
EARNINGS_REQUESTS_PER_RUN = MAX_DAILY_REQUESTS - OVERVIEW_REQUESTS - CALENDAR_REQUESTS

REQUEST_PAUSE_SECONDS = 1.0


def safe_float(value):
    try:
        if value in (None, "", "None", "-", "N/A"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def api_json(api_key, function, symbol):
    response = requests.get(
        API_URL,
        params={
            "function": function,
            "symbol": symbol,
            "apikey": api_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if "Information" in data:
        raise RuntimeError(data["Information"])

    if "Note" in data:
        raise RuntimeError(data["Note"])

    if "Error Message" in data:
        raise RuntimeError(data["Error Message"])

    return data


def load_cache(tickers):
    if CACHE_FILE.exists():
        cache = pd.read_csv(CACHE_FILE)
    else:
        cache = pd.DataFrame({"Ticker ADR": tickers})

    expected_columns = [
        "Beta",
        "P/E",
        "EPS TTM",
        "Próximo earnings",
        "Estado fecha earnings",
        "Último reporte",
        "EPS estimado último",
        "EPS reportado último",
        "Sorpresa EPS %",
        "Actualizado fundamentals",
        "Actualizado earnings",
    ]

    for column in expected_columns:
        if column not in cache.columns:
            cache[column] = None

    missing_tickers = sorted(set(tickers) - set(cache["Ticker ADR"].astype(str)))

    if missing_tickers:
        cache = pd.concat(
            [
                cache,
                pd.DataFrame({"Ticker ADR": missing_tickers}),
            ],
            ignore_index=True,
        )

    return cache[cache["Ticker ADR"].isin(tickers)].copy()


def fetch_calendar(api_key, tickers):
    response = requests.get(
        API_URL,
        params={
            "function": "EARNINGS_CALENDAR",
            "horizon": "12month",
            "apikey": api_key,
        },
        timeout=45,
    )
    response.raise_for_status()

    text = response.text.strip()

    if not text or text.startswith("{"):
        raise RuntimeError(
            "Alpha Vantage no devolvió el calendario CSV esperado."
        )

    calendar = pd.read_csv(io.StringIO(text))

    if "symbol" not in calendar.columns:
        raise RuntimeError("El calendario no contiene la columna symbol.")

    calendar["symbol"] = calendar["symbol"].astype(str).str.upper()
    calendar = calendar[calendar["symbol"].isin(tickers)].copy()

    if "reportDate" in calendar.columns:
        calendar["reportDate"] = pd.to_datetime(
            calendar["reportDate"],
            errors="coerce",
        )

    calendar = calendar.sort_values("reportDate")
    calendar = calendar.drop_duplicates("symbol", keep="first")

    return calendar


def choose_earnings_tickers(cache, limit):
    work = cache[["Ticker ADR", "Actualizado earnings"]].copy()
    work["Actualizado earnings"] = pd.to_datetime(
        work["Actualizado earnings"],
        errors="coerce",
    )

    work = work.sort_values(
        ["Actualizado earnings", "Ticker ADR"],
        na_position="first",
    )

    return work["Ticker ADR"].head(limit).tolist()


def main():
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Falta la variable de entorno ALPHA_VANTAGE_API_KEY."
        )

    targets = pd.read_csv(TARGETS_FILE)
    tickers = (
        targets["Ticker ADR"]
        .astype(str)
        .str.strip()
        .str.upper()
        .drop_duplicates()
        .tolist()
    )

    cache = load_cache(tickers)
    now_iso = datetime.now(timezone.utc).isoformat()

    # 15 requests: Company Overview
    for ticker in tickers:
        try:
            overview = api_json(api_key, "OVERVIEW", ticker)

            mask = cache["Ticker ADR"] == ticker

            cache.loc[mask, "Beta"] = safe_float(overview.get("Beta"))
            cache.loc[mask, "P/E"] = safe_float(overview.get("PERatio"))
            cache.loc[mask, "EPS TTM"] = safe_float(overview.get("EPS"))
            cache.loc[mask, "Actualizado fundamentals"] = now_iso

            print(f"OVERVIEW OK: {ticker}")
        except Exception as exc:
            print(f"OVERVIEW ERROR {ticker}: {exc}")

        time.sleep(REQUEST_PAUSE_SECONDS)

    # 1 request: calendario global
    try:
        calendar = fetch_calendar(api_key, tickers)

        for ticker in tickers:
            row = calendar.loc[calendar["symbol"] == ticker]
            mask = cache["Ticker ADR"] == ticker

            if row.empty:
                cache.loc[mask, "Próximo earnings"] = None
                cache.loc[mask, "Estado fecha earnings"] = None
                continue

            report_date = row["reportDate"].iloc[0]
            cache.loc[mask, "Próximo earnings"] = (
                report_date.date().isoformat()
                if pd.notna(report_date)
                else None
            )

            # Alpha Vantage no expone un campo universal de confirmación.
            # Por prudencia, el calendario se marca como estimado.
            cache.loc[mask, "Estado fecha earnings"] = "estimated"

        print("EARNINGS_CALENDAR OK")
    except Exception as exc:
        print(f"EARNINGS_CALENDAR ERROR: {exc}")

    time.sleep(REQUEST_PAUSE_SECONDS)

    # 9 requests por corrida: rota los tickers más desactualizados.
    # De esta manera el plan gratuito de 25 llamadas/día alcanza:
    # 15 OVERVIEW + 1 CALENDAR + 9 EARNINGS = 25.
    earnings_tickers = choose_earnings_tickers(
        cache,
        EARNINGS_REQUESTS_PER_RUN,
    )

    for ticker in earnings_tickers:
        try:
            earnings = api_json(api_key, "EARNINGS", ticker)
            quarterly = earnings.get("quarterlyEarnings", [])

            if quarterly:
                latest = quarterly[0]
                mask = cache["Ticker ADR"] == ticker

                cache.loc[mask, "Último reporte"] = latest.get(
                    "reportedDate"
                )
                cache.loc[mask, "EPS estimado último"] = safe_float(
                    latest.get("estimatedEPS")
                )
                cache.loc[mask, "EPS reportado último"] = safe_float(
                    latest.get("reportedEPS")
                )
                cache.loc[mask, "Sorpresa EPS %"] = safe_float(
                    latest.get("surprisePercentage")
                )
                cache.loc[mask, "Actualizado earnings"] = now_iso

            print(f"EARNINGS OK: {ticker}")
        except Exception as exc:
            print(f"EARNINGS ERROR {ticker}: {exc}")

        time.sleep(REQUEST_PAUSE_SECONDS)

    cache = cache.sort_values("Ticker ADR")
    cache.to_csv(CACHE_FILE, index=False, encoding="utf-8-sig")
    print(f"Actualizado: {CACHE_FILE}")


if __name__ == "__main__":
    main()
