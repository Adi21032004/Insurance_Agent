"""Policy limit and deductible calculations in Indian rupees."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from backend import database
from backend.money import CENT, converted_plans, to_inr

BENEFIT_MAXIMUM = {
    "trip_cancellation": "trip_cancellation_max_inr",
    "trip_interruption": "trip_cancellation_max_inr",
    "trip_delay": "trip_delay_max_inr",
    "baggage_delay": "baggage_delay_max_inr",
    "baggage": "baggage_max_inr",
    "passport": "passport_max_inr",
    "emergency_medical": "emergency_medical_max_inr",
}
QUANTITY_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def currency(amount: float) -> Decimal:
    """Return an INR amount rounded to paise."""
    return Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)


def amount_step(description: str, amount: Decimal) -> dict[str, Any]:
    """Create one rounded calculation line."""
    return {"step": description, "amount_inr": float(amount)}


def item_quantity(description: str) -> int:
    """Read an explicit item count from the source description."""
    match = re.search(
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        description,
        re.IGNORECASE,
    )
    if match is None:
        return 1
    value = match.group(1).lower()
    return int(value) if value.isdigit() else QUANTITY_WORDS[value]


def validate_selection(
    claim_items: list[dict[str, Any]], items: list[dict[str, Any]]
) -> None:
    """Reject repeated or out-of-range claim item selections."""
    indices = [item["item_index"] for item in items]
    if len(indices) != len(set(indices)):
        raise ValueError("A claim item can only be selected once")
    if any(index >= len(claim_items) for index in indices):
        raise ValueError("Claim item index is out of range")


def item_limit(
    benefit: str,
    source_item: dict[str, Any],
    plan: dict[str, Any],
    add_ons: list[str],
) -> Decimal | None:
    """Return the applicable per-item limit for a selected claim item."""
    if benefit != "baggage" or source_item["category"] == "cash":
        return None
    per_item_limit = plan["per_item_limit_inr"]
    if source_item["category"] == "electronics" and "electronics_plus" in add_ons:
        plan_data = converted_plans(database.get_setting("plans"))
        per_item_limit = next(
            row["electronics_per_item_limit_inr"]
            for row in plan_data["add_ons"]
            if row["add_on"] == "electronics_plus"
        )
    return currency(per_item_limit * item_quantity(source_item["description"]))


def selected_items(
    benefit: str,
    claim_items: list[dict[str, Any]],
    selections: list[dict[str, Any]],
    plan: dict[str, Any],
    add_ons: list[str],
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    """Calculate selected line values and collect any claimed cash."""
    validate_selection(claim_items, selections)
    calculation = []
    total = Decimal("0.00")
    cash_total = Decimal("0.00")
    for selection in selections:
        source_item = claim_items[selection["item_index"]]
        amount = currency(to_inr(source_item["amount"]))
        if benefit == "baggage" and source_item["category"] == "cash":
            cash_total += amount
            continue
        limit = item_limit(benefit, source_item, plan, add_ons)
        eligible = min(amount, limit) if limit is not None else amount
        description = source_item["description"]
        if eligible < amount:
            description = f"{description}, capped at the per-item limit"
        calculation.append(amount_step(description, eligible))
        total += eligible
    return calculation, total, cash_total


def add_cash(
    calculation: list[dict[str, Any]], total: Decimal, cash_total: Decimal
) -> Decimal:
    """Apply the wording's combined cash limit to selected items."""
    if cash_total:
        eligible_cash = min(cash_total, currency(to_inr(100)))
        calculation.append(
            amount_step("Cash, subject to the policy limit", eligible_cash)
        )
        return total + eligible_cash
    return total


def subtract_other_payments(
    calculation: list[dict[str, Any]], total: Decimal, paid_by_others_inr: float
) -> Decimal:
    """Subtract carrier or other party reimbursements."""
    if not paid_by_others_inr:
        return total
    paid_by_others = currency(paid_by_others_inr)
    calculation.append(amount_step("Less paid by another party", -paid_by_others))
    return total - paid_by_others


def subtract_deductible(
    calculation: list[dict[str, Any]],
    total: Decimal,
    benefit: str,
    plan: dict[str, Any],
) -> Decimal:
    """Apply the plan deductible to benefits named in the policy."""
    if benefit not in database.get_setting("plans")["deductible_applies_to"]:
        return total
    if total <= 0:
        return total
    deductible = min(currency(plan["deductible_inr"]), total)
    if deductible:
        calculation.append(amount_step("Less the policy deductible", -deductible))
    return total - deductible


def benefit_limit(
    benefit: str, plan: dict[str, Any], record: dict[str, Any]
) -> Decimal:
    """Return the plan maximum, also capped by trip cost where required."""
    maximum = currency(plan[BENEFIT_MAXIMUM[benefit]])
    if benefit in {"trip_cancellation", "trip_interruption"}:
        trip_cost = currency(to_inr(record["policy"]["trip"]["trip_cost"]))
        maximum = min(maximum, trip_cost)
    return maximum


def apply_benefit_limit(
    calculation: list[dict[str, Any]], total: Decimal, maximum: Decimal
) -> Decimal:
    """Cap the calculated amount at the plan benefit maximum."""
    if total > maximum:
        reduction = maximum - total
        calculation.append(
            amount_step("Less the amount over the benefit maximum", reduction)
        )
        return maximum
    if total < 0:
        calculation.append(amount_step("Nothing is left to pay", -total))
        return Decimal("0.00")
    return total


def calculate(
    claim_id: str,
    benefit: str,
    items: list[dict[str, Any]],
    paid_by_others_inr: float,
) -> dict[str, Any]:
    """Calculate a payout from selected items and the insured plan limits."""
    record = database.get_claim_record(claim_id)
    if record is None:
        raise KeyError("Claim not found")
    if benefit != record["claim"]["claim_type"]:
        raise ValueError("Benefit must match the claim type")
    plan_data = converted_plans(database.get_setting("plans"))
    plan = next(
        row for row in plan_data["plans"] if row["plan"] == record["policy"]["plan"]
    )
    calculation, total, cash_total = selected_items(
        benefit,
        record["claim"]["items"],
        items,
        plan,
        record["policy"]["add_ons"],
    )
    total = add_cash(calculation, total, cash_total)
    total = subtract_other_payments(calculation, total, paid_by_others_inr)
    total = subtract_deductible(calculation, total, benefit, plan)
    total = apply_benefit_limit(
        calculation, total, benefit_limit(benefit, plan, record)
    )
    payout = float(total.quantize(CENT, rounding=ROUND_HALF_UP))
    approval_limit = to_inr(database.get_setting("company")["payout_approval_limit"])
    return {
        "calculation": calculation,
        "payout_inr": payout,
        "needs_sign_off": payout > approval_limit,
    }
