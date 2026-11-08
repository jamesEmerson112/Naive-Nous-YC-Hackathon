"""Shared device <-> cloud contracts for Romulus (contract v0.1, draft).

The authority is docs/cloud-platform-planning/cloud-platform-apis.md §2-3; these models
must match it. Device-sent models keep unknown fields (`extra="allow"`).
"""

from romulus_contracts.agentrun import AgentRunAccepted, AgentRunReport
from romulus_contracts.codepatch import summarize_code_patch, touches_safety
from romulus_contracts.common import SHA256_HEADER, ErrorResponse, Running
from romulus_contracts.device import Assigned, CheckIn, CheckInResponse, InstallResult
from romulus_contracts.doctor import (
    ConsultRequest,
    DoctorAdvice,
    DoctorAsk,
    DoctorContext,
    Hypothesis,
    Proposal,
    ProposalBase,
    ProposalPointer,
    ProposalResult,
    proposal_body_and_sha256,
)
from romulus_contracts.incident import (
    Agent,
    IncidentAccepted,
    IncidentReport,
    Outcome,
    Overlay,
    OverlayStatus,
)
from romulus_contracts.manifest import (
    FirmwareManifest,
    FirmwareResponse,
    HarnessMeta,
    Policy,
    firmware_response,
    manifest_sha256,
)
from romulus_contracts.rules import (
    Fired,
    ReplayReport,
    RulesError,
    evaluate,
    replay_passes,
    replay_report,
    validate_rules,
)
from romulus_contracts.telemetry import Episode, RecordingAccepted, RecordingKind, RecordingMeta
from romulus_contracts.tools import (
    TOOL_CATALOG,
    ToolCall,
    ToolResult,
    ToolSpec,
    ToolTier,
    tool,
    validate_args,
)
from romulus_contracts.version import CONTRACT_VERSION, SUPPORTED_VERSIONS, is_supported

__all__ = [
    "CONTRACT_VERSION",
    "SHA256_HEADER",
    "SUPPORTED_VERSIONS",
    "TOOL_CATALOG",
    "Agent",
    "AgentRunAccepted",
    "AgentRunReport",
    "Assigned",
    "CheckIn",
    "CheckInResponse",
    "ConsultRequest",
    "DoctorAdvice",
    "DoctorAsk",
    "DoctorContext",
    "Episode",
    "ErrorResponse",
    "FirmwareManifest",
    "FirmwareResponse",
    "Fired",
    "HarnessMeta",
    "Hypothesis",
    "IncidentAccepted",
    "IncidentReport",
    "InstallResult",
    "Outcome",
    "Overlay",
    "OverlayStatus",
    "Policy",
    "Proposal",
    "ProposalBase",
    "ProposalPointer",
    "ProposalResult",
    "RecordingAccepted",
    "RecordingKind",
    "RecordingMeta",
    "ReplayReport",
    "Running",
    "RulesError",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "ToolTier",
    "evaluate",
    "firmware_response",
    "is_supported",
    "manifest_sha256",
    "proposal_body_and_sha256",
    "replay_passes",
    "replay_report",
    "summarize_code_patch",
    "tool",
    "touches_safety",
    "validate_args",
    "validate_rules",
]
