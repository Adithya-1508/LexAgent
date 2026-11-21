"""Deterministic tools used by the agent.

The calculator is policy-driven on purpose. A legal formula must be tied to a
versioned, verified provision before it is used in a production answer.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GratuityPolicy(BaseModel):
    """A transparent calculation policy supplied by a verified legal source."""

    policy_id: str = Field(min_length=1)
    currency: str = Field(default="AED", min_length=1)
    days_per_year: Decimal = Field(gt=0)
    max_years: Decimal | None = Field(default=None, gt=0)
    legal_basis: str = Field(min_length=1)


class GratuityInput(BaseModel):
    basic_salary: Decimal = Field(gt=0)
    service_years: Decimal = Field(ge=0, le=60)
    work_pattern: Literal["full_time", "part_time"] = "full_time"
    is_uae_national: bool = False
    unpaid_absence_days: Decimal = Field(default=Decimal("0"), ge=0)
    contract_working_hours: Decimal | None = Field(default=None, gt=0)
    full_time_working_hours: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_working_hours(self) -> Self:
        if self.contract_working_hours is not None and self.full_time_working_hours is not None:
            if self.contract_working_hours > self.full_time_working_hours:
                raise ValueError("contract_working_hours cannot exceed full_time_working_hours.")
        return self


class GratuityResult(BaseModel):
    amount: Decimal
    currency: str
    policy_id: str
    calculation_basis: str
    legal_basis: str


class GratuityCalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basic_salary: float
    service_years: float
    total_gratuity: float | None = None
    currency: str = "AED"
    statutory_basis: str
    eligibility_status: Literal[
        "eligible",
        "ineligible_under_one_year",
        "ineligible_pension_applicable",
        "part_time_prorated",
        "non_calculable_missing_hours",
    ]
    note: str | None = None


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
    is_uae_national: bool = False,
    work_pattern: str = "full_time",
    unpaid_absence_days: float | Decimal = Decimal("0"),
    contract_working_hours: float | Decimal | None = None,
    full_time_working_hours: float | Decimal | None = None,
) -> dict[str, Any]:
    """Calculate UAE statutory gratuity under Federal Decree-Law No. 33 of 2021 Article 51
    and Cabinet Resolution No. 1 of 2022 Article 30.

    Statutory Authority & Formulas:
    -------------------------------
    1. Full-Time Foreign Workers (FDL 33/2021 Article 51(2)-(3)):
       - Minimum continuous service requirement: 1 year (Clause 2).
       - First 5 years: 21 days basic wage per year (daily wage = basic_salary / 30).
       - Exceeding 5 years: 30 days basic wage per year.
       - Maximum aggregate ceiling: 2 years' basic wage (24 months) (Clause 3).
       - Unpaid absence deduction: Unpaid leave days deducted from continuous service (Clause 2).
       - UAE Nationals: Excluded from gratuity under Article 51(1) and covered by Federal
         Pension and Social Security Law (GPSSA).

    2. Part-Time Work Pattern Proration (Cabinet Resolution No. 1 of 2022 Article 30 Clause 1):
       - Statutory Formula (Clause 1(a)-(b)):
           Proration % = (Contractual working hours per year / Full-time working hours per year) * 100
           Prorated Gratuity = Full-time Equivalent Gratuity * (Contractual Hours / Full-Time Hours)
       - Temporal Units: Contractual hours and full-time equivalent hours must be expressed in
         identical time units (e.g. weekly hours or annual hours), ensuring a dimensionless
         ratio in (0, 1]. Contractual hours cannot exceed full-time equivalent hours.
       - Calculation Period: Proration applies to the baseline full-time gratuity accrued over the
         worker's continuous service period (minimum 1 year per Article 30 Clause 2).
       - Missing Hours: If contractual or full-time hours are unspecified, the function returns
         total_gratuity=None with eligibility_status='non_calculable_missing_hours' to avoid
         misleading zero or fabricated amounts.

    Architectural Design Decision: Direct Calculator API vs Autonomous Agent Gating:
    -------------------------------------------------------------------------------
    - Direct Calculator API (this function): Retains standard programmatic defaults
      (`is_uae_national=False, work_pattern='full_time'`) for deterministic developer scripting
      and utility functions where inputs are pre-validated by external callers.
    - Autonomous Legal Agent (`tool_node`): Strictly prohibits silent factual assumptions. If
      a user query omits worker nationality or work pattern, the agent refuses to apply default
      arguments and strictly abstains with `fallback_reason='missing_eligibility_facts'`. Silent
      factual assumptions in legal reasoning systems are dangerous and policy-forbidden.
    """
    try:
        salary = Decimal(str(basic_salary))
        years = Decimal(str(service_years))
        absence = Decimal(str(unpaid_absence_days))
    except Exception as exc:
        raise ValueError(
            f"Invalid gratuity inputs: could not parse numeric values: {type(exc).__name__}"
        ) from exc

    if not salary.is_finite() or not years.is_finite() or not absence.is_finite():
        raise ValueError(
            "Invalid gratuity input: non-finite values (NaN or Infinity) are prohibited."
        )

    if salary <= Decimal("0"):
        raise ValueError("Invalid gratuity input: basic_salary must be strictly positive.")

    if years < Decimal("0") or absence < Decimal("0"):
        raise ValueError(
            "Invalid gratuity input: service_years and unpaid_absence_days must be non-negative."
        )

    # Optional working hours validation
    contract_hours: Decimal | None = None
    full_time_hours: Decimal | None = None
    if contract_working_hours is not None:
        try:
            contract_hours = Decimal(str(contract_working_hours))
        except Exception as exc:
            raise ValueError(f"Invalid contract_working_hours: {type(exc).__name__}") from exc
        if not contract_hours.is_finite() or contract_hours <= Decimal("0"):
            raise ValueError(
                "Invalid contract_working_hours: must be strictly positive and finite."
            )

    if full_time_working_hours is not None:
        try:
            full_time_hours = Decimal(str(full_time_working_hours))
        except Exception as exc:
            raise ValueError(f"Invalid full_time_working_hours: {type(exc).__name__}") from exc
        if not full_time_hours.is_finite() or full_time_hours <= Decimal("0"):
            raise ValueError(
                "Invalid full_time_working_hours: must be strictly positive and finite."
            )

    if contract_hours is not None and full_time_hours is not None:
        if contract_hours > full_time_hours:
            raise ValueError("contract_working_hours cannot exceed full_time_working_hours.")

    # 1. Article 51(1): UAE National pension exclusion
    if is_uae_national:
        res = GratuityCalculationResult(
            basic_salary=float(salary),
            service_years=float(years),
            total_gratuity=0.0,
            currency="AED",
            statutory_basis="FDL_33_2021_A51_C1",
            eligibility_status="ineligible_pension_applicable",
            note=(
                "UAE national workers are covered under the Federal Pension and Social Security "
                "Law rather than end-of-service gratuity under Article 51(1)."
            ),
        )
        return res.model_dump()

    # Article 51(2): Days of absence from work without pay shall not be included in calculating service
    effective_years = max(Decimal("0.0"), years - (absence / Decimal("365.0")))

    if effective_years < Decimal("1.0"):
        res = GratuityCalculationResult(
            basic_salary=float(salary),
            service_years=float(years),
            total_gratuity=0.0,
            currency="AED",
            statutory_basis="FDL_33_2021_A51_C2",
            eligibility_status="ineligible_under_one_year",
            note=(
                "A worker who has completed less than 1 year of continuous service is not entitled "
                "to end-of-service gratuity under Article 51(2)."
            ),
        )
        return res.model_dump()

    # Baseline full-time statutory entitlement under Article 51(2) & 51(3)
    daily_wage = salary / Decimal("30")
    first_5_years = min(effective_years, Decimal("5.0"))
    gratuity_first_5 = daily_wage * Decimal("21") * first_5_years

    additional_years = max(Decimal("0.0"), effective_years - Decimal("5.0"))
    gratuity_additional = daily_wage * Decimal("30") * additional_years

    full_time_total = (gratuity_first_5 + gratuity_additional).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    max_cap = salary * Decimal("24")  # 2 years total basic salary cap (Article 51(3))
    final_full_time = min(full_time_total, max_cap)

    # 2. Part-Time Work Pattern Proration under Cabinet Resolution No. 1 of 2022 Article 30
    if work_pattern == "part_time":
        if contract_hours is not None and full_time_hours is not None:
            proration_ratio = contract_hours / full_time_hours
            prorated_amount = (final_full_time * proration_ratio).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            res = GratuityCalculationResult(
                basic_salary=float(salary),
                service_years=float(years),
                total_gratuity=float(prorated_amount),
                currency="AED",
                statutory_basis="CABINET_RES_1_2022_A30_C1",
                eligibility_status="part_time_prorated",
                note=(
                    f"Part-time worker end-of-service gratuity prorated at "
                    f"{(proration_ratio * 100).quantize(Decimal('0.01'))}% "
                    f"({contract_hours}/{full_time_hours} hours) under Cabinet Resolution "
                    f"No. 1 of 2022 Article 30 against Article 51 baseline AED {final_full_time}."
                ),
            )
            return res.model_dump()

        res = GratuityCalculationResult(
            basic_salary=float(salary),
            service_years=float(years),
            total_gratuity=None,
            currency="AED",
            statutory_basis="CABINET_RES_1_2022_A30_C1",
            eligibility_status="non_calculable_missing_hours",
            note=(
                "Part-time gratuity calculation under Cabinet Resolution No. 1 of 2022 Article 30 "
                "requires contractual working hours and full-time equivalent hours to determine the "
                "statutory proration percentage. Amount cannot be determined from basic salary and "
                "service years alone."
            ),
        )
        return res.model_dump()

    # 3. Standard full-time worker calculation under Article 51(2)-(3)
    res = GratuityCalculationResult(
        basic_salary=float(salary),
        service_years=float(years),
        total_gratuity=float(final_full_time),
        currency="AED",
        statutory_basis="FDL_33_2021_A51_C2",
        eligibility_status="eligible",
        note="Calculated under Article 51(2) with 2-year basic wage ceiling under Article 51(3).",
    )
    return res.model_dump()
