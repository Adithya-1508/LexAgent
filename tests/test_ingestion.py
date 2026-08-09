from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from uae_lexagent import (
    Jurisdiction,
    Language,
    LegalProvision,
    LegalQuery,
    is_valid_official_url,
    load_document_metadata,
    load_provisions,
    load_source_manifest,
    validate_bilingual_alignment,
    validate_corpus,
    validate_provision_integrity,
    validate_source_manifest,
    validate_temporal_consistency,
)
from uae_lexagent.retrieval import InMemoryRetriever

DATA_DIR = Path(__file__).parent.parent / "data" / "official_sources"
PROJECT_ROOT = Path(__file__).parent.parent


def test_source_manifest_reports_missing_official_pdf_lineage() -> None:
    manifest_path = DATA_DIR / "manifest.json"
    sources = load_source_manifest(manifest_path)
    assert len(sources) >= 4

    issues = validate_source_manifest(sources, base_dir=PROJECT_ROOT)
    assert len(issues) == 4
    assert all("unknown derived_from_source_id" in issue for issue in issues)


def test_document_metadata_loads_and_declares_partial_mvp() -> None:
    doc_path = DATA_DIR / "fdl_33_2021_document.json"
    doc = load_document_metadata(doc_path)
    assert doc.document_id == "FDL_33_2021_CURRENT"
    assert doc.jurisdiction == Jurisdiction.UAE_ONSHORE
    assert doc.status == "active"
    assert doc.coverage_status == "partial_mvp"
    assert doc.authoritative_language == "ar"
    assert doc.version == "2021-as-amended-2024"


def test_validate_corpus_exposes_unavailable_official_pdf_lineage() -> None:
    sources = load_source_manifest(DATA_DIR / "manifest.json")
    fdl_doc = load_document_metadata(DATA_DIR / "fdl_33_2021_document.json")
    cab_doc = load_document_metadata(DATA_DIR / "cabinet_resolution_1_2022_document.json")
    fdl_provs = load_provisions(DATA_DIR / "fdl_33_2021_provisions.json")
    cab_provs = load_provisions(DATA_DIR / "cabinet_resolution_1_2022_provisions.json")

    documents = [fdl_doc, cab_doc]
    provisions = fdl_provs + cab_provs

    corpus_issues = validate_corpus(documents, sources, provisions, base_dir=PROJECT_ROOT)
    assert len(corpus_issues) == 4
    assert all("unknown derived_from_source_id" in issue for issue in corpus_issues)


def test_article_9_clauses_1_to_7_alignment() -> None:
    prov_path = DATA_DIR / "fdl_33_2021_provisions.json"
    provisions = load_provisions(prov_path)

    art_9_provisions = [p for p in provisions if p.article == "9"]
    clause_numbers = {p.clause for p in art_9_provisions}
    assert clause_numbers == {"1", "2", "3", "4", "5", "6", "7"}

    for clause in ("1", "2", "3", "4", "5", "6", "7"):
        ar = [p for p in art_9_provisions if p.clause == clause and p.language == "ar"]
        en = [p for p in art_9_provisions if p.clause == clause and p.language == "en"]
        assert len(ar) == 1
        assert len(en) == 1
        assert en[0].translation_of == ar[0].provision_id
        assert ar[0].is_authoritative is True
        assert en[0].is_authoritative is False


def test_clause_2_vs_clause_3_correction() -> None:
    prov_path = DATA_DIR / "fdl_33_2021_provisions.json"
    provisions = load_provisions(prov_path)

    c2_en = [p for p in provisions if p.article == "9" and p.clause == "2" and p.language == "en"][0]
    c3_en = [p for p in provisions if p.article == "9" and p.clause == "3" and p.language == "en"][0]

    assert "successfully completes the probation period" in c2_en.text
    assert "move during the probation period to work for another employer" in c3_en.text


def test_strict_url_hostname_validation() -> None:
    assert is_valid_official_url("https://www.mohre.gov.ae/en/laws-and-regulations/laws.aspx") is True
    assert is_valid_official_url("https://uaelegislation.gov.ae") is True
    assert is_valid_official_url("https://mohre.gov.ae.attacker.com/malicious") is False
    assert is_valid_official_url("http://www.mohre.gov.ae") is False  # Insecure HTTP rejected


def test_foreign_jurisdiction_isolation() -> None:
    doc = load_document_metadata(DATA_DIR / "fdl_33_2021_document.json")
    sources = load_source_manifest(DATA_DIR / "manifest.json")

    valid_ar_difc = LegalProvision(
        provision_id="DIFC_A1_AR",
        document_id="FDL_33_2021_CURRENT",
        source_id="MOHRE_FDL_33_2021_AR_CURRENT",
        jurisdiction=Jurisdiction.DIFC,
        legal_domain="employment",
        article="1",
        language="ar",
        is_authoritative=True,
        translation_of=None,
        text="DIFC arabic text",
        source_url="https://uaelegislation.gov.ae/ar/legislations/1541/download",
    )

    issues = validate_provision_integrity([doc], sources, [valid_ar_difc])
    assert len(issues) == 1
    assert "conflicts with document jurisdiction" in issues[0]


