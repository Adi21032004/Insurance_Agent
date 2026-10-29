from copy import deepcopy
from typing import Any

from fastapi.testclient import TestClient

from backend import database
from backend.payout import LinePayout, calculate_lines


def line(
    kind: str,
    amount: float,
    **details: Any,
) -> dict[str, Any]:
    return {
        "description": "Claim line",
        "quantity": 1,
        "nights": 0,
        "hours": 0,
        **details,
        "amount": amount,
        "kind": kind,
    }


def payout(
    policy: dict[str, Any],
    plan_settings: dict[str, Any],
    benefit: str,
    lines: list[dict[str, Any]],
    **options: float,
) -> dict[str, Any]:
    return calculate_lines(
        LinePayout(
            policy=policy,
            plan_settings=plan_settings,
            approval_limit_usd=options.get("limit", 1000),
            benefit=benefit,
            lines=lines,
            paid_by_others=options.get("others", 0),
        )
    )


def test_calculate_payout_operation_uses_usd_rules_and_does_not_write(
    client: TestClient,
):
    response = client.post(
        "/api/claims/CLM-1001/payout",
        json={
            "benefit": "trip_delay",
            "lines": [
                line("hotel", 245, description="Hotel", nights=1),
                line("expense", 95, description="Meals"),
                line("expense", 80, description="Taxi"),
            ],
            "paid_by_others": 0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "calculation": [
            {"step": "Hotel", "amount": 245.0},
            {"step": "Meals", "amount": 95.0},
            {"step": "Taxi", "amount": 80.0},
        ],
        "payout": 420.0,
        "needs_sign_off": False,
    }
    record = client.get("/api/claims/CLM-1001").json()
    assert record["status"] == "waiting"
    assert record["decision"] is None


def test_line_rules_apply_item_electronics_and_cash_limits(client: TestClient):
    record = database.get_claim_record("CLM-1001")
    assert record is not None
    policy = record["policy"]
    settings = database.get_setting("plans")

    item = payout(policy, settings, "baggage", [line("item", 700, quantity=2)])
    electronics_policy = deepcopy(policy)
    electronics_policy["add_ons"] = ["electronics_plus"]
    electronics = payout(
        electronics_policy,
        settings,
        "baggage",
        [line("electronics", 1500, description="Laptop")],
    )
    cash = payout(
        policy,
        settings,
        "baggage",
        [line("cash", 150, description="Wallet"), line("cash", 50)],
    )

    assert item["payout"] == 550.0
    assert item["calculation"][0] == {
        "step": "Claim line, limited to $600.00",
        "amount": 600.0,
    }
    assert electronics["payout"] == 1150.0
    assert electronics["calculation"][0]["amount"] == 1200.0
    assert cash["payout"] == 50.0
    assert cash["calculation"][0]["amount"] == 100.0


def test_line_rules_apply_hotel_meal_and_expense_limits(client: TestClient):
    record = database.get_claim_record("CLM-1001")
    assert record is not None
    policy = record["policy"]
    settings = database.get_setting("plans")

    hotel = payout(
        policy,
        settings,
        "trip_delay",
        [line("hotel", 1000, nights=2)],
    )
    meals = payout(
        policy,
        settings,
        "trip_delay",
        [line("meals_without_receipts", 200, hours=25)],
    )
    expense = payout(
        policy,
        settings,
        "emergency_medical",
        [line("expense", 3800)],
    )

    assert hotel["payout"] == 700.0
    assert hotel["calculation"][0]["step"] == "Claim line, limited to $700.00"
    assert meals["payout"] == 70.0
    assert meals["calculation"][0]["amount"] == 70.0
    assert expense["payout"] == 3750.0
    assert expense["calculation"][-1] == {
        "step": "Less the deductible",
        "amount": -50.0,
    }
    assert expense["needs_sign_off"] is True


def test_line_rules_apply_other_payments_trip_cost_cap_zero_floor_and_setting(
    client: TestClient,
):
    record = database.get_claim_record("CLM-1001")
    assert record is not None
    policy = deepcopy(record["policy"])
    settings = database.get_setting("plans")

    policy["trip"]["trip_cost"] = 100
    trip_cost = payout(
        policy,
        settings,
        "trip_cancellation",
        [line("expense", 200)],
    )
    zero = payout(
        policy,
        settings,
        "trip_delay",
        [line("expense", 100)],
        others=250,
    )
    custom_limit = payout(
        policy,
        settings,
        "trip_delay",
        [line("expense", 550)],
        limit=500,
    )

    assert trip_cost["payout"] == 100.0
    assert zero["payout"] == 0.0
    assert zero["calculation"][-1]["step"] == "Nothing is left to pay"
    assert custom_limit["needs_sign_off"] is True


def test_payout_endpoint_rejects_unknown_claim_and_incomplete_lines(
    client: TestClient,
):
    unknown_claim = client.post(
        "/api/claims/CLM-9999/payout",
        json={"benefit": "baggage", "lines": [], "paid_by_others": 0},
    )
    missing_field = client.post(
        "/api/claims/CLM-1001/payout",
        json={
            "benefit": "baggage",
            "lines": [{"description": "Bag", "amount": 20}],
            "paid_by_others": 0,
        },
    )

    assert unknown_claim.status_code == 404
    assert missing_field.status_code == 422
