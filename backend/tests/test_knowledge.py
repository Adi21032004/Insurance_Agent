from fastapi.testclient import TestClient

from backend import database


def start_review(client: TestClient, claim_id: str) -> None:
    database.set_claim_status(claim_id, "in progress")


def test_search_returns_relevant_manual_section_excerpt(client: TestClient):
    start_review(client, "CLM-1001")

    response = client.post(
        "/api/agent/knowledge/search",
        json={
            "claim_id": "CLM-1001",
            "source": "claims_manual",
            "query": "third claim within 12 months",
        },
    )

    matches = response.json()["results"]
    repeat_claims = next(
        match for match in matches if match["section_id"] == "claims_manual:09"
    )
    assert response.status_code == 200
    assert repeat_claims["heading"] == "Repeat claims"
    assert repeat_claims["version"] == "2026.2"
    assert "third claim" in repeat_claims["excerpt"].casefold()
    assert "content" not in repeat_claims
    assert len(repeat_claims["excerpt"]) <= 280


def test_read_converts_manual_amounts_and_records_source_on_claim(
    client: TestClient,
):
    start_review(client, "CLM-1001")

    response = client.post(
        "/api/agent/knowledge/read",
        json={
            "claim_id": "CLM-1001",
            "source": "claims_manual",
            "section_id": "claims_manual:07",
        },
    )
    claim = client.get("/api/claims/CLM-1001").json()

    assert response.status_code == 200
    assert response.json()["version"] == "2026.2"
    assert response.json()["heading"] == "Interpretation notes"
    assert "₹29,050.00" in response.json()["content"]
    assert "$" not in response.json()["content"]
    assert claim["source_reads"][0]["source"] == "claims_manual"
    assert claim["source_reads"][0]["section_id"] == "claims_manual:07"
    assert claim["source_reads"][0]["content"] == response.json()["content"]


def test_search_and_read_policy_section_use_wording_version(client: TestClient):
    start_review(client, "CLM-1001")
    query = client.post(
        "/api/agent/knowledge/search",
        json={
            "claim_id": "CLM-1001",
            "source": "policy_wording",
            "query": "Trip Delay waiting period",
        },
    )
    read = client.post(
        "/api/agent/knowledge/read",
        json={
            "claim_id": "CLM-1001",
            "source": "policy_wording",
            "section_id": "policy_wording:03",
        },
    )
    plan_section = client.post(
        "/api/agent/knowledge/read",
        json={
            "claim_id": "CLM-1001",
            "source": "policy_wording",
            "section_id": "policy_wording:01",
        },
    )

    assert query.status_code == 200
    assert any(
        match["section_id"] == "policy_wording:03" for match in query.json()["results"]
    )
    assert read.status_code == 200
    assert read.json()["version"] == "2026.1"
    assert read.json()["heading"] == "Trip Delay"
    assert "$" not in read.json()["content"]
    assert plan_section.status_code == 200
    assert "₹24,900.00" in plan_section.json()["content"]
    assert "$" not in plan_section.json()["content"]


def test_unknown_source_section_and_claim_return_clear_errors(client: TestClient):
    start_review(client, "CLM-1001")
    unknown_section = client.post(
        "/api/agent/knowledge/read",
        json={
            "claim_id": "CLM-1001",
            "source": "claims_manual",
            "section_id": "claims_manual:99",
        },
    )
    unknown_claim = client.post(
        "/api/agent/knowledge/search",
        json={
            "claim_id": "CLM-9999",
            "source": "claims_manual",
            "query": "third claim",
        },
    )
    waiting_claim = client.post(
        "/api/agent/knowledge/search",
        json={
            "claim_id": "CLM-1002",
            "source": "policy_wording",
            "query": "baggage",
        },
    )

    assert unknown_section.status_code == 404
    assert unknown_claim.status_code == 404
    assert waiting_claim.status_code == 409
