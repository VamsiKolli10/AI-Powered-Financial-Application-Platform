"""End-to-end HTTP tests for the Transactions service."""

from libs.db.models import TransactionStatus


async def test_health_and_ready_endpoints(client):
    assert (await client.get("/health")).status_code == 200
    # /ready checks the real engine, which is not configured in tests.
    assert (await client.get("/ready")).status_code in (200, 503)


async def test_post_returns_202_with_pending_status(client, tx_payload):
    response = await client.post("/transactions", json=tx_payload)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == TransactionStatus.PENDING_CATEGORIZATION.value
    assert body["id"].startswith("tx_")
    assert body["amount"] == "-42.50"


async def test_replay_returns_200_and_same_id(client, tx_payload):
    first = await client.post("/transactions", json=tx_payload)
    second = await client.post("/transactions", json=tx_payload)

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


async def test_get_transaction_shows_display_category(client, tx_payload):
    created = (await client.post("/transactions", json=tx_payload)).json()
    # BackgroundTasks run once the response is delivered, so the row is categorized by now.
    response = await client.get(f"/transactions/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "Dining & Coffee"
    assert body["status"] == "categorized"


async def test_list_is_paginated(client, tx_payload):
    for i in range(4):
        await client.post("/transactions", json={**tx_payload, "external_tx_id": f"tx_{i}"})

    response = await client.get("/transactions", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"]


async def test_patch_category(client, tx_payload):
    created = (await client.post("/transactions", json=tx_payload)).json()

    response = await client.patch(f"/transactions/{created['id']}", json={"category": "Groceries"})

    assert response.status_code == 200
    assert response.json()["category"] == "Groceries"
    assert response.json()["category_source"] == "user"


async def test_patch_rejects_unknown_category(client, tx_payload):
    created = (await client.post("/transactions", json=tx_payload)).json()
    response = await client.patch(f"/transactions/{created['id']}", json={"category": "Yachts"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["request_id"]


async def test_zero_amount_is_rejected(client, tx_payload):
    response = await client.post("/transactions", json={**tx_payload, "amount": 0})
    assert response.status_code == 422


async def test_missing_transaction_returns_404_envelope(client):
    response = await client.get("/transactions/tx_does_not_exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_unauthenticated_request_is_rejected(client, tx_payload):
    response = await client.post(
        "/transactions",
        json=tx_payload,
        headers={"X-Internal-Token": "", "X-User-Id": ""},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_request_id_header_is_echoed(client):
    response = await client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
