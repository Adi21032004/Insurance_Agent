"""Currency conversion for the claims pilot."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")
MONEY_PATTERN = re.compile(r"\$([0-9][0-9,]*(?:\.\d{1,2})?)")


def to_inr(amount_usd: int | float | str, rate: int | float | str | Decimal) -> float:
    """Convert a USD amount to INR and round to paise."""
    amount = Decimal(str(amount_usd)) * Decimal(str(rate))
    return float(amount.quantize(CENT, rounding=ROUND_HALF_UP))


def convert_currency_text(value: str, rate: int | float | str | Decimal) -> str:
    """Convert dollar amounts embedded in source text to Indian rupees."""

    def replace(match: re.Match[str]) -> str:
        amount_inr = to_inr(match.group(1).replace(",", ""), rate)
        return f"₹{amount_inr:,.2f}"

    return MONEY_PATTERN.sub(replace, value)


def converted_strings(value: Any, rate: int | float | str | Decimal) -> Any:
    """Convert currency mentions in nested source text while retaining its shape."""
    if isinstance(value, str):
        return convert_currency_text(value, rate)
    if isinstance(value, list):
        return [converted_strings(item, rate) for item in value]
    if isinstance(value, dict):
        return {key: converted_strings(item, rate) for key, item in value.items()}
    return value


def converted_claim(
    source: dict[str, Any], rate: int | float | str | Decimal
) -> dict[str, Any]:
    """Return a claim with numeric and written amounts expressed in INR."""
    claim = converted_strings(source, rate)
    claim["amount_claimed_inr"] = to_inr(source["amount_claimed"], rate)
    del claim["amount_claimed"]
    claim["items"] = [
        {
            **item,
            "amount_inr": to_inr(item["amount"], rate),
        }
        for item in claim["items"]
    ]
    for item in claim["items"]:
        del item["amount"]
    return claim


def converted_policy(
    source: dict[str, Any], rate: int | float | str | Decimal
) -> dict[str, Any]:
    """Return a policy with trip costs expressed in INR."""
    policy = converted_strings(source, rate)
    policy["trip"]["trip_cost_inr"] = to_inr(source["trip"]["trip_cost"], rate)
    del policy["trip"]["trip_cost"]
    return policy


def converted_customer(
    source: dict[str, Any], rate: int | float | str | Decimal
) -> dict[str, Any]:
    """Return customer history with paid claim amounts expressed in INR."""
    customer = converted_strings(source, rate)
    for index, previous_claim in enumerate(source["previous_claims"]):
        customer["previous_claims"][index]["amount_paid_inr"] = to_inr(
            previous_claim["amount_paid"], rate
        )
        del customer["previous_claims"][index]["amount_paid"]
    return customer


def converted_plans(
    source: dict[str, Any], rate: int | float | str | Decimal
) -> dict[str, Any]:
    """Return plan limits and deductibles in INR."""
    plans = converted_strings(source, rate)
    monetary_fields = {
        "trip_cancellation_max",
        "trip_delay_max",
        "baggage_delay_max",
        "baggage_max",
        "per_item_limit",
        "passport_max",
        "emergency_medical_max",
        "deductible",
        "electronics_per_item_limit",
    }
    for plan in plans["plans"]:
        for field in monetary_fields:
            if field in plan:
                plan[f"{field}_inr"] = to_inr(plan.pop(field), rate)
    for add_on in plans["add_ons"]:
        field = "electronics_per_item_limit"
        if field in add_on:
            add_on[f"{field}_inr"] = to_inr(add_on.pop(field), rate)
    return plans


def converted_company(source: dict[str, Any]) -> dict[str, Any]:
    """Return company payout settings in INR."""
    rate = Decimal(str(source["usd_to_inr_rate"]))
    company = converted_strings(source, rate)
    company["usd_to_inr_rate"] = float(rate)
    company["payout_approval_limit_inr"] = to_inr(source["payout_approval_limit"], rate)
    del company["payout_approval_limit"]
    return company
