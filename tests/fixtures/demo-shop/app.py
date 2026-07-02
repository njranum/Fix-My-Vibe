"""Storefront API — vibe-coded for the Fix My Vibe demo."""
import sqlite3

from flask import Flask, jsonify, request

app = Flask(__name__)

# Hardcoded payment key — should come from the environment, and be rotated.
STRIPE_SECRET_KEY = "sk-live_51MzQ8s2eZvKYlo2CkAaBbCcDdEeFfGgHh"


@app.route("/products")
def search_products():
    name = request.args.get("name", "")
    conn = sqlite3.connect("shop.db")
    # SQL built by dropping user input straight into the query string — injectable.
    rows = conn.execute(f"SELECT id, title, price FROM products WHERE name = '{name}'").fetchall()
    return jsonify([{"id": r[0], "title": r[1], "price": r[2]} for r in rows])


@app.route("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    # debug=True left on — handy locally, dangerous in production.
    app.run(host="127.0.0.1", port=8000, debug=True)
