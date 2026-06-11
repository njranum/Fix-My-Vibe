"""Order Tracker API — main application.

FIXTURE NOTE: this file intentionally contains planted vulnerabilities
for testing the Fix My Vibe security scanner. Do not fix them.
"""

import sqlite3
import requests
from fastapi import FastAPI

app = FastAPI()

# Planted: hardcoded API key (known sk- format)
OPENAI_API_KEY = "sk-FAKEFIXTURE9a8b7c6d5e4f3a2b1c0d9e8f7a6b"

# Planted: generic secret assignment
db_password = "hunter2hunter2hunter2"


@app.get("/orders/{customer_name}")
def get_orders(customer_name: str):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    # Planted: SQL built with an f-string
    cursor.execute(f"SELECT * FROM orders WHERE customer = '{customer_name}'")
    rows = cursor.fetchall()
    conn.close()
    return {"orders": rows}


@app.get("/status")
def upstream_status():
    # Planted: TLS verification disabled
    resp = requests.get("https://status.internal.example.com", verify=False)
    return {"upstream": resp.status_code}


if __name__ == "__main__":
    import uvicorn
    # Planted: debug mode enabled
    uvicorn.run(app, host="0.0.0.0", port=8000, debug=True)
