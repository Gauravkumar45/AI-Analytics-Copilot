"""Create the deterministic SQLite demo database (demo/sales.db).

Fictional retail data for 2024-2025: customers, products, orders and order
lines. A fixed random seed keeps eval answers stable across rebuilds.

    python scripts/seed_demo_db.py
"""
from __future__ import annotations

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "demo" / "sales.db"

CITIES = [("Gurugram", "Haryana"), ("Delhi", "Delhi"), ("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"),
          ("Bengaluru", "Karnataka"), ("Hyderabad", "Telangana"), ("Chennai", "Tamil Nadu"),
          ("Kolkata", "West Bengal"), ("Jaipur", "Rajasthan"), ("Ahmedabad", "Gujarat")]
SEGMENTS = ["Consumer", "Small Business", "Enterprise"]
CATEGORIES = {
    "Electronics": [("Wireless Earbuds", 2499), ("Smartwatch", 6999), ("Bluetooth Speaker", 3499),
                    ("4K Monitor", 24999), ("Mechanical Keyboard", 5499), ("Laptop Stand", 1899)],
    "Home": [("Air Fryer", 7499), ("Coffee Maker", 4999), ("LED Desk Lamp", 1299), ("Water Purifier", 11999)],
    "Fashion": [("Running Shoes", 3999), ("Denim Jacket", 2999), ("Backpack", 1799), ("Sunglasses", 1499)],
    "Books": [("Python Handbook", 899), ("Data Science Guide", 1199), ("System Design Notes", 999)],
    "Sports": [("Yoga Mat", 999), ("Dumbbell Set", 3299), ("Cycling Helmet", 2199)],
}
STATUSES = ["delivered"] * 80 + ["shipped"] * 8 + ["cancelled"] * 7 + ["returned"] * 5
CHANNELS = ["web"] * 50 + ["mobile_app"] * 40 + ["marketplace"] * 10
FIRST = ["Aarav", "Vivaan", "Aditya", "Ishaan", "Kabir", "Ananya", "Diya", "Meera", "Saanvi", "Asha",
         "Rohan", "Arjun", "Neha", "Priya", "Karan", "Riya", "Sara", "Dev", "Tara", "Nikhil"]
LAST = ["Sharma", "Verma", "Gupta", "Mehta", "Iyer", "Reddy", "Nair", "Singh", "Kapoor", "Das"]


def build(path: Path = OUT, seed: int = 42) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, city TEXT, state TEXT,
                            segment TEXT, signup_date TEXT);
    CREATE TABLE products (product_id INTEGER PRIMARY KEY, name TEXT, category TEXT, unit_price REAL);
    CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id),
                         order_date TEXT, status TEXT, channel TEXT);
    CREATE TABLE order_items (item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id),
                              product_id INTEGER REFERENCES products(product_id), quantity INTEGER,
                              unit_price REAL, discount REAL);
    """)

    customers = []
    for cid in range(1, 301):
        city, state = rng.choice(CITIES)
        signup = date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))
        customers.append((cid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", city, state,
                          rng.choices(SEGMENTS, weights=[70, 22, 8])[0], signup.isoformat()))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)

    products, pid = [], 0
    for category, items in CATEGORIES.items():
        for name, price in items:
            pid += 1
            products.append((pid, name, category, float(price)))
    conn.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    orders, items, item_id = [], [], 0
    start = date(2024, 1, 1)
    for oid in range(1, 3001):
        day = start + timedelta(days=rng.randint(0, 730))
        # Mild seasonality: more orders in Oct-Dec (festive season).
        if day.month in (10, 11, 12) and rng.random() < 0.3:
            day = day.replace(day=min(day.day, 28))
        cust = rng.choice(customers)
        orders.append((oid, cust[0], day.isoformat(), rng.choice(STATUSES), rng.choice(CHANNELS)))
        for _ in range(rng.choices([1, 2, 3, 4], weights=[55, 28, 12, 5])[0]):
            item_id += 1
            prod = rng.choice(products)
            discount = rng.choice([0, 0, 0, 0.05, 0.1, 0.15, 0.2])
            items.append((item_id, oid, prod[0], rng.choices([1, 2, 3], weights=[75, 20, 5])[0], prod[3], discount))
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    conn.commit()
    conn.close()
    return path


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(f"Wrote {out}")
