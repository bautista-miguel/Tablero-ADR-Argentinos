
# Tablero de ADRs argentinos — versión 6

Esta versión conserva el tablero de precios, price targets y gráficos de la versión 5, y agrega la pestaña **Fundamentals & Earnings**.

## Datos

- Precios e históricos: `yfinance`
- Beta, P/E, EPS y earnings: Alpha Vantage
- P/E sectorial: `sector_pe.csv`
- Price targets: `price_targets.csv`

## Instalar localmente

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Configurar Alpha Vantage en GitHub

1. Obtener una API key gratuita de Alpha Vantage.
2. En GitHub entrar al repositorio.
3. Ir a **Settings → Secrets and variables → Actions**.
4. Crear un secret llamado:

```text
ALPHA_VANTAGE_API_KEY
```

5. Pegar la API key como valor.
6. Ir a **Actions → Update Alpha Vantage fundamentals → Run workflow**.

El workflow también corre automáticamente de lunes a viernes.

## Límite gratuito

Alpha Vantage permite hasta 25 solicitudes diarias en el plan gratuito.

El actualizador usa:

- 15 solicitudes `OVERVIEW`
- 1 solicitud `EARNINGS_CALENDAR`
- 9 solicitudes `EARNINGS`

Los datos del último reporte rotan entre tickers: en aproximadamente dos ejecuciones quedan actualizados los 15 ADRs.

## P/E negativo

Cuando el EPS TTM es negativo, el tablero muestra:

```text
N/M — EPS negativo
```

No calcula prima o descuento frente al sector porque un P/E negativo no es comparable.

## P/E sectorial

`sector_pe.csv` es una referencia manual. Puede editarse directamente en GitHub.
