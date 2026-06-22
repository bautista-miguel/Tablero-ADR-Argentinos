
# Tablero ADRs Argentinos — versión 7

Esta versión conserva el diseño y las funciones de la versión 6, pero optimiza las consultas a Alpha Vantage.

## Lógica de actualización

El workflow corre de lunes a viernes. El script decide qué consultar:

- **Precios:** `yfinance`, al abrir la app.
- **P/E:** se calcula en vivo como `precio actual / EPS TTM`.
- **Calendario de earnings:** máximo una vez cada 7 días, con una sola llamada global.
- **Overview:** máximo un ticker por día, solo si falta información o pasaron 35 días.
- **Earnings:** solo para tickers sin datos, con información vieja o cerca de su fecha de reporte.
- **EPS TTM:** se recalcula sumando los últimos cuatro EPS trimestrales cuando se actualiza `EARNINGS`.
- **Límite interno:** máximo 10 consultas por ejecución, aunque el plan permita más.

## Archivos que hay que subir al repositorio

```text
app.py
price_targets.csv
requirements.txt
README.md
sector_pe.csv
fundamentals_cache.csv
update_metadata.json
update_fundamentals.py
.github/workflows/update_fundamentals.yml
```

## GitHub secret

Debe existir este secret:

```text
ALPHA_VANTAGE_API_KEY
```

Ruta:

```text
Settings → Secrets and variables → Actions
```

## Primera carga

Los datos pueden tardar varios días hábiles en completarse porque el sistema distribuye las consultas deliberadamente.

Para acelerar solo la carga inicial, se puede ejecutar el workflow manualmente una vez por día. No conviene ejecutarlo repetidamente el mismo día porque Alpha Vantage limita el uso gratuito.

## P/E negativo

Cuando `EPS TTM <= 0`, se muestra:

```text
N/M — EPS negativo
```

No se calcula prima o descuento frente al sector.
