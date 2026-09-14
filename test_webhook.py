"""
Tests for the WhatsApp webhook verification + ack. Uses FastAPI's in-process
TestClient (no live server, no external calls).
Run:  python -m pytest test_webhook.py -v
"""

import os

from fastapi.testclient import TestClient

import webhook

client = TestClient(webhook.app)


def test_verification_accepts_correct_token():
    token = os.getenv("WHATSAPP_VERIFY_TOKEN")
    r = client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": token,
        "hub.challenge": "challenge-123",
    })
    assert r.status_code == 200
    assert r.text == "challenge-123"  # must echo the challenge back verbatim


def test_verification_rejects_wrong_token():
    r = client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "definitely-wrong",
        "hub.challenge": "challenge-123",
    })
    assert r.status_code == 403


def test_post_acks_quickly():
    # Empty messages list -> nothing to process, but must still 200 fast.
    r = client.post("/webhook", json={"entry": [{"changes": [{"value": {"messages": []}}]}]})
    assert r.status_code == 200
    assert r.json() == {"status": "received"}
