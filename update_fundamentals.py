
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests


API_URL = "https://www.alphavantage.co/query"
ROOT = Path(__file__).resolve().parent
TARGETS_FILE = ROOT / "price_targets.csv"
CACHE_FILE = ROOT / "fundamentals_cache.csv"
METADATA_FILE = ROOT / "update_metadata.json"

# Conservative request budget. The free plan currently allows 25/day.
MAX_REQUESTS_PER_RUN = 10
MAX_OVERVIEW_PER_RUN = 1
MAX_EARNINGS_PER_RUN = 6

CALENDAR_MAX_AGE_DAYS = 7
OVERVIEW_MAX_AGE_DAYS = 35
EARNINGS_FALLBACK_MAX_AGE_DAYS = 45

# We check around the expected report date because dates can be estimated
# and provider data may arrive with a delay.
EARNINGS_WINDOW_BEFORE_DAYS = 2
EARNINGS_WINDOW_AFTER_DAYS = 7

REQUEST_PAUSE_SECONDS = 1.0


class RequestBudget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def consume(self):
        if self.used >= self.limit:
            raise RuntimeError(
                f"Daily request budget exhausted ({self.used}/{self.limit})."
            )
        self.used += 1


def safe_float(value):
    try:
        if value in (None, "", "None", "-", "N/A"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_metadata():
    if not METADATA_FILE.exists():
        return {
            "last_calendar_update": None,
            "last_successful_run": None,
            "version": 1,
        }

    try:
        return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "last_calendar_update": None,
            "last_successful_run": None,
            "version": 1,
        }


