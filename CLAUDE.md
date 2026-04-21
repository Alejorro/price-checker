# Price Checker — CLAUDE.md

## Status

**LIVE** — app corriendo en `https://prices.dot4sa.com.ar`

- GitHub: `https://github.com/Alejorro/price-checker`
- Railway: proyecto DOT4, servicio `price-checker` + PostgreSQL
- Pushes a `main` → redeploy automático en Railway
- Sync diario: cron a las 3:00 AM UTC

---

## Archivos

| Archivo | Responsabilidad |
|---|---|
| `db.py` | Conexión a PostgreSQL, queries reutilizables, init de tablas |
| `sync_odoo.py` | Conecta a Odoo, trae datos, convierte precios, guarda en DB |
| `main.py` | FastAPI: endpoints `/` y `/search` |
| `frontend/index.html` | UI completa (HTML + CSS + JS puro, sin frameworks) |
| `railway.toml` | Config de deploy + cron diario en Railway |
| `Dockerfile` | Build para Railway |
| `requirements.txt` | Dependencias Python |
| `.env` | Credenciales locales — NO commitear, está en `.gitignore` |
| `.env.example` | Template sin valores reales — este sí va a git |

---

## Variables de entorno

```
ODOO_URL=https://odoo.dot4sa.com
ODOO_DB=dot4-prod
ODOO_USER=alejo.palladino@dot4sa.com
ODOO_PASSWORD=...
DATABASE_URL=postgresql://...
```

**Importante:** El `DATABASE_URL` para correr local debe ser la URL pública de Railway (TCP Proxy), no la interna (`postgres.railway.internal` solo funciona dentro de Railway).

---

## Cómo correr local

```bash
# 1. Crear y activar entorno virtual (solo la primera vez)
python3.12 -m venv venv
source venv/bin/activate

# 2. Instalar dependencias (solo la primera vez)
pip install -r requirements.txt

# 3. Configurar credenciales
cp .env.example .env
# Editar .env con los valores reales

# 4. Cargar variables y correr sync
set -a && source .env && set +a
python sync_odoo.py

# 5. Levantar servidor
uvicorn main:app --reload

# Abrir http://localhost:8000
```

**Nota:** Cada vez que abrís una terminal nueva, hay que activar el venv y cargar el .env:
```bash
source venv/bin/activate
set -a && source .env && set +a
```

---

## Deploy

### Flujo normal
```
git add . && git commit -m "..." && git push
```
Railway detecta el push y redeploya automáticamente.

### Dockerfile
Usa shell form en CMD para que `$PORT` se expanda correctamente:
```dockerfile
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```
**No agregar `startCommand` en `railway.toml`** — pisaría el CMD del Dockerfile y `$PORT` no se expande.

### DNS (Ferozo)
| Tipo | Nombre | Valor |
|---|---|---|
| CNAME | `prices` | `su4gfqw7.up.railway.app` |
| TXT | `_railway-verify.prices.dot4sa.com.ar` | valor de Railway |

Ferozo no acepta `_railway-verify.prices` como nombre — hay que poner el FQDN completo `_railway-verify.prices.dot4sa.com.ar`.

---

## Lógica de conversión de monedas

Las tasas en Odoo están en formato inverso: `1 ARS = rate USD`, por lo tanto `1 USD = 1/rate ARS`.

`USD` = dólar oficial (Banco Central). `US$` = dólar blue/MEP.

### USD → USD
Sin conversión. `price_usd = price_unit`.

### PES (ARS) → USD oficial
```
price_usd = price_unit * rate_usd
```

### US$ (blue/MEP) → USD oficial
1. Convertir US$ a ARS: `ars = price_unit / rate_usdd`
2. Convertir ARS a USD oficial: `price_usd = ars * rate_usd`

---

## Fallback de cotización

Cuando no existe cotización exacta para la fecha de la orden, se busca la más cercana anterior:

```sql
SELECT rate FROM currency_rates
WHERE currency_name = 'USD' AND rate_date <= :order_date
ORDER BY rate_date DESC
LIMIT 1
```

Nunca se usa una cotización futura.

---

## Sync incremental

1. Al arrancar, lee `last_sync_date` de la tabla `sync_state`.
2. Si es `NULL` (primer sync), trae todo desde `2025-01-06`.
3. Si tiene valor, filtra `purchase.order.line` por `order_id.date_approve >= last_sync_date`.
4. Si el sync termina exitosamente, actualiza `last_sync_date`. Si falla, no la actualiza.
5. El cron en Railway corre el sync cada día a las 3:00 AM UTC.

### Upsert
Se usa `odoo_line_id` como clave única. Todo en batch con `execute_values` para velocidad.

### Performance (datos reales)
- Cotizaciones: ~396 registros → segundos
- Líneas: ~983 líneas → ~30 segundos

---

## Comportamiento del endpoint /search

- `q` → búsqueda `ILIKE %q%` sobre `product_name` (case insensitive, parcial)
- Si hay múltiples productos distintos → devuelve `type: "variants"` para elegir
- Si hay un solo match → devuelve `type: "result"`
- Líneas con `price_usd = 0` o nulo son ignoradas
- `months` inválido → fallback a 2 meses
- `date` con formato inválido → HTTP 400
- La búsqueda es por string literal — `notebook v15` no matchea `NOTEBOOK LENOVO V15` porque tiene `LENOVO` en el medio. Buscar por modelo (`V15 G5`) o código (`83GW004QAC`) funciona mejor.

---

## Notas y decisiones técnicas

- `product_name` se limpia eliminando ` (copia)` antes de guardar.
- El frontend usa `data-name` en lugar de `onclick` con JSON — los nombres de productos tienen caracteres especiales que romperían el HTML.
- Las variantes se manejan con `addEventListener` después de renderizar el HTML.
- `supplier_name` y `order_name` nulos → fallback a string vacío/"Sin proveedor".
- El `.env` tiene el `ODOO_PASSWORD` entre comillas porque contiene `<` que rompe el shell.
- El tipo de cambio (ARS/USD) se muestra en el desglose por proveedor — se calcula con `1/rate` del fallback más cercano anterior.
- Diseño visual alineado con DOT4 Forecast — mismos colores, tipografía, cards y tabla de desglose.
- Logo: `https://forecast.dot4sa.com.ar/dot4-logo.png` y favicon: `https://forecast.dot4sa.com.ar/favicon.png`
