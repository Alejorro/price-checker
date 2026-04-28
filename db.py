import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

DATABASE_URL = os.environ["DATABASE_URL"]


def get_conn():
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def cursor():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur, conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with cursor() as (cur, conn):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS purchase_lines (
                id SERIAL PRIMARY KEY,
                odoo_line_id INTEGER UNIQUE NOT NULL,
                order_name VARCHAR(50),
                order_date DATE,
                product_name TEXT,
                supplier_name TEXT,
                quantity NUMERIC,
                price_original NUMERIC,
                currency VARCHAR(10),
                price_usd NUMERIC,
                product_category TEXT,
                synced_at TIMESTAMP DEFAULT NOW()
            );

            ALTER TABLE purchase_lines ADD COLUMN IF NOT EXISTS product_category TEXT;

            CREATE TABLE IF NOT EXISTS currency_rates (
                id SERIAL PRIMARY KEY,
                rate_date DATE NOT NULL,
                currency_name VARCHAR(20) NOT NULL,
                rate NUMERIC NOT NULL,
                UNIQUE(rate_date, currency_name)
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                id SERIAL PRIMARY KEY,
                last_sync_date TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_product_name
                ON purchase_lines USING gin(to_tsvector('simple', product_name));
            CREATE INDEX IF NOT EXISTS idx_order_date
                ON purchase_lines(order_date);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_log (
                id SERIAL PRIMARY KEY,
                query TEXT NOT NULL,
                searched_at TIMESTAMP DEFAULT NOW()
            );
        """)

        cur.execute("SELECT COUNT(*) FROM sync_state")
        row = cur.fetchone()
        if row["count"] == 0:
            cur.execute("INSERT INTO sync_state (last_sync_date) VALUES (NULL)")


def get_rate(cur, currency_name: str, order_date) -> float | None:
    cur.execute("""
        SELECT rate FROM currency_rates
        WHERE currency_name = %s AND rate_date <= %s
        ORDER BY rate_date DESC
        LIMIT 1
    """, (currency_name, order_date))
    row = cur.fetchone()
    return float(row["rate"]) if row else None
