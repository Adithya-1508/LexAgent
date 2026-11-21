from decimal import Decimal

from uae_lexagent.tools import (
    GratuityInput,
    GratuityPolicy,
    calculate_end_of_service_gratuity,
)


def test_gratuity_calculator_is_deterministic_and_traceable() -> None:
    result = calculate_end_of_service_gratuity(
        GratuityInput(basic_salary=Decimal("12000"), service_years=Decimal("4")),
        GratuityPolicy(
            policy_id="test-policy-v1",
            days_per_year=Decimal("21"),
            legal_basis="verified provision fixture",
        ),
    )

    assert result.amount == Decimal("33600.00")
    assert result.currency == "AED"
    assert result.policy_id == "test-policy-v1"
    assert "21 days/year" in result.calculation_basis


def test_gratuity_policy_can_cap_service_years() -> None:
    result = calculate_end_of_service_gratuity(
        GratuityInput(basic_salary=Decimal("10000"), service_years=Decimal("10")),
        GratuityPolicy(
            policy_id="capped-policy-v1",
            days_per_year=Decimal("30"),
            max_years=Decimal("5"),
            legal_basis="verified provision fixture",
        ),
    )

    assert result.amount == Decimal("50000.00")
    assert "5 years" in result.calculation_basis


def test_gratuity_statutory_eligibility_and_caps() -> None:
    from uae_lexagent.tools import GratuityInput, calculate_gratuity

    # 1. UAE National pension exclusion
    res_nat = calculate_gratuity(basic_salary=10000, service_years=5, is_uae_national=True)
    assert res_nat["total_gratuity"] == 0.0
    assert res_nat["eligibility_status"] == "ineligible_pension_applicable"
    assert "Pension" in res_nat["note"]

    # 2. Service under 1 year
    res_under = calculate_gratuity(basic_salary=10000, service_years=0.8)
    assert res_under["total_gratuity"] == 0.0
    assert res_under["eligibility_status"] == "ineligible_under_one_year"

    # 3. Maximum 2-year basic wage ceiling (24 months)
    # 30 years service: without cap = 5 * 21/30 * 10000 + 25 * 30/30 * 10000 = 35000 + 250000 = 285000
    # Cap = 24 * 10000 = 240,000
    res_cap = calculate_gratuity(basic_salary=10000, service_years=30)
    assert res_cap["total_gratuity"] == 240000.00
    assert res_cap["eligibility_status"] == "eligible"

    # 4. Validated GratuityInput bounds
    inp = GratuityInput(
        basic_salary=Decimal("5000"),
        service_years=Decimal("2.5"),
        is_uae_national=False,
    )
    assert inp.work_pattern == "full_time"
    assert inp.unpaid_absence_days == Decimal("0")


def test_gratuity_part_time_with_contractual_hours() -> None:
    """Part-time calculation under Cabinet Res 1/2022 Art 30 prorates by contractual hours."""
    from uae_lexagent.tools import GratuityCalculationResult, calculate_gratuity

    res = calculate_gratuity(
        basic_salary=10000,
        service_years=2,
        work_pattern="part_time",
        contract_working_hours=20,
        full_time_working_hours=40,
    )

    # Full time is 2 * 21/30 * 10000 = 14000; 20/40 = 50% -> 7000.00
    assert res["total_gratuity"] == 7000.0
    assert res["eligibility_status"] == "part_time_prorated"
    assert res["statutory_basis"] == "CABINET_RES_1_2022_A30_C1"
    assert "50.00%" in res["note"]

    # Strict schema validation
    validated = GratuityCalculationResult.model_validate(res)
    assert validated.total_gratuity == 7000.0


def test_gratuity_part_time_without_contractual_hours_returns_non_calculable() -> None:
    """Part-time without hours must return total_gratuity=None without misleading 0.0 amount."""
    from uae_lexagent.tools import GratuityCalculationResult, calculate_gratuity

    res = calculate_gratuity(
        basic_salary=10000,
        service_years=2,
        work_pattern="part_time",
    )

    assert res["total_gratuity"] is None
    assert res["eligibility_status"] == "non_calculable_missing_hours"
    assert res["statutory_basis"] == "CABINET_RES_1_2022_A30_C1"
    assert "requires contractual working hours" in res["note"]

    validated = GratuityCalculationResult.model_validate(res)
    assert validated.total_gratuity is None
    assert validated.eligibility_status == "non_calculable_missing_hours"


def test_gratuity_input_contract_hours_exceeding_full_time_rejected() -> None:
    """Contract hours greater than full-time equivalent must be rejected."""
    import pytest
    from pydantic import ValidationError

    from uae_lexagent.tools import GratuityInput

    with pytest.raises(ValidationError):
        GratuityInput(
            basic_salary=Decimal("10000"),
            service_years=Decimal("3"),
            work_pattern="part_time",
            contract_working_hours=Decimal("45"),
            full_time_working_hours=Decimal("40"),
        )
