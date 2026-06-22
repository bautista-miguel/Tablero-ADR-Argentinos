
import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh


st.set_page_config(
    page_title="ADRs Argentinos",
    page_icon="🇦🇷",
    layout="wide",
)

DEFAULT_FILE = "price_targets.csv"

COLUMN_ALIASES = {
    "ticker": "Ticker ADR",
    "ticker adr": "Ticker ADR",
    "adr": "Ticker ADR",
    "compania": "Compañía",
    "compañía": "Compañía",
    "empresa": "Compañía",
    "sector": "Sector",
    "rating": "Rating / consenso",
    "rating / consenso": "Rating / consenso",
    "consenso": "Rating / consenso",
    "target": "Target prom. 12m",
    "target prom. 12m": "Target prom. 12m",
    "target promedio": "Target prom. 12m",
    "pt promedio": "Target prom. 12m",
    "fecha pt": "Fecha actualización PT",
    "fecha actualizacion pt": "Fecha actualización PT",
    "fecha actualización pt": "Fecha actualización PT",
}

REQUIRED_COLUMNS = [
    "Ticker ADR",
    "Compañía",
    "Sector",
    "Rating / consenso",
    "Target prom. 12m",
]


st.markdown(
    """
    <style>
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 2rem;
            max-width: 1500px;
        }

        h1, h2, h3 {
            letter-spacing: -0.03em;
        }

        [data-testid="stMetric"] {
            background: #eef5f8;
            border: 1px solid #c9dce6;
            padding: 16px;
            border-radius: 14px;
        }

        .hero-card {
            background: linear-gradient(135deg, #0d4964 0%, #146b8e 100%);
            color: white;
            border-radius: 18px;
            padding: 24px 28px;
            margin: 8px 0 22px 0;
            box-shadow: 0 8px 24px rgba(13, 73, 100, 0.16);
        }

        .hero-grid {
            display: grid;
            grid-template-columns: 1.1fr 1.1fr 1.1fr 1.7fr;
            gap: 24px;
            align-items: center;
        }

        .hero-label {
            font-size: 0.82rem;
            opacity: 0.78;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 4px;
        }

        .hero-value {
            font-size: 2.2rem;
            line-height: 1.05;
            font-weight: 800;
        }

        .hero-value.up {
            color: #69f0ae;
        }

        .hero-value.down {
            color: #ff8a80;
        }

        .hero-company {
            border-left: 1px solid rgba(255,255,255,0.25);
            padding-left: 22px;
        }

        .hero-company-name {
            font-size: 1.35rem;
            font-weight: 750;
            margin-bottom: 6px;
        }

        .hero-company-meta {
            font-size: 0.95rem;
            opacity: 0.82;
        }

        @media (max-width: 900px) {
            .hero-grid {
                grid-template-columns: 1fr 1fr;
            }

            .hero-company {
                border-left: none;
                padding-left: 0;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def normalize_column_name(name: str) -> str:
    clean = str(name).strip()
    return COLUMN_ALIASES.get(clean.lower(), clean)


def parse_number(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    values = series.astype(str).str.strip()

    both = values.str.contains(r"\.", regex=True) & values.str.contains(",", regex=False)
    values.loc[both] = (
        values.loc[both]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )

    only_comma = values.str.contains(",", regex=False) & ~values.str.contains(r"\.", regex=True)
    values.loc[only_comma] = values.loc[only_comma].str.replace(",", ".", regex=False)

    return pd.to_numeric(values, errors="coerce")


def load_targets(uploaded_file=None) -> pd.DataFrame:
    if uploaded_file is not None:
        filename = uploaded_file.name.lower()

        if filename.endswith(".csv"):
            raw = uploaded_file.getvalue()
            try:
                df = pd.read_csv(io.BytesIO(raw))
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file)
        else:
            raise ValueError("El archivo debe ser CSV, XLSX o XLS.")
    else:
        df = pd.read_csv(DEFAULT_FILE)

    df = df.rename(columns={c: normalize_column_name(c) for c in df.columns})

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Faltan columnas obligatorias: " + ", ".join(missing))

    if "Fecha actualización PT" not in df.columns:
        df["Fecha actualización PT"] = pd.NaT
    else:
        df["Fecha actualización PT"] = pd.to_datetime(
            df["Fecha actualización PT"],
            errors="coerce",
        )

    df["Ticker ADR"] = (
        df["Ticker ADR"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["Target prom. 12m"] = parse_number(df["Target prom. 12m"])

    df = df.dropna(subset=["Ticker ADR", "Target prom. 12m"])
    df = df.drop_duplicates(subset=["Ticker ADR"], keep="last")

    return df


@st.cache_data(ttl=60, show_spinner=False)
def download_market_snapshot(tickers: tuple[str, ...]) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()

    data = yf.download(
        tickers=list(tickers),
        period="10d",
        interval="1d",
        auto_adjust=True,
        group_by="column",
        threads=True,
        progress=False,
    )

    if data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        close = data["Close"].copy()
        volume = data["Volume"].copy() if "Volume" in data.columns.get_level_values(0) else None
    else:
        ticker = tickers[0]
        close = data[["Close"]].rename(columns={"Close": ticker})
        volume = (
            data[["Volume"]].rename(columns={"Volume": ticker})
            if "Volume" in data
            else None
        )

    rows = []

    for ticker in tickers:
        if ticker not in close.columns:
            rows.append({"Ticker ADR": ticker})
            continue

        prices = close[ticker].dropna()

        if prices.empty:
            rows.append({"Ticker ADR": ticker})
            continue

        last_price = float(prices.iloc[-1])
        previous_price = float(prices.iloc[-2]) if len(prices) >= 2 else None

        daily_change = (
            (last_price / previous_price - 1) * 100
            if previous_price not in (None, 0)
            else None
        )

        last_volume = None

        if volume is not None and ticker in volume.columns:
            volume_values = volume[ticker].dropna()
            if not volume_values.empty:
                last_volume = float(volume_values.iloc[-1])

        rows.append(
            {
                "Ticker ADR": ticker,
                "Precio hoy": last_price,
                "Cierre anterior": previous_price,
                "Cambio % Diario": daily_change,
                "Volumen": last_volume,
                "Fecha precio": prices.index[-1],
            }
        )

    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def download_history(ticker: str, period: str) -> pd.DataFrame:
    hist = yf.download(
        ticker,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if hist.empty:
        return pd.DataFrame()

    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)

    return hist.dropna(how="all")


def build_dashboard_table(targets: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    df = targets.merge(market, on="Ticker ADR", how="left")

    df["Upside vs Prom"] = (
        df["Target prom. 12m"] / df["Precio hoy"] - 1
    ) * 100

    return df


def style_dashboard(df: pd.DataFrame):
    def color_percent(value):
        if pd.isna(value):
            return ""
        if value > 0:
            return "color: #008f5a; font-weight: 800;"
        if value < 0:
            return "color: #d62828; font-weight: 800;"
        return "color: #555;"

    return (
        df.style
        .map(
            color_percent,
            subset=["Upside vs Prom", "Cambio % Diario"],
        )
        .format(
            {
                "Precio hoy": "USD {:,.2f}",
                "Target prom. 12m": "USD {:,.2f}",
                "Upside vs Prom": "{:+.1f}%",
                "Cambio % Diario": "{:+.2f}%",
                "Volumen": "{:,.0f}",
            },
            na_rep="—",
        )
    )



# ---------------------------------------------------------------------
# Fundamentals y earnings
# ---------------------------------------------------------------------

FUNDAMENTALS_CACHE_FILE = Path("fundamentals_cache.csv")
SECTOR_PE_FILE = Path("sector_pe.csv")


def safe_float(value):
    try:
        if value in (None, "", "None", "-", "N/A"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=600, show_spinner=False)
def load_fundamentals_cache() -> pd.DataFrame:
    if not FUNDAMENTALS_CACHE_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(FUNDAMENTALS_CACHE_FILE)

    numeric_columns = [
        "Beta",
        "P/E",
        "EPS TTM",
        "EPS estimado último",
        "EPS reportado último",
        "Sorpresa EPS %",
    ]

    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    date_columns = [
        "Próximo earnings",
        "Último reporte",
        "Actualizado fundamentals",
        "Actualizado earnings",
    ]

    for column in date_columns:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce")

    return df


@st.cache_data(ttl=600, show_spinner=False)
def load_sector_pe() -> pd.DataFrame:
    if not SECTOR_PE_FILE.exists():
        return pd.DataFrame(columns=["Sector", "P/E sector", "Fuente", "Fecha"])

    df = pd.read_csv(SECTOR_PE_FILE)
    df["P/E sector"] = pd.to_numeric(df["P/E sector"], errors="coerce")
    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    return df


def pe_status(row) -> str:
    eps = row.get("EPS TTM")
    pe = row.get("P/E")

    if pd.notna(eps) and eps <= 0:
        return "N/M — EPS negativo"

    if pd.notna(pe) and pe > 0:
        return f"{pe:.2f}"

    return "—"


def earnings_result_label(surprise):
    if pd.isna(surprise):
        return "Sin datos"

    if surprise > 2:
        return "Mejor"

    if surprise < -2:
        return "Peor"

    return "En línea"


def earnings_result_icon(label):
    return {
        "Mejor": "🟢 Mejor",
        "En línea": "⚪ En línea",
        "Peor": "🔴 Peor",
        "Sin datos": "—",
    }.get(label, label)


def earnings_date_label(date_value, status):
    if pd.isna(date_value):
        return "—"

    label = pd.Timestamp(date_value).strftime("%d/%m/%Y")
    if str(status).lower() == "estimated":
        return f"{label} estimada"
    return label


st.title("🇦🇷 Tablero de ADRs argentinos")
st.caption("Precios, target prices, upside e históricos.")

with st.sidebar:
    st.header("Configuración")

    uploaded = st.file_uploader(
        "Subir base de price targets",
        type=["csv", "xlsx", "xls"],
    )

    auto_refresh = st.toggle("Actualización automática", value=False)

    refresh_seconds = st.selectbox(
        "Frecuencia",
        options=[60, 120, 300, 600],
        index=2,
        format_func=lambda x: f"{x // 60} min",
        disabled=not auto_refresh,
    )

    if auto_refresh:
        st_autorefresh(
            interval=refresh_seconds * 1000,
            key="market_refresh",
        )

    if st.button("Actualizar ahora", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    st.caption(
        "La cotización es el último dato disponible en Yahoo Finance."
    )


try:
    targets = load_targets(uploaded)
except Exception as exc:
    st.error(f"No se pudo leer la base de price targets: {exc}")
    st.stop()


tickers = tuple(targets["Ticker ADR"].dropna().unique().tolist())

with st.spinner("Descargando precios..."):
    market = download_market_snapshot(tickers)

dashboard = build_dashboard_table(targets, market)


filter_col1, filter_col2, filter_col3 = st.columns([1.1, 1.1, 2])

with filter_col1:
    sectors = st.multiselect(
        "Sector",
        options=sorted(dashboard["Sector"].dropna().unique()),
    )

with filter_col2:
    ratings = st.multiselect(
        "Rating",
        options=sorted(dashboard["Rating / consenso"].dropna().unique()),
    )

with filter_col3:
    search = st.text_input(
        "Buscar ticker o compañía",
        placeholder="Ej.: YPF, Galicia, energía...",
    )


filtered = dashboard.copy()

if sectors:
    filtered = filtered[filtered["Sector"].isin(sectors)]

if ratings:
    filtered = filtered[filtered["Rating / consenso"].isin(ratings)]

if search:
    query = search.strip()

    mask = (
        filtered["Ticker ADR"].str.contains(query, case=False, na=False)
        | filtered["Compañía"].str.contains(query, case=False, na=False)
        | filtered["Sector"].str.contains(query, case=False, na=False)
    )

    filtered = filtered[mask]


filtered = filtered.sort_values(
    "Upside vs Prom",
    ascending=False,
    na_position="last",
)



tab_panorama, tab_graficos, tab_fundamentals = st.tabs(
    ["Panorama", "Gráficos", "Fundamentals & Earnings"]
)

with tab_panorama:
    st.subheader("Panorama general")

    display_columns = [
        "Ticker ADR",
        "Precio hoy",
        "Target prom. 12m",
        "Upside vs Prom",
        "Compañía",
        "Sector",
        "Rating / consenso",
        "Cambio % Diario",
        "Volumen",
        "Fecha precio",
        "Fecha actualización PT",
    ]

    display_columns = [c for c in display_columns if c in filtered.columns]
    table = filtered[display_columns].copy()


    event = st.dataframe(
        style_dashboard(table),
        width="stretch",
        height=560,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="adr_table",
        column_config={
            "Ticker ADR": st.column_config.TextColumn(
                "ADR",
                pinned=True,
                width="small",
            ),
            "Precio hoy": st.column_config.NumberColumn(
                "Precio USD",
                format="USD %.2f",
                width="small",
            ),
            "Target prom. 12m": st.column_config.NumberColumn(
                "Target Price",
                format="USD %.2f",
                width="small",
            ),
            "Upside vs Prom": st.column_config.NumberColumn(
                "Upside",
                format="%.1f%%",
                width="small",
            ),
            "Compañía": st.column_config.TextColumn(
                "Compañía",
                width="medium",
            ),
            "Sector": st.column_config.TextColumn(
                "Sector",
                width="medium",
            ),
            "Rating / consenso": st.column_config.TextColumn(
                "Consenso",
                width="medium",
            ),
            "Cambio % Diario": st.column_config.NumberColumn(
                "Cambio diario",
                format="%.2f%%",
                width="small",
            ),
            "Fecha precio": st.column_config.DatetimeColumn(
                "Fecha precio",
                format="DD/MM/YYYY",
            ),
            "Fecha actualización PT": st.column_config.DateColumn(
                "Fecha PT",
                format="DD/MM/YYYY",
            ),
        },
    )


    csv_data = filtered.to_csv(index=False).encode("utf-8-sig")

    st.download_button(
        "Descargar tabla actual en CSV",
        data=csv_data,
        file_name=f"adrs_argentinos_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
    )

with tab_graficos:
    st.subheader("Gráficos y comparación")
    st.caption(
        "Las filas marcadas en Panorama aparecen automáticamente acá. "
        "También podés modificar la selección desde el campo inferior."
    )

    ticker_options = filtered["Ticker ADR"].tolist()

    if ticker_options:
        selected_from_table = []

        try:
            selected_rows = event.selection.rows
            selected_from_table = [
                table.iloc[row_index]["Ticker ADR"]
                for row_index in selected_rows
                if row_index < len(table)
            ]
        except Exception:
            selected_from_table = []

        default_selection = (
            selected_from_table
            if selected_from_table
            else ticker_options[:3]
        )

        selected_tickers = st.multiselect(
            "ADRs incluidos en el gráfico",
            options=ticker_options,
            default=default_selection,
            max_selections=10,
            help=(
                "Podés marcarlos en la tabla de la pestaña Panorama "
                "o agregarlos y quitarlos directamente desde acá."
            ),
        )

        period_labels = {
            "1 mes": "1mo",
            "3 meses": "3mo",
            "6 meses": "6mo",
            "1 año": "1y",
            "2 años": "2y",
            "5 años": "5y",
            "Máximo": "max",
        }

        period_name = st.segmented_control(
            "Período",
            options=list(period_labels.keys()),
            default="1 año",
        )

        chart_mode = st.radio(
            "Tipo de gráfico",
            options=["Rendimiento normalizado", "Precio en USD"],
            horizontal=True,
        )

        @st.cache_data(ttl=300, show_spinner=False)
        def download_multiple_history(tickers: tuple[str, ...], period: str) -> pd.DataFrame:
            if not tickers:
                return pd.DataFrame()

            data = yf.download(
                tickers=list(tickers),
                period=period,
                interval="1d",
                auto_adjust=True,
                group_by="column",
                threads=True,
                progress=False,
            )

            if data.empty:
                return pd.DataFrame()

            if isinstance(data.columns, pd.MultiIndex):
                close = data["Close"].copy()
            else:
                close = data[["Close"]].copy()
                close.columns = [tickers[0]]

            close = close.dropna(how="all")
            close = close[[ticker for ticker in tickers if ticker in close.columns]]

            return close

        if not selected_tickers:
            st.info("Seleccioná al menos un ADR para generar el gráfico.")
        else:
            with st.spinner("Descargando históricos seleccionados..."):
                close_prices = download_multiple_history(
                    tuple(selected_tickers),
                    period_labels[period_name],
                )

            if close_prices.empty:
                st.warning("No se pudieron descargar los históricos seleccionados.")
            else:
                fig = go.Figure()

                if chart_mode == "Rendimiento normalizado":
                    normalized = close_prices.copy()

                    plotly_colors = [
                        "#636EFA",
                        "#EF553B",
                        "#00CC96",
                        "#AB63FA",
                        "#FFA15A",
                        "#19D3F3",
                        "#FF6692",
                        "#B6E880",
                        "#FF97FF",
                        "#FECB52",
                    ]

                    for ticker in normalized.columns:
                        first_valid = normalized[ticker].dropna()
                        if not first_valid.empty:
                            normalized[ticker] = (
                                normalized[ticker] / first_valid.iloc[0] * 100
                            )

                    for index, ticker in enumerate(normalized.columns):
                        color = plotly_colors[index % len(plotly_colors)]

                        fig.add_trace(
                            go.Scatter(
                                x=normalized.index,
                                y=normalized[ticker],
                                mode="lines",
                                name=ticker,
                                line=dict(color=color),
                            )
                        )

                        original_series = close_prices[ticker].dropna()

                        if original_series.empty:
                            continue

                        initial_price = float(original_series.iloc[0])

                        target_row = filtered.loc[
                            filtered["Ticker ADR"] == ticker,
                            "Target prom. 12m",
                        ]

                        if (
                            not target_row.empty
                            and pd.notna(target_row.iloc[0])
                            and initial_price > 0
                        ):
                            target_price = float(target_row.iloc[0])
                            normalized_target = (
                                target_price / initial_price * 100
                            )

                            fig.add_trace(
                                go.Scatter(
                                    x=[
                                        normalized.index.min(),
                                        normalized.index.max(),
                                    ],
                                    y=[
                                        normalized_target,
                                        normalized_target,
                                    ],
                                    mode="lines",
                                    name=f"{ticker} PT",
                                    line=dict(
                                        color=color,
                                        dash="dot",
                                        width=2,
                                    ),
                                    hovertemplate=(
                                        f"{ticker} PT normalizado: "
                                        f"{normalized_target:.1f}"
                                        "<extra></extra>"
                                    ),
                                )
                            )

                            fig.add_annotation(
                                x=normalized.index.max(),
                                y=normalized_target,
                                text=f"{ticker} PT {normalized_target:.0f}",
                                showarrow=False,
                                xanchor="left",
                                xshift=8,
                                font=dict(color=color),
                            )

                    fig.update_layout(
                        title="Rendimiento comparado — base 100",
                        yaxis_title="Índice base 100",
                    )

                else:
                    for ticker in close_prices.columns:
                        fig.add_trace(
                            go.Scatter(
                                x=close_prices.index,
                                y=close_prices[ticker],
                                mode="lines",
                                name=ticker,
                            )
                        )

                        target_row = filtered.loc[
                            filtered["Ticker ADR"] == ticker,
                            "Target prom. 12m",
                        ]

                        if not target_row.empty and pd.notna(target_row.iloc[0]):
                            target_value = float(target_row.iloc[0])

                            fig.add_hline(
                                y=target_value,
                                line_dash="dot",
                                annotation_text=f"{ticker} PT {target_value:,.2f}",
                                annotation_position="right",
                            )

                    fig.update_layout(
                        title="Precios históricos en USD",
                        yaxis_title="USD",
                    )

                fig.update_layout(
                    xaxis_title="",
                    height=620,
                    margin=dict(l=20, r=20, t=65, b=20),
                    hovermode="x unified",
                    legend_title_text="ADR",
                )

                st.plotly_chart(fig, width="stretch")

                summary_rows = []

                for ticker in close_prices.columns:
                    series = close_prices[ticker].dropna()

                    if series.empty:
                        continue

                    first_price = float(series.iloc[0])
                    last_price = float(series.iloc[-1])
                    period_return = (last_price / first_price - 1) * 100

                    target_row = filtered.loc[
                        filtered["Ticker ADR"] == ticker
                    ]

                    target = (
                        float(target_row["Target prom. 12m"].iloc[0])
                        if not target_row.empty
                        else None
                    )

                    upside = (
                        (target / last_price - 1) * 100
                        if target is not None and last_price
                        else None
                    )

                    company = (
                        target_row["Compañía"].iloc[0]
                        if not target_row.empty
                        else ""
                    )

                    summary_rows.append(
                        {
                            "ADR": ticker,
                            "Precio inicial": first_price,
                            "Precio actual": last_price,
                            "Rendimiento período": period_return,
                            "Target Price": target,
                            "Upside": upside,
                            "Compañía": company,
                        }
                    )

                comparison_summary = pd.DataFrame(summary_rows)

                st.dataframe(
                    comparison_summary,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Precio inicial": st.column_config.NumberColumn(
                            "Precio inicial",
                            format="USD %.2f",
                        ),
                        "Precio actual": st.column_config.NumberColumn(
                            "Precio actual",
                            format="USD %.2f",
                        ),
                        "Rendimiento período": st.column_config.NumberColumn(
                            "Rendimiento período",
                            format="%.1f%%",
                        ),
                        "Target Price": st.column_config.NumberColumn(
                            "Target Price",
                            format="USD %.2f",
                        ),
                        "Upside": st.column_config.NumberColumn(
                            "Upside",
                            format="%.1f%%",
                        ),
                        "Compañía": st.column_config.TextColumn(
                            "Compañía",
                            width="medium",
                        ),
                    },
                )

                history_export = close_prices.reset_index().rename(
                    columns={close_prices.index.name or "index": "Fecha"}
                )

                st.download_button(
                    "Descargar históricos seleccionados",
                    data=history_export.to_csv(index=False).encode("utf-8-sig"),
                    file_name=(
                        f"historicos_{'_'.join(selected_tickers)}_"
                        f"{period_labels[period_name]}.csv"
                    ),
                    mime="text/csv",
                )

    else:
        st.info("No quedan ADRs después de aplicar los filtros.")


with tab_fundamentals:
    st.subheader("Fundamentals & Earnings")
    st.caption(
        "Beta y P/E provienen de Alpha Vantage. El próximo earnings surge "
        "del calendario de Alpha Vantage. El resultado del último reporte "
        "compara EPS reportado contra EPS estimado."
    )

    fundamentals = load_fundamentals_cache()
    sector_pe = load_sector_pe()

    if fundamentals.empty:
        st.warning(
            "Todavía no hay datos en fundamentals_cache.csv. "
            "Ejecutá update_fundamentals.py o activá el workflow de GitHub Actions."
        )
    else:
        company_reference = targets[
            ["Ticker ADR", "Compañía", "Sector"]
        ].drop_duplicates("Ticker ADR")

        fundamentals_view = company_reference.merge(
            fundamentals,
            on="Ticker ADR",
            how="left",
        )

        fundamentals_view = fundamentals_view.merge(
            sector_pe[["Sector", "P/E sector", "Fecha"]],
            on="Sector",
            how="left",
        )

        fundamentals_view["P/E mostrado"] = fundamentals_view.apply(
            pe_status,
            axis=1,
        )

        fundamentals_view["Prima / descuento vs sector"] = None

        valid_pe = (
            fundamentals_view["P/E"].notna()
            & fundamentals_view["P/E sector"].notna()
            & (fundamentals_view["P/E"] > 0)
            & (fundamentals_view["P/E sector"] > 0)
        )

        fundamentals_view.loc[
            valid_pe,
            "Prima / descuento vs sector",
        ] = (
            fundamentals_view.loc[valid_pe, "P/E"]
            / fundamentals_view.loc[valid_pe, "P/E sector"]
            - 1
        ) * 100

        fundamentals_view["Resultado último reporte"] = (
            fundamentals_view["Sorpresa EPS %"]
            .apply(earnings_result_label)
            .apply(earnings_result_icon)
        )

        fundamentals_view["Próximo earnings mostrado"] = fundamentals_view.apply(
            lambda row: earnings_date_label(
                row.get("Próximo earnings"),
                row.get("Estado fecha earnings"),
            ),
            axis=1,
        )

        fundamentals_view = fundamentals_view.sort_values(
            ["Próximo earnings", "Ticker ADR"],
            na_position="last",
        )

        display_fundamentals = fundamentals_view[
            [
                "Ticker ADR",
                "Compañía",
                "Sector",
                "Beta",
                "P/E mostrado",
                "P/E sector",
                "Prima / descuento vs sector",
                "Próximo earnings mostrado",
                "Resultado último reporte",
                "Sorpresa EPS %",
                "Último reporte",
            ]
        ].copy()

        def color_fundamentals(value):
            text = str(value)

            if text.startswith("🟢"):
                return "color: #008f5a; font-weight: 800;"

            if text.startswith("🔴") or text.startswith("N/M"):
                return "color: #d62828; font-weight: 800;"

            if text.startswith("⚪"):
                return "color: #555; font-weight: 700;"

            return ""

        styled_fundamentals = (
            display_fundamentals.style
            .map(
                color_fundamentals,
                subset=["P/E mostrado", "Resultado último reporte"],
            )
            .format(
                {
                    "Beta": "{:.2f}",
                    "P/E sector": "{:.2f}",
                    "Prima / descuento vs sector": "{:+.1f}%",
                    "Sorpresa EPS %": "{:+.1f}%",
                },
                na_rep="—",
            )
        )

        st.dataframe(
            styled_fundamentals,
            width="stretch",
            height=560,
            hide_index=True,
            column_config={
                "Ticker ADR": st.column_config.TextColumn(
                    "ADR",
                    pinned=True,
                    width="small",
                ),
                "Compañía": st.column_config.TextColumn(
                    "Compañía",
                    width="medium",
                ),
                "Sector": st.column_config.TextColumn(
                    "Sector",
                    width="medium",
                ),
                "Beta": st.column_config.NumberColumn(
                    "Beta",
                    format="%.2f",
                    width="small",
                ),
                "P/E mostrado": st.column_config.TextColumn(
                    "P/E",
                    width="small",
                    help="N/M indica que el P/E no es significativo porque el EPS TTM es negativo.",
                ),
                "P/E sector": st.column_config.NumberColumn(
                    "P/E sector EE.UU.",
                    format="%.2f",
                    width="small",
                ),
                "Prima / descuento vs sector": st.column_config.NumberColumn(
                    "Prima / descuento",
                    format="%.1f%%",
                    width="small",
                ),
                "Próximo earnings mostrado": st.column_config.TextColumn(
                    "Próximo earnings",
                    width="medium",
                ),
                "Resultado último reporte": st.column_config.TextColumn(
                    "Último reporte vs esperado",
                    width="medium",
                ),
                "Sorpresa EPS %": st.column_config.NumberColumn(
                    "Sorpresa EPS",
                    format="%.1f%%",
                    width="small",
                ),
                "Último reporte": st.column_config.DateColumn(
                    "Fecha último reporte",
                    format="DD/MM/YYYY",
                    width="small",
                ),
            },
        )

        st.caption(
            "La comparación de último reporte usa únicamente la sorpresa de EPS. "
            "No incorpora sorpresa de ingresos porque el endpoint EARNINGS de "
            "Alpha Vantage no incluye revenue estimado y reportado."
        )

        st.divider()
        st.subheader("Detalle por ADR")

        selected_fundamental_ticker = st.selectbox(
            "Seleccionar ADR",
            options=fundamentals_view["Ticker ADR"].tolist(),
            key="fundamental_ticker",
        )

        detail = fundamentals_view.loc[
            fundamentals_view["Ticker ADR"] == selected_fundamental_ticker
        ].iloc[0]

        d1, d2, d3, d4 = st.columns(4)

        d1.metric(
            "Beta",
            f"{detail['Beta']:.2f}" if pd.notna(detail["Beta"]) else "—",
        )

        d2.metric(
            "P/E",
            pe_status(detail),
        )

        d3.metric(
            "P/E sector",
            f"{detail['P/E sector']:.2f}"
            if pd.notna(detail["P/E sector"])
            else "—",
        )

        premium_discount = detail["Prima / descuento vs sector"]

        d4.metric(
            "Prima / descuento",
            f"{premium_discount:+.1f}%"
            if pd.notna(premium_discount)
            else "N/A",
        )

        e1, e2, e3, e4 = st.columns(4)

        e1.metric(
            "Próximo earnings",
            earnings_date_label(
                detail.get("Próximo earnings"),
                detail.get("Estado fecha earnings"),
            ),
        )

        e2.metric(
            "EPS estimado último",
            f"{detail['EPS estimado último']:.2f}"
            if pd.notna(detail["EPS estimado último"])
            else "—",
        )

        e3.metric(
            "EPS reportado último",
            f"{detail['EPS reportado último']:.2f}"
            if pd.notna(detail["EPS reportado último"])
            else "—",
        )

        last_label = earnings_result_icon(
            earnings_result_label(detail.get("Sorpresa EPS %"))
        )

        e4.metric(
            "Resultado vs esperado",
            last_label,
            (
                f"{detail['Sorpresa EPS %']:+.1f}%"
                if pd.notna(detail["Sorpresa EPS %"])
                else None
            ),
        )

        export_fundamentals = fundamentals_view.to_csv(
            index=False
        ).encode("utf-8-sig")

        st.download_button(
            "Descargar fundamentals en CSV",
            data=export_fundamentals,
            file_name=f"fundamentals_adrs_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
        )


st.caption(
    f"Última actualización de la aplicación: {datetime.now():%d/%m/%Y %H:%M:%S}"
)
