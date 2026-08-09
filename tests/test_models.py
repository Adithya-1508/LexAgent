from datetime import date

import pytest
from pydantic import ValidationError

from uae_lexagent.models import (
    Jurisdiction,
    LegalDocument,
    LegalEvidence,
    LegalProvision,
    LegalSource,
    SourceType,
)


def test_document_and_provision_preserve_citation_metadata() -> None:
    document = LegalDocument(
        document_id="example-law-2026",
        title_en="Example Employment Law",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        legal_domain="employment",
        source_url="https://example.test/law",
        source_type=SourceType.OFFICIAL,
        effective_from=date(2026, 1, 1),
        version="2026-01",
    )
    provision = LegalProvision(
        provision_id="example-law-2026-a9-c1-en",
        document_id=document.document_id,
        source_id="MOHRE_EX_2026_EN",
        jurisdiction=document.jurisdiction,
        legal_domain=document.legal_domain,
        article="9",
        clause="1",
        language="en",
        is_authoritative=False,
        translation_of="example-law-2026-a9-c1-ar",
        text="Example provision text.",
        alignment_id="example-law-2026-a9-c1",
        effective_from=document.effective_from,
        source_url=document.source_url,
    )
    evidence = LegalEvidence(
        evidence_id="ev-1",
        provision=provision,
        retrieval_rank=1,
        dense_score=0.91,
    )

    assert evidence.provision.article == "9"
    assert evidence.provision.alignment_id == "example-law-2026-a9-c1"
    assert evidence.provision.jurisdiction is Jurisdiction.UAE_ONSHORE


def test_document_rejects_invalid_effective_window() -> None:
    with pytest.raises(ValidationError):
        LegalDocument(
            document_id="bad-window",
            title_en="Bad Window",
            jurisdiction=Jurisdiction.UAE_ONSHORE,
            legal_domain="employment",
            source_url="https://example.test/bad",
            source_type=SourceType.OFFICIAL,
            effective_from=date(2026, 2, 1),
            effective_until=date(2026, 1, 1),
        )


def test_source_rejects_invalid_sha256_and_incomplete_lineage() -> None:
    with pytest.raises(ValidationError):
        LegalSource(
            source_id="bad-hash",
            document_id="doc",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.pdf",
            local_path="law.pdf",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="not-a-sha256",
            is_authoritative=False,
            extraction_method="pdf_text",
        )

    with pytest.raises(ValidationError):
        LegalSource(
            source_id="missing-parent",
            document_id="doc",
            language="en",
            publisher="MOHRE",
            source_url="https://mohre.gov.ae/law.txt",
            local_path="law.txt",
            retrieved_at="2026-01-01T00:00:00Z",
            sha256="0" * 64,
            is_authoritative=False,
            artifact_type="derived_text",
            extraction_method="pdf_text",
        )


def test_provision_rejects_invalid_page_and_effective_windows() -> None:
    common = dict(
        provision_id="bad-provision",
        document_id="doc",
        source_id="source",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        legal_domain="employment",
        article="9",
        language="ar",
        is_authoritative=True,
        text="Text",
        source_url="https://mohre.gov.ae/law.pdf",
    )

    with pytest.raises(ValidationError):
        LegalProvision(**common, page_start=5, page_end=4)

    with pytest.raises(ValidationError):
        LegalProvision(
            **common,
            effective_from=date(2026, 2, 1),
            effective_until=date(2026, 1, 1),
        )
