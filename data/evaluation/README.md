# UAE-LexAgent Evaluation Benchmark Suite

This directory contains the ground-truth legal evaluation dataset for the **Shubhi Signal** benchmark framework.

## Benchmark Methodology
1. **Isolated Evaluation**: This benchmark is strictly reserved for measuring performance and **must never be used in fine-tuning or training datasets**.
2. **Canonical Alignment References**: All provision expectations reference canonical `alignment_id` values (e.g., `FDL_33_2021_A9_C1`).
3. **Bilingual Symmetry**: Every bilingual query pair shares matching categories, legal conclusions, answerability criteria, and aligned provisions.
4. **Shubhi Signal Dimensions**:
   - **J (Jurisdiction)**: Onshore vs DIFC vs ADGM vs Unknown.
   - **R (Retrieval)**: Recall@K, HitRate@K, and MRR.
   - **G (Grounding)**: Claim-level entailment and citation validity.
   - **T (Tool Accuracy)**: Selection, argument matching, and calculation accuracy.
   - **B (Bilingual Consistency)**: English vs Arabic legal conclusion equivalence.
   - **A (Abstention Quality)**: Precision/Recall on unanswerable and adversarial cases.
   - **V (Temporal/Version)**: Correct `as_of` date filtering and version selection.
   - **L (Latency)**: P50 and P95 latency tracking.

## Category Taxonomy
| Category | Description | Target Count |
| :--- | :--- | :--- |
| `direct_factual` | Straightforward legal queries on single articles | 30 |
| `multi_hop` | Multi-provision reasoning scenarios | 25 |
| `code_switched` | Mixed Arabic and English legal inquiries | 25 |
| `tool_calculation` | Gratuity & entitlement calculations | 25 |
| `adversarial` | Inquiries citing false or non-existent articles | 20 |
| `out_of_scope` | Uncovered legal domains (domestic workers, criminal) | 20 |
| `jurisdiction_distractor` | Explicit DIFC/ADGM queries & free zones | 20 |
| `temporal_versioning` | Historical / pre-2022 scenarios with `as_of` dates | 15 |
| `citation_verification` | Exact claim-to-clause citation checks | 20 |
| **Total** | | **200** |
