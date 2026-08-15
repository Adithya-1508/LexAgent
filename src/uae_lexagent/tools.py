"""Deterministic tools used by the agent.

The calculator is policy-driven on purpose. A legal formula must be tied to a
versioned, verified provision before it is used in a production answer.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, Field


class GratuityPolicy(BaseModel):
    """A transparent calculation policy supplied by a verified legal source."""

    policy_id: str = Field(min_length=1)
    currency: str = Field(default="AED", min_length=1)
    days_per_year: Decimal = Field(gt=0)
    max_years: Decimal | None = Field(default=None, gt=0)
    legal_basis: str = Field(min_length=1)


class GratuityInput(BaseModel):
    basic_salary: Decimal = Field(gt=0)
    service_years: Decimal = Field(ge=0)


class GratuityResult(BaseModel):
    amount: Decimal
    currency: str
    policy_id: str
    calculation_basis: str
    legal_basis: str


def calculate_end_of_service_gratuity(
    values: GratuityInput,
    policy: GratuityPolicy,
) -> GratuityResult:
    """Calculate using an explicit policy; never infer a legal rule in code."""

    years = values.service_years
    if policy.max_years is not None:
        years = min(years, policy.max_years)

    daily_rate = values.basic_salary / Decimal("30")
    raw_amount = daily_rate * policy.days_per_year * years
    amount = raw_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return GratuityResult(
        amount=amount,
        currency=policy.currency,
        policy_id=policy.policy_id,
        calculation_basis=(
            f"{values.basic_salary} monthly basic salary / 30 * "
            f"{policy.days_per_year} days/year * {years} years"
        ),
        legal_basis=policy.legal_basis,
    )


def calculate_gratuity(
    basic_salary: float | Decimal,
    service_years: float | Decimal,
) -> dict[str, Any]:
    """Calculate UAE statutory gratuity under Federal Decree-Law No. 33 of 2021 Article 51."""
    salary = Decimal(str(basic_salary))
    years = Decimal(str(service_years))

    if years < Decimal("1.0"):
        return {
            "basic_salary": float(salary),
            "service_years": float(years),
            "total_gratuity": 0.0,
            "currency": "AED",
            "statutory_basis": "FDL_33_2021_A51_C2",
        }

    daily_wage = salary / Decimal("30")
    first_5_years = min(years, Decimal("5.0"))
    gratuity_first_5 = daily_wage * Decimal("21") * first_5_years

    additional_years = max(Decimal("0.0"), years - Decimal("5.0"))
    gratuity_additional = daily_wage * Decimal("30") * additional_years

    total = (gratuity_first_5 + gratuity_additional).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    max_cap = salary * Decimal("24")  # 2 years total basic salary cap

    final_amount = min(total, max_cap)

    return {
        "basic_salary": float(salary),
        "service_years": float(years),
        "total_gratuity": float(final_amount),
        "currency": "AED",
        "statutory_basis": "FDL_33_2021_A51_C2",
    }
