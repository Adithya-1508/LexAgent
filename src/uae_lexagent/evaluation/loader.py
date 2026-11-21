"""Benchmark loader and dataset integrity validation module."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from ..models import (
    BenchmarkCase,
    BenchmarkCategory,
    BenchmarkManifest,
    Language,
    LegalProvision,
)

MIN_CATEGORY_COUNTS: dict[BenchmarkCategory, int] = {
    BenchmarkCategory.DIRECT_FACTUAL: 30,
    BenchmarkCategory.MULTI_HOP: 25,
    BenchmarkCategory.CODE_SWITCHED: 25,
    BenchmarkCategory.TOOL_CALCULATION: 25,
    BenchmarkCategory.ADVERSARIAL: 20,
    BenchmarkCategory.OUT_OF_SCOPE: 20,
    BenchmarkCategory.JURISDICTION_DISTRACTOR: 20,
    BenchmarkCategory.TEMPORAL_VERSIONING: 15,
    BenchmarkCategory.CITATION_VERIFICATION: 20,
}


def load_benchmark_cases(path: Path) -> list[BenchmarkCase]:
    """Load benchmark test cases from JSONL."""
    cases: list[BenchmarkCase] = []
    with path.open("r", encoding="utf-8") as file:
        for line_num, line in enumerate(file, 1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                case = BenchmarkCase.model_validate(data)
                cases.append(case)
            except Exception as exc:
                raise ValueError(f"Error parsing benchmark case at line {line_num}: {exc}") from exc
    return cases


def load_benchmark_manifest(path: Path) -> BenchmarkManifest:
    """Load and validate benchmark manifest metadata."""
    with path.open("r", encoding="utf-8") as file:
        return BenchmarkManifest.model_validate(json.load(file))


def compute_corpus_fingerprint(provisions: list[LegalProvision]) -> str:
    """Compute a stable fingerprint from canonical provision records."""
    records = [provision.model_dump(mode="json") for provision in provisions]
    records.sort(key=lambda record: record["provision_id"])
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def validate_benchmark_manifest(
    manifest: BenchmarkManifest,
    benchmark_path: Path,
    cases: list[BenchmarkCase],
    provisions: list[LegalProvision],
    corpus_version: str,
) -> list[str]:
    """Verify dataset hash, counts, distribution, and corpus fingerprint."""
    issues: list[str] = []
    raw_bytes = benchmark_path.read_bytes()
    actual_hash_raw = hashlib.sha256(raw_bytes).hexdigest()
    actual_hash_lf = hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n")).hexdigest()
    manifest_hash = manifest.dataset_sha256.casefold()
    if manifest_hash not in {actual_hash_raw.casefold(), actual_hash_lf.casefold()}:
        issues.append("Benchmark dataset SHA256 does not match its manifest.")
    if manifest.total_cases != len(cases):
        issues.append(f"Manifest declares {manifest.total_cases} cases but loaded {len(cases)}.")
    if manifest.corpus_version != corpus_version:
        issues.append(
            f"Manifest corpus version '{manifest.corpus_version}' does not match '{corpus_version}'."
        )
    if manifest.corpus_fingerprint:
        actual_fingerprint = compute_corpus_fingerprint(provisions)
        if actual_fingerprint.casefold() != manifest.corpus_fingerprint.casefold():
            issues.append("Benchmark corpus fingerprint does not match the loaded provisions.")

    actual_distribution = Counter(case.category.value for case in cases)
    if dict(actual_distribution) != manifest.category_distribution:
        issues.append("Benchmark category distribution does not match its manifest.")
    return issues


def validate_benchmark_integrity(
    cases: list[BenchmarkCase],
    provisions: list[LegalProvision],
    corpus_version: str,
) -> list[str]:
    """Verify integrity, bilingual symmetry, corpus linkage, and category distribution."""
    issues: list[str] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    valid_alignment_ids = {p.alignment_id for p in provisions if p.alignment_id}
    alignment_languages: dict[str, set[str]] = {}
    for provision in provisions:
        if provision.alignment_id:
            alignment_languages.setdefault(provision.alignment_id, set()).add(provision.language)
    bilingual_pairs: dict[str, list[BenchmarkCase]] = {}
    category_counts: Counter[BenchmarkCategory] = Counter()

    for case in cases:
        # 1. Unique case_id check
        if case.case_id in seen_ids:
            issues.append(f"Duplicate case_id found: '{case.case_id}'")
        seen_ids.add(case.case_id)

        # 2. Duplicate query check (within same language)
        query_key = f"{case.language.value}:{case.query.strip().casefold()}"
        if query_key in seen_queries:
            issues.append(f"Duplicate query detected in case '{case.case_id}'")
        seen_queries.add(query_key)

        # 3. Corpus version check
        if case.expected_corpus_version != corpus_version:
            issues.append(
                f"Case '{case.case_id}' expects corpus version '{case.expected_corpus_version}', "
                f"expected '{corpus_version}'"
            )

        # 4. Canonical alignment_id validation against loaded provisions
        for align_id in case.expected_provisions:
            if align_id not in valid_alignment_ids:
                issues.append(
                    f"Case '{case.case_id}' references unknown alignment_id '{align_id}' "
                    f"not found in corpus provisions"
                )
            elif alignment_languages.get(align_id) != {"ar", "en"}:
                issues.append(
                    f"Case '{case.case_id}' references alignment_id '{align_id}' without an EN/AR pair."
                )

        # 5. Answerability & abstention rules
        if not case.expected_answerable:
            if not case.abstention_reason:
                issues.append(f"Unanswerable case '{case.case_id}' must specify abstention_reason")
            if case.expected_provisions:
                issues.append(
                    f"Unanswerable case '{case.case_id}' should not cite expected provisions"
                )

        # 6. Category-specific requirements
        if case.category == BenchmarkCategory.TEMPORAL_VERSIONING and case.as_of is None:
            issues.append(f"Temporal case '{case.case_id}' must specify as_of date")

        if case.category == BenchmarkCategory.ADVERSARIAL and not case.adversarial_type:
            issues.append(f"Adversarial case '{case.case_id}' must specify adversarial_type")

        # 7. Collect bilingual pairs
        if case.bilingual_pair_id:
            bilingual_pairs.setdefault(case.bilingual_pair_id, []).append(case)

        category_counts[case.category] += 1

    # 8. Bilingual pair symmetry check
    for pair_id, pair_cases in bilingual_pairs.items():
        en_cases = [c for c in pair_cases if c.language == Language.EN]
        ar_cases = [c for c in pair_cases if c.language == Language.AR]

        if len(en_cases) != 1 or len(ar_cases) != 1:
            issues.append(
                f"Bilingual pair '{pair_id}' must have exactly 1 EN and 1 AR case, "
                f"found {len(en_cases)} EN and {len(ar_cases)} AR"
            )
            continue

        c_en, c_ar = en_cases[0], ar_cases[0]
        if c_en.category != c_ar.category:
            issues.append(
                f"Bilingual pair '{pair_id}' category mismatch: {c_en.category} vs {c_ar.category}"
            )
        if c_en.expected_jurisdiction != c_ar.expected_jurisdiction:
            issues.append(f"Bilingual pair '{pair_id}' jurisdiction mismatch")
        if c_en.expected_answerable != c_ar.expected_answerable:
            issues.append(f"Bilingual pair '{pair_id}' answerability mismatch")
        if c_en.expected_provisions != c_ar.expected_provisions:
            issues.append(f"Bilingual pair '{pair_id}' expected provisions mismatch")
        if c_en.as_of != c_ar.as_of:
            issues.append(f"Bilingual pair '{pair_id}' as_of date mismatch")
        if c_en.expected_corpus_version != c_ar.expected_corpus_version:
            issues.append(f"Bilingual pair '{pair_id}' corpus-version mismatch")
        if {claim.claim_id for claim in c_en.expected_claims} != {
            claim.claim_id for claim in c_ar.expected_claims
        }:
            issues.append(f"Bilingual pair '{pair_id}' claim-id mismatch")
        if (c_en.expected_tool is None) != (c_ar.expected_tool is None):
            issues.append(f"Bilingual pair '{pair_id}' tool expectation mismatch")
        if c_en.expected_answerable and c_en.expected_tool and c_ar.expected_tool:
            if c_en.expected_tool.tool_name != c_ar.expected_tool.tool_name:
                issues.append(f"Bilingual pair '{pair_id}' tool-name mismatch")
            if c_en.expected_tool.calculation != c_ar.expected_tool.calculation:
                issues.append(f"Bilingual pair '{pair_id}' calculation expectation mismatch")
        if c_en.adversarial_type != c_ar.adversarial_type:
            issues.append(f"Bilingual pair '{pair_id}' adversarial-type mismatch")

    # 9. Minimum category count check
    for cat, min_count in MIN_CATEGORY_COUNTS.items():
        actual_count = category_counts.get(cat, 0)
        if actual_count < min_count:
            issues.append(
                f"Category '{cat.value}' has {actual_count} cases, minimum required is {min_count}"
            )

    return issues
