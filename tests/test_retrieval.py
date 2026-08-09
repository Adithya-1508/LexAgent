from datetime import date

from uae_lexagent.models import Jurisdiction, Language, LegalProvision, LegalQuery
from uae_lexagent.retrieval import InMemoryRetriever


def _provision(
    provision_id: str,
    text: str,
    *,
    jurisdiction: Jurisdiction = Jurisdiction.UAE_ONSHORE,
    language: str = "en",
    effective_from: date | None = None,
    effective_until: date | None = None,
) -> LegalProvision:
    is_auth = (language == "ar")
    trans_of = None if is_auth else f"{provision_id}-ar"
    return LegalProvision(
        provision_id=provision_id,
        document_id="law-1",
        source_id="source-1",
        jurisdiction=jurisdiction,
        legal_domain="employment",
        article="9",
        language=language,
        is_authoritative=is_auth,
        translation_of=trans_of,
        text=text,
        effective_from=effective_from,
        effective_until=effective_until,
        source_url="https://example.test/law",
    )


def test_retrieval_applies_jurisdiction_and_date_filters_before_scoring() -> None:
    retriever = InMemoryRetriever(
        [
            _provision("onshore", "probation termination notice period"),
            _provision("difc", "probation termination notice period", jurisdiction=Jurisdiction.DIFC),
            _provision(
                "future",
                "probation termination notice period",
                effective_from=date(2027, 1, 1),
            ),
        ]
    )
    results = retriever.search(
        LegalQuery(
            query_id="q-1",
            text="probation termination notice period",
            language=Language.EN,
            jurisdiction=Jurisdiction.UAE_ONSHORE,
            as_of=date(2026, 8, 9),
        )
    )

    assert [item.provision.provision_id for item in results] == ["onshore"]


def test_retrieval_can_match_arabic_provisions_for_arabic_queries() -> None:
    retriever = InMemoryRetriever(
        [_provision("ar-1", "فترة الاختبار وإنهاء عقد العمل", language="ar")]
    )
    results = retriever.search(
        LegalQuery(
            query_id="q-ar",
            text="إنهاء عقد العمل خلال فترة الاختبار",
            language=Language.AR,
            jurisdiction=Jurisdiction.UAE_ONSHORE,
        )
    )

    assert len(results) == 1
    assert results[0].provision.language == "ar"

