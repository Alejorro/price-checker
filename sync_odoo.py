import os
import xmlrpc.client
import psycopg2.extras
from datetime import datetime, date, timezone
from db import cursor, init_db, get_rate

ODOO_URL = os.environ["ODOO_URL"]
ODOO_DB = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]

FIRST_SYNC_DATE = "2025-01-06"


def odoo_connect():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    if not uid:
        raise RuntimeError("Odoo authentication failed — verificá ODOO_USER y ODOO_PASSWORD")
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return uid, models


def clean_name(name: str) -> str:
    return name.replace(" (copia)", "").strip() if name else ""


def convert_to_usd(price: float, currency: str, order_date, cur) -> float | None:
    if currency == "USD":
        return price

    rate_usd = get_rate(cur, "USD", order_date)
    if rate_usd is None:
        return None

    if currency == "PES":
        return price * rate_usd

    if currency == "US$":
        rate_usdd = get_rate(cur, "US$", order_date)
        if rate_usdd is None:
            return None
        ars = price / rate_usdd
        return ars * rate_usd

    return None


def sync_currency_rates(uid, models):
    print("Syncing currency rates... (llamando a Odoo, puede tardar)")
    rate_records = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        "res.currency.rate", "search_read",
        [[]],
        {"fields": ["name", "rate", "currency_id"], "limit": 10000}
    )
    print(f"  Recibidos {len(rate_records)} registros de cotizaciones")

    seen = {}
    for r in rate_records:
        rate_date = r["name"]
        currency_name = r["currency_id"][1] if r["currency_id"] else None
        rate_val = r["rate"]
        if not currency_name or not rate_val:
            continue
        seen[(rate_date, currency_name)] = rate_val

    rows = [(d, c, v) for (d, c), v in seen.items()]

    if rows:
        with cursor() as (cur, _):
            psycopg2.extras.execute_values(cur, """
                INSERT INTO currency_rates (rate_date, currency_name, rate)
                VALUES %s
                ON CONFLICT (rate_date, currency_name) DO UPDATE SET rate = EXCLUDED.rate
            """, rows)

    print(f"  Currency rates: {len(rows)} upserted")


def get_last_sync_date() -> str | None:
    with cursor() as (cur, _):
        cur.execute("SELECT last_sync_date FROM sync_state ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row and row["last_sync_date"]:
            return row["last_sync_date"].strftime("%Y-%m-%d %H:%M:%S")
    return None


def set_last_sync_date(dt: datetime):
    with cursor() as (cur, _):
        cur.execute("UPDATE sync_state SET last_sync_date = %s", (dt,))


def sync_purchase_lines(uid, models, since: str):
    print(f"Syncing purchase lines since {since}...")

    domain = [
        ["order_id.date_approve", ">=", since],
        ["order_id.state", "in", ["purchase", "done"]]
    ]

    print("  Buscando IDs de líneas en Odoo...")
    line_ids = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        "purchase.order.line", "search",
        [domain],
        {"limit": 50000}
    )

    print(f"  Found {len(line_ids)} lines")

    if not line_ids:
        return

    print("  Descargando datos de líneas...")
    lines = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        "purchase.order.line", "read",
        [line_ids],
        {"fields": [
            "id", "order_id", "product_id", "product_qty",
            "price_unit", "currency_id"
        ]}
    )

    order_ids = list({l["order_id"][0] for l in lines if l["order_id"]})
    print(f"  Descargando datos de {len(order_ids)} órdenes...")
    orders_data = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        "purchase.order", "read",
        [order_ids],
        {"fields": ["id", "name", "date_approve", "partner_id"]}
    )
    orders = {o["id"]: o for o in orders_data}

    print("  Calculando precios USD...")
    rows = []
    no_rate = 0

    with cursor() as (cur, _):
        for i, line in enumerate(lines):
            if (i + 1) % 200 == 0:
                print(f"  ... {i + 1}/{len(lines)} calculadas")

            order_id = line["order_id"][0] if line["order_id"] else None
            order = orders.get(order_id, {})
            order_name = order.get("name", "")
            order_date_raw = order.get("date_approve")
            supplier_name = order["partner_id"][1] if order.get("partner_id") else ""

            if not order_date_raw:
                continue
            order_date = order_date_raw[:10]

            product_name = clean_name(line["product_id"][1] if line["product_id"] else "")
            if not product_name:
                continue
            currency = line["currency_id"][1] if line["currency_id"] else "USD"
            price_original = float(line["price_unit"] or 0)
            quantity = float(line["product_qty"] or 0)
            odoo_line_id = line["id"]

            price_usd = convert_to_usd(price_original, currency, order_date, cur)
            if price_usd is None:
                no_rate += 1
                print(f"  WARNING: No rate currency={currency} date={order_date} line={odoo_line_id}")
                continue

            rows.append((odoo_line_id, order_name, order_date, product_name,
                         supplier_name, quantity, price_original, currency, price_usd))

    print(f"  Guardando {len(rows)} líneas en la base de datos...")
    with cursor() as (cur, _):
        psycopg2.extras.execute_values(cur, """
            INSERT INTO purchase_lines
                (odoo_line_id, order_name, order_date, product_name,
                 supplier_name, quantity, price_original, currency, price_usd)
            VALUES %s
            ON CONFLICT (odoo_line_id) DO UPDATE SET
                order_name = EXCLUDED.order_name,
                order_date = EXCLUDED.order_date,
                product_name = EXCLUDED.product_name,
                supplier_name = EXCLUDED.supplier_name,
                quantity = EXCLUDED.quantity,
                price_original = EXCLUDED.price_original,
                currency = EXCLUDED.currency,
                price_usd = EXCLUDED.price_usd,
                synced_at = NOW()
        """, rows)

    print(f"  Processed: {len(lines)} | Saved: {len(rows)} | No rate: {no_rate}")


def main():
    print("=== Price Checker Sync ===")
    init_db()

    uid, models = odoo_connect()
    print(f"Connected to Odoo as uid={uid}")

    sync_currency_rates(uid, models)

    last_sync = get_last_sync_date()
    since = last_sync if last_sync else FIRST_SYNC_DATE
    print(f"Sync mode: {'incremental' if last_sync else 'full'}")

    try:
        sync_purchase_lines(uid, models, since)
        set_last_sync_date(datetime.now(timezone.utc))
        print("Sync complete.")
    except Exception as e:
        print(f"ERROR durante sync: {e}")
        raise


if __name__ == "__main__":
    main()
