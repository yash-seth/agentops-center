"""Create the synthetic inventory database used by the inventory_sql tool."""

import random
import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("inventory.db")
SKUS = {
    "SKU-1001": "Masala Chips 50g",
    "SKU-1002": "Salted Chips 50g",
    "SKU-1003": "Cola 300ml",
    "SKU-1004": "Orange Drink 300ml",
    "SKU-1005": "Oat Bar 30g",
}
DCS = ["Hyderabad", "Mumbai", "Delhi"]


def seed(path: Path = DB) -> Path:
    path.unlink(missing_ok=True)
    rnd = random.Random(42)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE inventory "
        "(sku TEXT, name TEXT, dc TEXT, on_hand INT, daily_demand INT)"
    )
    for sku, name in SKUS.items():
        for dc in DCS:
            con.execute(
                "INSERT INTO inventory VALUES (?,?,?,?,?)",
                (sku, name, dc, rnd.randint(200, 5000), rnd.randint(80, 600)),
            )
    con.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY, priority TEXT, summary TEXT)")
    con.commit()
    con.close()
    return path


if __name__ == "__main__":
    print(seed())
