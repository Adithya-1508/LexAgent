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

