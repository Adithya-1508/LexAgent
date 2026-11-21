"""Official legal corpus ingestion and integrity verification module."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from .models import LegalDocument, LegalProvision, LegalSource

ALLOWED_OFFICIAL_HOSTNAMES: set[str] = {
    "mohre.gov.ae",
    "www.mohre.gov.ae",
    "uaelegislation.gov.ae",
    "www.uaelegislation.gov.ae",
}


def is_valid_official_url(url: str) -> bool:
    """Validate that a URL uses HTTPS and points strictly to an approved official domain."""
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in ALLOWED_OFFICIAL_HOSTNAMES
    except Exception:
        return False


def load_source_manifest(path: Path) -> list[LegalSource]:
    """Load raw legal source manifest records from JSON."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return [LegalSource.model_validate(item) for item in data]


def load_document_metadata(path: Path) -> LegalDocument:
    """Load legal document metadata from JSON."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return LegalDocument.model_validate(data)


def load_provisions(path: Path) -> list[LegalProvision]:
    """Load provision-sized legal units from JSON."""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return [LegalProvision.model_validate(item) for item in data]


def validate_source_manifest(
    sources: list[LegalSource],
    base_dir: Path | None = None,
) -> list[str]:
    """Validate physical existence, official domain, and SHA256 checksums of source files."""
    issues: list[str] = []
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        issues.append("Source manifest contains duplicate source_id values.")
    source_map = {source.source_id: source for source in sources}

    for source in sources:
        if not is_valid_official_url(source.source_url):
            issues.append(
                f"Source '{source.source_id}' source_url '{source.source_url}' "
                f"does not belong to an approved official domain."
            )

        norm_path = source.local_path.replace("\\", "/")
        file_path = Path(norm_path)
        if base_dir:
            root = base_dir.resolve()
            file_path = (file_path if file_path.is_absolute() else root / file_path).resolve()
            try:
                file_path.relative_to(root)
            except ValueError:
                issues.append(
                    f"Source '{source.source_id}' local_path escapes the configured base directory."
                )
                continue
        else:
            file_path = file_path.resolve()

        if source.artifact_type == "derived_text":
            if Path(source.local_path).suffix.casefold() != ".txt":
                issues.append(
                    f"Derived text source '{source.source_id}' must use a .txt local_path."
                )
            if source.derived_from_source_id not in source_map:
                issues.append(
                    f"Source '{source.source_id}' references unknown derived_from_source_id "
                    f"'{source.derived_from_source_id}'."
                )
            else:
                parent = source_map[source.derived_from_source_id]
                if parent.artifact_type != "raw_pdf":
                    issues.append(f"Source '{source.source_id}' must derive from a raw_pdf source.")
                if parent.document_id != source.document_id:
                    issues.append(
                        f"Source '{source.source_id}' and its parent source belong to different documents."
                    )
                if parent.language != source.language:
                    issues.append(
                        f"Source '{source.source_id}' and its parent source have different languages."
                    )
        elif Path(source.local_path).suffix.casefold() != ".pdf":
            issues.append(f"Raw PDF source '{source.source_id}' must use a .pdf local_path.")

        if not file_path.exists():
            issues.append(f"Raw source file '{source.local_path}' does not exist on disk.")
            continue
        if not file_path.is_file():
            issues.append(f"Raw source path '{source.local_path}' is not a file.")
            continue

        file_bytes = file_path.read_bytes()
        actual_hash = hashlib.sha256(file_bytes).hexdigest()
        if actual_hash.casefold() != source.sha256.casefold():
            issues.append(
                f"Source '{source.source_id}' SHA256 checksum mismatch. "
                f"Expected '{source.sha256}', calculated '{actual_hash}'"
            )

    return issues


def validate_provision_integrity(
    documents: list[LegalDocument],
    sources: list[LegalSource],
    provisions: list[LegalProvision],
) -> list[str]:
    """Verify provision linkage to valid documents, sources, jurisdictions, and languages."""
    issues: list[str] = []
    doc_map = {d.document_id: d for d in documents}
    src_map = {s.source_id: s for s in sources}
    seen_ids: set[str] = set()

    for prov in provisions:
        if prov.provision_id in seen_ids:
            issues.append(f"Duplicate provision_id found: '{prov.provision_id}'")
        seen_ids.add(prov.provision_id)

        if prov.document_id not in doc_map:
            issues.append(
                f"Provision '{prov.provision_id}' references unknown document_id '{prov.document_id}'"
            )
            continue
        doc = doc_map[prov.document_id]

        if prov.source_id not in src_map:
            issues.append(
                f"Provision '{prov.provision_id}' references unknown source_id '{prov.source_id}'"
            )
            continue
        src = src_map[prov.source_id]

        if src.document_id != prov.document_id:
            issues.append(
                f"Provision '{prov.provision_id}' source '{src.source_id}' belongs to document "
                f"'{src.document_id}', not '{prov.document_id}'"
            )

        if prov.source_id not in doc.source_ids:
            issues.append(
                f"Provision '{prov.provision_id}' source_id '{prov.source_id}' is not declared by "
                f"document '{doc.document_id}'"
            )

        if prov.jurisdiction != doc.jurisdiction:
            issues.append(
                f"Provision '{prov.provision_id}' jurisdiction '{prov.jurisdiction}' "
                f"conflicts with document jurisdiction '{doc.jurisdiction}'"
            )

        if prov.language != src.language:
            issues.append(
                f"Provision '{prov.provision_id}' language '{prov.language}' "
                f"conflicts with source language '{src.language}'"
            )

        if prov.is_authoritative != src.is_authoritative:
            issues.append(
                f"Provision '{prov.provision_id}' is_authoritative '{prov.is_authoritative}' "
                f"conflicts with source authority '{src.is_authoritative}'"
            )
        if src.is_authoritative and src.language != doc.authoritative_language:
            issues.append(
                f"Authoritative source '{src.source_id}' language '{src.language}' "
                f"conflicts with document authoritative_language '{doc.authoritative_language}'"
            )

        if not is_valid_official_url(prov.source_url):
            issues.append(
                f"Provision '{prov.provision_id}' source_url '{prov.source_url}' "
                f"is not a valid official HTTPS URL."
            )
        if prov.source_url != src.source_url:
            issues.append(
                f"Provision '{prov.provision_id}' source_url does not match its manifest source URL."
            )

    return issues


def validate_bilingual_alignment(
    provisions: list[LegalProvision],
) -> list[str]:
    """Verify strict 1-to-1 EN <-> AR alignment and translation linkage for all provisions."""
    issues: list[str] = []
    prov_map = {p.provision_id: p for p in provisions}
    alignment_groups: dict[str, list[LegalProvision]] = {}

    for prov in provisions:
        if not prov.alignment_id:
            issues.append(f"Provision '{prov.provision_id}' lacks mandatory alignment_id.")
            continue
        alignment_groups.setdefault(prov.alignment_id, []).append(prov)

    for align_id, group in alignment_groups.items():
        ar_provisions = [p for p in group if p.language == "ar"]
        en_provisions = [p for p in group if p.language == "en"]

        if len(ar_provisions) != 1:
            issues.append(
                f"Alignment group '{align_id}' must have exactly one Arabic provision, found {len(ar_provisions)}"
            )
        if len(en_provisions) != 1:
            issues.append(
                f"Alignment group '{align_id}' must have exactly one English provision, found {len(en_provisions)}"
            )

        if len(ar_provisions) == 1 and len(en_provisions) == 1:
            ar_p = ar_provisions[0]
            en_p = en_provisions[0]

            if not ar_p.is_authoritative:
                issues.append(
                    f"Arabic provision '{ar_p.provision_id}' in group '{align_id}' must be authoritative"
                )

            if en_p.is_authoritative:
                issues.append(
                    f"English provision '{en_p.provision_id}' in group '{align_id}' cannot be authoritative"
                )

            if en_p.translation_of not in prov_map:
                issues.append(
                    f"English provision '{en_p.provision_id}' translation_of target '{en_p.translation_of}' "
                    f"does not exist in provisions"
                )
            elif en_p.translation_of != ar_p.provision_id:
                issues.append(
                    f"English provision '{en_p.provision_id}' translation_of '{en_p.translation_of}' "
                    f"does not match Arabic provision_id '{ar_p.provision_id}'"
                )

            if en_p.document_id != ar_p.document_id:
                issues.append(f"Alignment group '{align_id}' document mismatch between EN and AR")
            if en_p.jurisdiction != ar_p.jurisdiction:
                issues.append(
                    f"Alignment group '{align_id}' jurisdiction mismatch between EN and AR"
                )
            if en_p.legal_domain != ar_p.legal_domain:
                issues.append(
                    f"Alignment group '{align_id}' legal-domain mismatch between EN and AR"
                )
            if (en_p.article, en_p.clause, en_p.subclause) != (
                ar_p.article,
                ar_p.clause,
                ar_p.subclause,
            ):
                issues.append(
                    f"Alignment group '{align_id}' article/clause metadata mismatch between EN and AR"
                )

    return issues


def validate_temporal_consistency(
    documents: list[LegalDocument],
    provisions: list[LegalProvision],
) -> list[str]:
    """Verify that provision effective dates stay within document validity windows."""
    issues: list[str] = []
    doc_map = {d.document_id: d for d in documents}
    alignment_groups: dict[str, list[LegalProvision]] = {}

    for prov in provisions:
        if prov.alignment_id:
            alignment_groups.setdefault(prov.alignment_id, []).append(prov)

    for prov in provisions:
        if (
            prov.effective_from is not None
            and prov.effective_until is not None
            and prov.effective_until < prov.effective_from
        ):
            issues.append(
                f"Provision '{prov.provision_id}' effective_until {prov.effective_until} "
                f"precedes effective_from {prov.effective_from}"
            )

        doc = doc_map.get(prov.document_id)
        if not doc:
            continue

        if (
            doc.effective_from is not None
            and prov.effective_from is not None
            and prov.effective_from < doc.effective_from
        ):
            issues.append(
                f"Provision '{prov.provision_id}' effective_from {prov.effective_from} "
                f"precedes document effective_from {doc.effective_from}"
            )

        if (
            doc.effective_until is not None
            and prov.effective_until is not None
            and prov.effective_until > doc.effective_until
        ):
            issues.append(
                f"Provision '{prov.provision_id}' effective_until {prov.effective_until} "
                f"exceeds document effective_until {doc.effective_until}"
            )

    for alignment_id, group in alignment_groups.items():
        if len(group) != 2:
            continue
        effective_windows = {
            (provision.effective_from, provision.effective_until) for provision in group
        }
        if len(effective_windows) > 1:
            issues.append(
                f"Alignment group '{alignment_id}' has mismatched effective dates between EN and AR."
            )

    return issues


def validate_corpus(
    documents: list[LegalDocument],
    sources: list[LegalSource],
    provisions: list[LegalProvision],
    base_dir: Path | None = None,
) -> list[str]:
    """Composed entrypoint running all manifest, integrity, alignment, and temporal validation checks."""
    issues: list[str] = []

    document_ids = [document.document_id for document in documents]
    if len(document_ids) != len(set(document_ids)):
        issues.append("Corpus contains duplicate document_id values.")

    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        issues.append("Corpus contains duplicate source_id values.")

    document_map = {document.document_id: document for document in documents}
    for source in sources:
        if source.document_id not in document_map:
            issues.append(
                f"Source '{source.source_id}' references unknown document_id '{source.document_id}'."
            )
        elif source.source_id not in document_map[source.document_id].source_ids:
            issues.append(
                f"Source '{source.source_id}' is not declared by document '{source.document_id}'."
            )

    # 1. Manifest validation
    issues.extend(validate_source_manifest(sources, base_dir=base_dir))

    # 2. Provision integrity validation
    issues.extend(validate_provision_integrity(documents, sources, provisions))

    # 3. Bilingual alignment validation
    issues.extend(validate_bilingual_alignment(provisions))

    # 4. Temporal consistency validation
    issues.extend(validate_temporal_consistency(documents, provisions))

    # 5. Check orphan documents & sources
    used_doc_ids = {p.document_id for p in provisions}
    for doc in documents:
        if doc.document_id not in used_doc_ids:
            issues.append(f"Orphan document '{doc.document_id}' has no associated provisions.")

    used_src_ids = {p.source_id for p in provisions}
    for src in sources:
        if src.source_id not in used_src_ids:
            issues.append(f"Orphan source '{src.source_id}' has no associated provisions.")

    return issues