def test_cabinet_resolution_1_2022_bilingual_sources() -> None:
    doc = load_document_metadata(DATA_DIR / "cabinet_resolution_1_2022_document.json")
    sources = load_source_manifest(DATA_DIR / "manifest.json")
    cab_sources = [s for s in sources if s.document_id == doc.document_id]
    provisions = load_provisions(DATA_DIR / "cabinet_resolution_1_2022_provisions.json")

    issues = validate_corpus([doc], cab_sources, provisions, base_dir=PROJECT_ROOT)
    assert len(issues) == 2
    assert all("unknown derived_from_source_id" in issue for issue in issues)

    ar_prov = [p for p in provisions if p.language == "ar"][0]
    en_prov = [p for p in provisions if p.language == "en"][0]

    assert ar_prov.source_id == "MOHRE_CABINET_RES_1_2022_AR"
    assert en_prov.source_id == "MOHRE_CABINET_RES_1_2022_EN"


def test_unaligned_provisions_rejected() -> None:
    doc = load_document_metadata(DATA_DIR / "fdl_33_2021_document.json")
    sources = load_source_manifest(DATA_DIR / "manifest.json")

    unaligned_prov = LegalProvision(
        provision_id="FDL_33_2021_A99_C1_EN",
        document_id="FDL_33_2021_CURRENT",
        source_id="MOHRE_FDL_33_2021_EN_CURRENT",
        jurisdiction=Jurisdiction.UAE_ONSHORE,
        legal_domain="employment",
        article="99",
        language="en",
        is_authoritative=False,
        alignment_id=None,  # Missing alignment_id
        translation_of="FDL_33_2021_A99_C1_AR",
        text="Unaligned English text",
        source_url="https://www.mohre.gov.ae/en/laws.aspx",
    )

    issues = validate_bilingual_alignment([unaligned_prov])
    assert len(issues) == 1
    assert "lacks mandatory alignment_id" in issues[0]


def test_missing_raw_file_causes_hard_validation_error() -> None:
    sources = load_source_manifest(DATA_DIR / "manifest.json")
    sources[0].local_path = "data/official_sources/raw/non_existent_file.pdf"

    issues = validate_source_manifest(sources, base_dir=PROJECT_ROOT)
    assert any("does not exist on disk" in issue for issue in issues)


def test_article_9_clause_4_and_7_preserve_all_relevant_conditions() -> None:
    provisions = load_provisions(DATA_DIR / "fdl_33_2021_provisions.json")
    c4 = next(p for p in provisions if p.provision_id == "FDL_33_2021_A9_C4_EN")
    c7 = next(p for p in provisions if p.provision_id == "FDL_33_2021_A9_C7_EN")

    assert "three months" in c4.text
    assert "compensation stipulated in Clause (3)" in c4.text
    assert "condition of not granting a work permit" in c7.text
    assert "compensation requirement" not in c7.text


@pytest.mark.parametrize(
    ("article", "expected_clauses"),
    (("42", {str(number) for number in range(1, 10)}),
     ("43", {str(number) for number in range(1, 6)}),
     ("51", {str(number) for number in range(1, 9)}),
     ("52", {"1"}),
     ("66", {"1", "2"})),
)
def test_statutory_articles_are_clause_granular(article: str, expected_clauses: set[str]) -> None:
    provisions = load_provisions(DATA_DIR / "fdl_33_2021_provisions.json")
    article_provisions = [p for p in provisions if p.article == article]

    assert {p.clause for p in article_provisions} == expected_clauses
    for clause in expected_clauses:
        pair = [p for p in article_provisions if p.clause == clause]
        assert {p.language for p in pair} == {"ar", "en"}
        assert len(pair) == 2
        assert pair[1].alignment_id == pair[0].alignment_id


def test_article_51_and_66_no_longer_merge_numbered_clauses() -> None:
    provisions = load_provisions(DATA_DIR / "fdl_33_2021_provisions.json")
    c51_2 = next(p for p in provisions if p.provision_id == "FDL_33_2021_A51_C2_EN")
    c51_5 = next(p for p in provisions if p.provision_id == "FDL_33_2021_A51_C5_EN")
    c66_2 = next(p for p in provisions if p.provision_id == "FDL_33_2021_A66_C2_EN")

    assert "twenty-one (21) days' wage" in c51_2.text
    assert "thirty (30) days' wage" in c51_2.text
    assert "last basic wage" in c51_5.text
    assert "another language" in c66_2.text


def test_source_path_escape_is_rejected() -> None:
    sources = load_source_manifest(DATA_DIR / "manifest.json")
    sources[0].local_path = "..\\outside.txt"

    issues = validate_source_manifest(sources, base_dir=PROJECT_ROOT)
    assert any("escapes the configured base directory" in issue for issue in issues)


def test_temporal_validator_rejects_mismatched_bilingual_windows() -> None:
    provisions = load_provisions(DATA_DIR / "fdl_33_2021_provisions.json")
    ar = next(p for p in provisions if p.provision_id == "FDL_33_2021_A9_C1_AR")
    en = next(p for p in provisions if p.provision_id == "FDL_33_2021_A9_C1_EN")
    en.effective_from = date(2023, 1, 1)

    issues = validate_temporal_consistency([], [ar, en])
    assert len(issues) == 1
    assert "mismatched effective dates" in issues[0]


def test_provenance_lineage_is_required_for_derived_text() -> None:
    sources = load_source_manifest(DATA_DIR / "manifest.json")
    assert all(source.artifact_type == "derived_text" for source in sources)
    assert all(source.derived_from_source_id for source in sources)
