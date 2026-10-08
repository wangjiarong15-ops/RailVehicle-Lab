"""AI analysis contracts and provider boundary (no vendor integrations)."""

from rail_vehicle.ai.context import (
    build_batch_comparison_payload,
    build_single_batch_payload,
)
from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.openai_provider import OpenAIResponsesProvider, create_configured_provider
from rail_vehicle.ai.provider import (
    AIAnalysisService,
    AIProviderUnavailable,
    FakeProvider,
    Provider,
    input_fingerprint,
    is_result_current,
)
from rail_vehicle.ai.schemas import (
    AIContractError,
    collect_evidence_ids,
    validate_input_payload,
    validate_output,
)

__all__ = [
    "AIAnalysisService",
    "AIConfig",
    "AIContractError",
    "AIProviderUnavailable",
    "FakeProvider",
    "OpenAIResponsesProvider",
    "Provider",
    "build_batch_comparison_payload",
    "build_single_batch_payload",
    "collect_evidence_ids",
    "create_configured_provider",
    "input_fingerprint",
    "is_result_current",
    "validate_input_payload",
    "validate_output",
]
