"""Policy limit and deductible calculations."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import ceil, inf
from typing import Any

from backend.money import CENT, to_inr

CASH_LIMIT_USD = Decimal("100.00")
HOTEL_NIGHT_LIMIT_USD = Decimal("350.00")
MEALS_PER_TRAVELER_PER_DAY_USD = Decimal("35.00")
BENEFIT_MAXIMUM = {
    "trip_cancellation": "trip_cancellation_max",
    "trip_interruption": "trip_cancellation_max",
    "trip_delay": "trip_delay_max",
    "baggage_delay": "baggage_delay_max",
    "baggage": "baggage_max",
    "passport": "passport_max",
    "emergency_medical": "emergency_medical_max",
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


@dataclass(frozen=True)
class PayoutCoverage:
    """Policy settings needed to calculate selected claim items."""

    plan: dict[str, Any]
    add_ons: list[str]
    rate: int | float | str
    deductible_benefits: list[str]
    electronics_plus_limit: int | float | str


@dataclass(frozen=True)
class SelectedItemsPayout:
    """Inputs for a selected-item INR payout calculation."""

    claim: dict[str, Any]
    coverage: PayoutCoverage
    approval_limit_usd: int | float | str
    benefit: str
    selections: list[dict[str, Any]]
    paid_by_others_inr: float


@dataclass(frozen=True)
class LinePayout:
    """Inputs for an explicit-line USD payout calculation."""

    policy: dict[str, Any]
    plan_settings: dict[str, Any]
    approval_limit_usd: int | float | str
    benefit: str
    lines: list[dict[str, Any]]
    paid_by_others: int | float


def currency(amount: int | float | str | Decimal) -> Decimal:
    """Round a money amount to paise."""
    return Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)


def amount_step(description: str, amount: Decimal) -> dict[str, Any]:
    """Create one rounded calculation line."""
    return {"step": description, "amount_inr": float(amount)}


def item_quantity(description: str) -> int:
    """Read an explicit item count from a source description."""
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
    claim_items: list[dict[str, Any]], selections: list[dict[str, Any]]
) -> None:
    """Reject repeated or out-of-range item selections."""
    indices = [item["item_index"] for item in selections]
    if len(indices) != len(set(indices)):
        raise ValueError("A claim item can only be selected once")
    if any(index >= len(claim_items) for index in indices):
        raise ValueError("Claim item index is out of range")


def selected_items(
    benefit: str,
    claim_items: list[dict[str, Any]],
    selections: list[dict[str, Any]],
    coverage: PayoutCoverage,
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    """Calculate selected item amounts in INR."""
    validate_selection(claim_items, selections)
    calculation = []
    total = Decimal("0.00")
    cash_total = Decimal("0.00")
    for selection in selections:
        item = claim_items[selection["item_index"]]
        amount = currency(to_inr(item["amount"], coverage.rate))
        if benefit == "baggage" and item["category"] == "cash":
            cash_total += amount
            continue
        limit = None
        if benefit == "baggage" and item["category"] != "cash":
            per_item_limit = coverage.plan["per_item_limit_inr"]
            if (
                item["category"] == "electronics"
                and "electronics_plus" in coverage.add_ons
            ):
                per_item_limit = to_inr(coverage.electronics_plus_limit, coverage.rate)
            limit = currency(per_item_limit * item_quantity(item["description"]))
        eligible = min(amount, limit) if limit is not None else amount
        description = item["description"]
        if eligible < amount:
            description = f"{description}, capped at the per-item limit"
        calculation.append(amount_step(description, eligible))
        total += eligible
    return calculation, total, cash_total


def add_cash(
    calculation: list[dict[str, Any]],
    total: Decimal,
    cash_total: Decimal,
    rate: int | float | str,
) -> Decimal:
    """Apply the combined cash limit."""
    if not cash_total:
        return total
    eligible_cash = min(cash_total, currency(to_inr(str(CASH_LIMIT_USD), rate)))
    calculation.append(amount_step("Cash, subject to the policy limit", eligible_cash))
    return total + eligible_cash


def subtract_other_payments(
    calculation: list[dict[str, Any]],
    total: Decimal,
    paid_by_others_inr: int | float,
) -> Decimal:
    """Subtract reimbursements from another party."""
    if not paid_by_others_inr:
        return total
    amount = currency(paid_by_others_inr)
    calculation.append(amount_step("Less paid by another party", -amount))
    return total - amount


def subtract_deductible(
    calculation: list[dict[str, Any]],
    total: Decimal,
    benefit: str,
    plan: dict[str, Any],
    deductible_benefits: list[str],
) -> Decimal:
    """Apply the deductible to the benefits named in the plan settings."""
    if benefit not in deductible_benefits or total <= 0:
        return total
    deductible = min(currency(plan["deductible_inr"]), total)
    if deductible:
        calculation.append(amount_step("Less the deductible", -deductible))
    return total - deductible


def benefit_limit(
    benefit: str,
    plan: dict[str, Any],
    claim: dict[str, Any],
    rate: int | float | str,
) -> Decimal:
    """Return the plan maximum, capped at trip cost for cancellation benefits."""
    maximum = currency(plan[f"{BENEFIT_MAXIMUM[benefit]}_inr"])
    if benefit in {"trip_cancellation", "trip_interruption"}:
        trip_cost = currency(to_inr(claim["policy"]["trip"]["trip_cost"], rate))
        maximum = min(maximum, trip_cost)
    return maximum


def apply_benefit_limit(
    calculation: list[dict[str, Any]], total: Decimal, maximum: Decimal
) -> Decimal:
    """Apply the plan maximum and prevent a negative payout."""
    if total > maximum:
        calculation.append(
            amount_step("Less the amount over the benefit maximum", maximum - total)
        )
        return maximum
    if total < 0:
        calculation.append(amount_step("Nothing is left to pay", -total))
        return Decimal("0.00")
    return total


def calculate_items(
    request: SelectedItemsPayout,
) -> dict[str, Any]:
    """Calculate the legacy selected-item request with supplied policy settings."""
    coverage = request.coverage
    plan = coverage.plan
    calculation, total, cash_total = selected_items(
        request.benefit,
        request.claim["claim"]["items"],
        request.selections,
        coverage,
    )
    total = add_cash(calculation, total, cash_total, coverage.rate)
    total = subtract_other_payments(calculation, total, request.paid_by_others_inr)
    total = subtract_deductible(
        calculation,
        total,
        request.benefit,
        plan,
        coverage.deductible_benefits,
    )
    total = apply_benefit_limit(
        calculation,
        total,
        benefit_limit(request.benefit, plan, request.claim, coverage.rate),
    )
    payout = float(currency(total))
    approval_limit = currency(to_inr(request.approval_limit_usd, coverage.rate))
    return {
        "calculation": calculation,
        "payout_inr": payout,
        "needs_sign_off": payout > approval_limit,
    }


def line_limit(
    line: dict[str, Any],
    plan: dict[str, Any],
    policy: dict[str, Any],
    plan_settings: dict[str, Any],
) -> Decimal:
    """Return the applicable limit in US dollars."""
    quantity = line["quantity"]
    kind = line["kind"]
    travelers = len(policy["travelers"])
    if kind == "item":
        return currency(plan["per_item_limit"] * quantity)
    if kind == "electronics":
        per_item = plan["per_item_limit"]
        if "electronics_plus" in policy["add_ons"]:
            per_item = next(
                add_on["electronics_per_item_limit"]
                for add_on in plan_settings["add_ons"]
                if add_on["add_on"] == "electronics_plus"
            )
        return currency(per_item * quantity)
    if kind == "hotel":
        return currency(HOTEL_NIGHT_LIMIT_USD * ceil(travelers / 2) * line["nights"])
    if kind == "meals_without_receipts":
        days = ceil(line["hours"] / 24)
        return currency(MEALS_PER_TRAVELER_PER_DAY_USD * travelers * days)
    return Decimal(str(inf))


def calculate_lines(
    request: LinePayout,
) -> dict[str, Any]:
    """Calculate a payout in US dollars from claim lines and supplied settings."""
    if request.benefit not in BENEFIT_MAXIMUM:
        raise ValueError("Unknown benefit")
    plan = next(
        (
            item
            for item in request.plan_settings["plans"]
            if item["plan"] == request.policy["plan"]
        ),
        None,
    )
    if plan is None:
        raise ValueError("Unknown policy plan")
    calculation, total, cash = calculate_line_items(request, plan)
    total = add_line_cash(calculation, total, cash)
    others = currency(request.paid_by_others)
    if others:
        calculation.append(
            amount_step("Less what others have paid or refunded", -others)
        )
        total -= others
    if request.benefit in request.plan_settings["deductible_applies_to"] and total > 0:
        deductible = min(currency(plan["deductible"]), total)
        if deductible:
            calculation.append(amount_step("Less the deductible", -deductible))
            total -= deductible
    maximum = currency(plan[BENEFIT_MAXIMUM[request.benefit]])
    if request.benefit in {"trip_cancellation", "trip_interruption"}:
        maximum = min(maximum, currency(request.policy["trip"]["trip_cost"]))
    total = apply_benefit_limit(calculation, total, maximum)
    payout = float(currency(total))
    return {
        "calculation": [
            {"step": row["step"], "amount": row["amount_inr"]} for row in calculation
        ],
        "payout": payout,
        "needs_sign_off": payout > currency(request.approval_limit_usd),
    }


def calculate_line_items(
    request: LinePayout, plan: dict[str, Any]
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    """Calculate non-cash lines and collect the cash total."""
    calculation: list[dict[str, Any]] = []
    total = Decimal("0.00")
    cash = Decimal("0.00")
    for line in request.lines:
        amount = currency(line["amount"])
        if line["kind"] == "cash":
            cash += amount
            continue
        limit = line_limit(line, plan, request.policy, request.plan_settings)
        eligible = min(amount, limit) if limit.is_finite() else amount
        name = line["description"]
        if eligible < amount and line["kind"] in {
            "item",
            "electronics",
            "hotel",
            "meals_without_receipts",
        }:
            name = f"{name}, limited to ${float(limit):,.2f}"
        calculation.append(amount_step(name, eligible))
        total += eligible
    return calculation, total, cash


def add_line_cash(
    calculation: list[dict[str, Any]], total: Decimal, cash: Decimal
) -> Decimal:
    """Apply the combined cash limit to a USD payout."""
    if not cash:
        return total
    cash_limit = currency(CASH_LIMIT_USD)
    eligible_cash = min(cash, cash_limit)
    calculation.append(
        amount_step(f"Cash, up to ${float(CASH_LIMIT_USD):,.0f} in all", eligible_cash)
    )
    return total + eligible_cash
