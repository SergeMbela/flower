import argparse
import random

import psycopg

from app.main import BASE_DAILY_ROTATION, SCENARIO_CONFIG
from app.vision_search import DATABASE_URL

INITIAL_STOCK: dict[str, int] = {
    "FAM-PL-001": 42,
    "FAM-PL-002": 18,
    "FAM-PL-003": 15,
    "FAM-PL-004": 9,
    "FAM-PL-005": 7,
    "FAM-PL-006": 26,
    "FAM-PL-007": 34,
    "FAM-PL-008": 11,
    "FAM-PL-009": 28,
    "FAM-PL-010": 19,
    "FAM-SO-010": 120,
    "FAM-SO-011": 55,
    "FAM-SO-012": 48,
    "FAM-HI-020": 65,
    "FAM-HI-021": 12,
    "FAM-DE-045": 22,
    "FAM-DE-046": 30,
    "FAM-DE-047": 16,
    "FAM-NO-088": 14,
    "FAM-NO-089": 10,
}


def print_status() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sku, name, stock_qty FROM products ORDER BY id")
            rows = cur.fetchall()
    print(f"{'SKU':<12} | {'STOCK':>5} | PRODUIT")
    print("-" * 65)
    for sku, name, stock_qty in rows:
        print(f"{sku:<12} | {stock_qty:>5} | {name}")


def add_stock(sku: str, qty: int) -> None:
    if qty <= 0:
        raise ValueError("La quantité à ajouter doit être positive.")
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            if sku.upper() == "ALL":
                cur.execute(
                    "UPDATE products SET stock_qty = stock_qty + %s RETURNING sku, stock_qty, price",
                    (qty,),
                )
            else:
                cur.execute(
                    "UPDATE products SET stock_qty = stock_qty + %s WHERE sku = %s RETURNING sku, stock_qty, price",
                    (qty, sku.upper()),
                )
            updated = cur.fetchall()
            for s, _, price in updated:
                cur.execute(
                    "INSERT INTO stock_movements (sku, movement_type, qty, unit_price, note) VALUES (%s, 'purchase', %s, %s, %s)",
                    (s, qty, round(float(price) * 0.55, 2), "Réapprovisionnement CLI"),
                )
        conn.commit()
    if not updated:
        print(f"Aucun produit trouvé pour SKU={sku}")
        return
    for s, new_qty, _ in updated:
        print(f"[+] {s} : nouveau stock = {new_qty}")


def sell_stock(sku: str, qty: int) -> None:
    if qty <= 0:
        raise ValueError("La quantité vendue doit être positive.")
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE products
                SET stock_qty = GREATEST(0, stock_qty - %s)
                WHERE sku = %s
                RETURNING sku, stock_qty, price
                """,
                (qty, sku.upper()),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO stock_movements (sku, movement_type, qty, unit_price, note) VALUES (%s, 'sale', %s, %s, %s)",
                    (row[0], qty, float(row[2]), "Vente enregistrée CLI"),
                )
        conn.commit()
    if not row:
        print(f"Aucun produit trouvé pour SKU={sku}")
        return
    print(f"[-] {row[0]} (-{qty} vendu) : nouveau stock = {row[1]}")


def simulate_sales(days: int, scenario: str) -> None:
    cfg = SCENARIO_CONFIG.get(scenario, SCENARIO_CONFIG["normal"])
    cat_mults = cfg["category_multipliers"]

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sku, name, category, stock_qty, price FROM products ORDER BY id")
            rows = cur.fetchall()

            print(f"Simulation de {days} jour(s) — scénario : {cfg['label']}")
            print(f"{'SKU':<12} | {'AVANT':>5} | {'VENDU':>5} | {'APRÈS':>5} | PRODUIT")
            print("-" * 75)

            for sku, name, category, stock_qty, price in rows:
                base_rate = BASE_DAILY_ROTATION.get(sku, 2.0)
                mult = cat_mults.get(category, 1.0)
                jitter = random.uniform(0.85, 1.15)
                sold = min(stock_qty, max(0, round(base_rate * mult * days * jitter)))
                new_qty = stock_qty - sold
                cur.execute(
                    "UPDATE products SET stock_qty = %s WHERE sku = %s",
                    (new_qty, sku),
                )
                if sold > 0:
                    cur.execute(
                        "INSERT INTO stock_movements (sku, movement_type, qty, unit_price, note) VALUES (%s, 'sale', %s, %s, %s)",
                        (sku, sold, float(price), f"Simulation {days}j ({scenario})"),
                    )
                print(f"{sku:<12} | {stock_qty:>5} | {sold:>5} | {new_qty:>5} | {name}")
        conn.commit()


def reset_stock() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for sku, qty in INITIAL_STOCK.items():
                cur.execute(
                    "UPDATE products SET stock_qty = %s WHERE sku = %s",
                    (qty, sku),
                )
        conn.commit()
    print("Stocks réinitialisés aux valeurs initiales.")
    print_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulateur de stock et de ventes Mes Plantes")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Afficher les stocks actuels")

    p_add = sub.add_parser("add", help="Ajouter du stock à un SKU (ou ALL)")
    p_add.add_argument("sku", help="Ex: FAM-PL-004 ou ALL")
    p_add.add_argument("qty", type=int, help="Quantité à ajouter")

    p_sell = sub.add_parser("sell", help="Enregistrer une vente sur un SKU")
    p_sell.add_argument("sku", help="Ex: FAM-HI-020")
    p_sell.add_argument("qty", type=int, help="Quantité vendue")

    p_sim = sub.add_parser("simulate", help="Simuler N jours de ventes selon un scénario")
    p_sim.add_argument("--days", type=int, default=2, help="Nombre de jours à simuler")
    p_sim.add_argument(
        "--scenario",
        choices=list(SCENARIO_CONFIG.keys()),
        default="normal",
        help="Scénario météo/saison",
    )

    sub.add_parser("reset", help="Réinitialiser les stocks initiaux")

    args = parser.parse_args()
    if args.cmd == "status":
        print_status()
    elif args.cmd == "add":
        add_stock(args.sku, args.qty)
    elif args.cmd == "sell":
        sell_stock(args.sku, args.qty)
    elif args.cmd == "simulate":
        simulate_sales(args.days, args.scenario)
    elif args.cmd == "reset":
        reset_stock()


if __name__ == "__main__":
    main()
