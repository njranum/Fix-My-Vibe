"""Tests for the Order Tracker API (fixture — minimal on purpose)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_status_route_exists():
    assert any(r.path == "/status" for r in app.routes)
