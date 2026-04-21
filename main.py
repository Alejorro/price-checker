import os
from datetime import date, timedelta
from typing import Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
import psycopg2.extras
from db import cursor, init_db

app = FastAPI()


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def index():
    with open("frontend/index.html", "r") as f:
        return f.read()


def build_date_filter(months: str, date_override: Optional[str]) -> tuple[str, list]:
    if date_override:
        try:
            if len(date_override) == 7:
                year, month = date_override.split("-")
                from calendar import monthrange
                last_day = monthrange(int(year), int(month))[1]
                start = f"{date_override}-01"
                end = f"{date_override}-{last_day:02d}"
                return "order_date BETWEEN %s AND %s", [start, end]
            elif len(date_override) == 10:
                return "order_date = %s", [date_override]
            else:
                raise ValueError("formato inválido")
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="date debe ser YYYY-MM o YYYY-MM-DD")

    if months == "all":
        return "1=1", []

    try:
        n = int(months)
    except ValueError:
        n = 2
    since = date.today() - timedelta(days=n * 30)
    return "order_date >= %s", [since.isoformat()]


def period_label(months: str, date_override: Optional[str]) -> str:
    if date_override:
        return date_override
    if months == "all":
        return "todo el historial"
    n = int(months)
    return f"último mes" if n == 1 else f"últimos {n} meses"


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    months: str = Query("2"),
    date: Optional[str] = Query(None)
):
    q_clean = q.strip()

    with cursor() as (cur, _):
        # Find distinct product names matching query
        cur.execute("""
            SELECT DISTINCT product_name
            FROM purchase_lines
            WHERE product_name ILIKE %s
            ORDER BY product_name
        """, (f"%{q_clean}%",))
        names = [row["product_name"] for row in cur.fetchall()]

    if not names:
        return {"type": "empty", "query": q_clean}

    if len(names) > 1:
        return {
            "type": "variants",
            "query": q_clean,
            "variants": [{"product_name": n} for n in names]
        }

    product_name = names[0]
    date_sql, date_params = build_date_filter(months, date)

    with cursor() as (cur, _):
        cur.execute(f"""
            SELECT
                pl.supplier_name, pl.order_date, pl.price_usd, pl.order_name,
                ROUND((1.0 / (
                    SELECT cr.rate FROM currency_rates cr
                    WHERE cr.currency_name = 'USD' AND cr.rate_date <= pl.order_date
                    ORDER BY cr.rate_date DESC LIMIT 1
                ))::numeric, 2) AS rate_ars
            FROM purchase_lines pl
            WHERE pl.product_name = %s AND {date_sql}
            ORDER BY pl.order_date DESC
        """, [product_name] + date_params)
        rows = cur.fetchall()

    if not rows:
        return {"type": "empty", "query": q_clean, "product": product_name}

    rows = [r for r in rows if r["price_usd"] and float(r["price_usd"]) > 0]

    if not rows:
        return {"type": "empty", "query": q_clean, "product": product_name}

    all_prices = [float(r["price_usd"]) for r in rows]
    average_usd = round(sum(all_prices) / len(all_prices), 2)
    total_purchases = len(rows)

    last = rows[0]
    cheapest_row = min(rows, key=lambda r: float(r["price_usd"]))

    suppliers_map: dict[str, list] = {}
    for r in rows:
        sname = r["supplier_name"] or "Sin proveedor"
        if sname not in suppliers_map:
            suppliers_map[sname] = []
        suppliers_map[sname].append(r)

    suppliers = []
    for sname, srows in suppliers_map.items():
        prices = [float(r["price_usd"]) for r in srows]
        suppliers.append({
            "name": sname,
            "average_usd": round(sum(prices) / len(prices), 2),
            "count": len(srows),
            "purchases": [
                {
                    "date": r["order_date"].isoformat(),
                    "price_usd": float(r["price_usd"]),
                    "order_name": r["order_name"] or "",
                    "rate_ars": float(r["rate_ars"]) if r["rate_ars"] else None
                }
                for r in srows
            ]
        })

    suppliers.sort(key=lambda s: s["average_usd"])

    return {
        "type": "result",
        "product": product_name,
        "period": period_label(months, date),
        "average_usd": average_usd,
        "total_purchases": total_purchases,
        "last_purchase": {
            "price_usd": float(last["price_usd"]),
            "supplier": last["supplier_name"] or "Sin proveedor",
            "date": last["order_date"].isoformat(),
            "order_name": last["order_name"] or ""
        },
        "cheapest": {
            "price_usd": float(cheapest_row["price_usd"]),
            "supplier": cheapest_row["supplier_name"] or "Sin proveedor",
            "date": cheapest_row["order_date"].isoformat(),
            "order_name": cheapest_row["order_name"] or ""
        },
        "suppliers": suppliers
    }