def save_metadata(metadata):
    METADATA_FILE.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def api_json(api_key, function, symbol, budget):
    budget.consume()

    response = requests.get(
        API_URL,
        params={
            "function": function,
            "symbol": symbol,
            "apikey": api_key,
        },
        timeout=40,
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

    rename_map = {
        "P/E": "P/E API control",
        "Actualizado fundamentals": "Actualizado overview",
    }
    cache = cache.rename(columns=rename_map)

    expected_columns = [
        "Beta",
        "P/E API control",
        "EPS TTM",
        "Próximo earnings",
        "Estado fecha earnings",
        "Último reporte",
        "EPS estimado último",
        "EPS reportado último",
        "Sorpresa EPS %",
        "Actualizado overview",
        "Actualizado calendario",
        "Actualizado earnings",
    ]

    for column in expected_columns:
        if column not in cache.columns:
            cache[column] = None

    cache["Ticker ADR"] = (
        cache["Ticker ADR"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    missing_tickers = sorted(
        set(tickers) - set(cache["Ticker ADR"].tolist())
    )

    if missing_tickers:
        cache = pd.concat(
            [
                cache,
                pd.DataFrame({"Ticker ADR": missing_tickers}),
            ],
            ignore_index=True,
        )

    cache = cache[cache["Ticker ADR"].isin(tickers)].copy()
    cache = cache.drop_duplicates("Ticker ADR", keep="last")

    return cache


def is_stale(value, max_age_days):
    parsed = pd.to_datetime(value, errors="coerce", utc=True)

    if pd.isna(parsed):
        return True

    now = pd.Timestamp.now(tz="UTC")
    return (now - parsed).days >= max_age_days


def fetch_calendar(api_key, tickers, budget):
    budget.consume()

    response = requests.get(
        API_URL,
        params={
            "function": "EARNINGS_CALENDAR",
            "horizon": "12month",
            "apikey": api_key,
        },
        timeout=60,
    )
    response.raise_for_status()
    text = response.text.strip()

    if not text or text.startswith("{"):
        raise RuntimeError(
            "Alpha Vantage did not return the expected CSV calendar."
        )

    calendar = pd.read_csv(io.StringIO(text))

    if "symbol" not in calendar.columns:
        raise RuntimeError("Calendar response has no symbol column.")

    calendar["symbol"] = (
        calendar["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    calendar = calendar[calendar["symbol"].isin(tickers)].copy()

    if "reportDate" in calendar.columns:
        calendar["reportDate"] = pd.to_datetime(
            calendar["reportDate"],
            errors="coerce",
        )

    calendar = calendar.sort_values("reportDate")
    calendar = calendar.drop_duplicates("symbol", keep="first")

    return calendar


def choose_overview_tickers(cache, limit):
    work = cache[
        ["Ticker ADR", "Actualizado overview", "Beta", "EPS TTM"]
    ].copy()

    work["Actualizado overview parsed"] = pd.to_datetime(
        work["Actualizado overview"],
        errors="coerce",
        utc=True,
    )

    missing = work["Beta"].isna() | work["EPS TTM"].isna()
    stale = work["Actualizado overview"].apply(
        lambda value: is_stale(value, OVERVIEW_MAX_AGE_DAYS)
    )

    candidates = work[missing | stale].copy()

    # Oldest or never updated first. This naturally distributes work
    # across weekdays without hard-coded ticker groups.
    candidates = candidates.sort_values(
        ["Actualizado overview parsed", "Ticker ADR"],
        na_position="first",
    )

    return candidates["Ticker ADR"].head(limit).tolist()


def earnings_needs_refresh(row, today):
    last_update = row.get("Actualizado earnings")
    next_earnings = parse_date(row.get("Próximo earnings"))
    last_report = parse_date(row.get("Último reporte"))
    eps_ttm = safe_float(row.get("EPS TTM"))

    # Missing essential data gets priority.
    if eps_ttm is None or last_report is None:
        return True, 0

    # Around a known earnings date, check for new data.
    if next_earnings is not None:
        start = next_earnings - timedelta(
            days=EARNINGS_WINDOW_BEFORE_DAYS
        )
        end = next_earnings + timedelta(
            days=EARNINGS_WINDOW_AFTER_DAYS
        )

        if start <= today <= end:
            # Higher priority the closer we are to or after the report.
            distance = abs((today - next_earnings).days)
            return True, 1 + distance

    # Safety net for stale earnings data.
    if is_stale(last_update, EARNINGS_FALLBACK_MAX_AGE_DAYS):
        return True, 100

    return False, 999


def choose_earnings_tickers(cache, limit):
    today = date.today()
    rows = []

    for _, row in cache.iterrows():
        needed, priority = earnings_needs_refresh(row, today)

        if needed:
            updated = pd.to_datetime(
                row.get("Actualizado earnings"),
                errors="coerce",
                utc=True,
            )

            rows.append(
                {
                    "Ticker ADR": row["Ticker ADR"],
                    "priority": priority,
                    "updated": updated,
                }
            )

    if not rows:
        return []

    work = pd.DataFrame(rows)
    work = work.sort_values(
        ["priority", "updated", "Ticker ADR"],
        na_position="first",
    )

    return work["Ticker ADR"].head(limit).tolist()


def calculate_ttm_eps(quarterly):
    values = []

    for item in quarterly[:4]:
        value = safe_float(item.get("reportedEPS"))

        if value is not None:
            values.append(value)

    if len(values) < 4:
        return None

    return sum(values)


def update_overview(cache, ticker, overview, now_iso):
    mask = cache["Ticker ADR"] == ticker

    cache.loc[mask, "Beta"] = safe_float(overview.get("Beta"))
    cache.loc[mask, "P/E API control"] = safe_float(
        overview.get("PERatio")
    )

    overview_eps = safe_float(overview.get("EPS"))

    if overview_eps is not None:
        cache.loc[mask, "EPS TTM"] = overview_eps

    cache.loc[mask, "Actualizado overview"] = now_iso


def update_earnings(cache, ticker, earnings, now_iso):
    quarterly = earnings.get("quarterlyEarnings", [])

    if not quarterly:
        return False

    latest = quarterly[0]
    mask = cache["Ticker ADR"] == ticker

    cache.loc[mask, "Último reporte"] = latest.get("reportedDate")
    cache.loc[mask, "EPS estimado último"] = safe_float(
        latest.get("estimatedEPS")
    )
    cache.loc[mask, "EPS reportado último"] = safe_float(
        latest.get("reportedEPS")
    )
    cache.loc[mask, "Sorpresa EPS %"] = safe_float(
        latest.get("surprisePercentage")
    )

    ttm_eps = calculate_ttm_eps(quarterly)

    if ttm_eps is not None:
        cache.loc[mask, "EPS TTM"] = ttm_eps

    cache.loc[mask, "Actualizado earnings"] = now_iso

    return True


def main():
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Missing environment variable ALPHA_VANTAGE_API_KEY."
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
    metadata = load_metadata()
    budget = RequestBudget(MAX_REQUESTS_PER_RUN)
    now_iso = utc_now_iso()

    print(
        f"Starting smart update with a maximum of "
        f"{MAX_REQUESTS_PER_RUN} requests."
    )

    # 1) Weekly global calendar refresh: only one API request.
    if is_stale(
        metadata.get("last_calendar_update"),
        CALENDAR_MAX_AGE_DAYS,
    ):
        try:
            calendar = fetch_calendar(api_key, tickers, budget)

            for ticker in tickers:
                row = calendar.loc[calendar["symbol"] == ticker]
                mask = cache["Ticker ADR"] == ticker

                if row.empty:
                    continue

                report_date = row["reportDate"].iloc[0]

                if pd.notna(report_date):
                    cache.loc[mask, "Próximo earnings"] = (
                        report_date.date().isoformat()
                    )
                    cache.loc[
                        mask,
                        "Estado fecha earnings",
                    ] = "estimated"
                    cache.loc[
                        mask,
                        "Actualizado calendario",
                    ] = now_iso

            metadata["last_calendar_update"] = now_iso
            print("CALENDAR OK: weekly refresh completed.")
        except Exception as exc:
            print(f"CALENDAR ERROR: {exc}")

        time.sleep(REQUEST_PAUSE_SECONDS)
    else:
        print("CALENDAR SKIPPED: cached calendar is still fresh.")

    # 2) One stale/missing overview per business-day run.
    overview_tickers = choose_overview_tickers(
        cache,
        MAX_OVERVIEW_PER_RUN,
    )

    for ticker in overview_tickers:
        try:
            overview = api_json(
                api_key,
                "OVERVIEW",
                ticker,
                budget,
            )
            update_overview(cache, ticker, overview, now_iso)
            print(f"OVERVIEW OK: {ticker}")
        except Exception as exc:
            print(f"OVERVIEW ERROR {ticker}: {exc}")

        time.sleep(REQUEST_PAUSE_SECONDS)

    if not overview_tickers:
        print("OVERVIEW SKIPPED: no stale overview data.")

    # 3) Earnings only for missing/stale tickers or around report dates.
    remaining_budget = max(
        0,
        min(
            MAX_EARNINGS_PER_RUN,
            MAX_REQUESTS_PER_RUN - budget.used,
        ),
    )

    earnings_tickers = choose_earnings_tickers(
        cache,
        remaining_budget,
    )

    for ticker in earnings_tickers:
        try:
            earnings = api_json(
                api_key,
                "EARNINGS",
                ticker,
                budget,
            )
            updated = update_earnings(
                cache,
                ticker,
                earnings,
                now_iso,
            )

            if updated:
                print(f"EARNINGS OK: {ticker}")
            else:
                print(f"EARNINGS EMPTY: {ticker}")
        except Exception as exc:
            print(f"EARNINGS ERROR {ticker}: {exc}")

        time.sleep(REQUEST_PAUSE_SECONDS)

    if not earnings_tickers:
        print("EARNINGS SKIPPED: no ticker requires refresh today.")

    metadata["last_successful_run"] = now_iso

    cache = cache.sort_values("Ticker ADR")
    cache.to_csv(
        CACHE_FILE,
        index=False,
        encoding="utf-8-sig",
    )
    save_metadata(metadata)

    print(
        f"Finished. Requests used: {budget.used}/"
        f"{MAX_REQUESTS_PER_RUN}."
    )


if __name__ == "__main__":
    main()
