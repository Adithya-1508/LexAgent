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
