from uae_lexagent.models import Jurisdiction
from uae_lexagent.routing import classify_jurisdiction


def test_difc_is_a_hard_boundary() -> None:
    decision = classify_jurisdiction("I work for a DIFC company. What is my notice period?")

    assert decision.jurisdiction is Jurisdiction.DIFC
    assert not decision.requires_clarification


def test_city_name_does_not_infer_free_zone() -> None:
    decision = classify_jurisdiction("I work in Dubai. What is my notice period?")

    assert decision.jurisdiction is Jurisdiction.UNKNOWN
    assert decision.requires_clarification


def test_conflicting_jurisdictions_require_clarification() -> None:
    decision = classify_jurisdiction("This concerns both DIFC and ADGM.")

    assert decision.jurisdiction is Jurisdiction.UNKNOWN
    assert decision.requires_clarification
