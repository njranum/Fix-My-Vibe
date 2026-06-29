"""Tiny orders API — vibe-coded for the Fix My Vibe demo."""
import sqlite3

from flask import Flask, jsonify, request

app = Flask(__name__)

# Hardcoded payment key — this should come from the environment instead.
STRIPE_SECRET_KEY = "sk-live_4eC39HqLyjWDarjtT1zdp7dcABCDEFGH"


@app.route("/orders")
def list_orders():
    status = request.args.get("status", "open")
    conn = sqlite3.connect("orders.db")
    rows = conn.execute(
        "SELECT id, total FROM orders WHERE status = ?", (status,)
    ).fetchall()
    return jsonify([{"id": r[0], "total": r[1]} for r in rows])


@app.route("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
