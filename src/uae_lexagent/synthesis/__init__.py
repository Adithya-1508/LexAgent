"""Citation-grounded legal synthesis and verification package for UAE-LexAgent."""

from .contracts import (
    DraftLegalClaim,
    GeneratedLegalAnswer,
    GeneratorProtocol,
    GroundedLegalAnswer,
    VerificationStatus,
    VerifiedLegalClaim,
)
from .engine import LegalSynthesisEngine
from .facts import LegalFact, LegalQuantity, extract_legal_facts
from .generators import (
    DeterministicTemplateGenerator,
    LLMGenerator,
    LLMProvider,
    get_generator,
)
from .policy import AnswerabilityDecision, evaluate_answerability
from .verifier import CitationVerifier

__all__ = [
    "AnswerabilityDecision",
    "CitationVerifier",
    "DeterministicTemplateGenerator",
    "DraftLegalClaim",
    "GeneratedLegalAnswer",
    "GeneratorProtocol",
    "GroundedLegalAnswer",
    "LLMGenerator",
    "LLMProvider",
    "LegalFact",
    "LegalQuantity",
    "LegalSynthesisEngine",
    "VerificationStatus",
    "VerifiedLegalClaim",
    "evaluate_answerability",
    "extract_legal_facts",
    "get_generator",
]
