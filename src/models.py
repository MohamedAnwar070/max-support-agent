"""Data models and structured response schemas for the Customer Support Agent."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation made by the agent."""
    tool_name: str
    arguments: Dict[str, Any]
    status: str  # "success", "error", "blocked", "conflict", "duplicate", "missing_info"
    output: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EscalationTicket:
    """Structured human-handoff ticket created when an issue is escalated."""
    ticket_id: str
    category: str  # "policy_conflict", "tool_failure", "unsafe_action", "customer_request"
    severity: str  # "high", "medium", "low"
    reason: str
    order_id: Optional[str] = None
    customer_email: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentResponse:
    """Complete structured response from a single turn of the support agent."""
    reply: str
    decision_type: str
    # Canonical decision_type values:
    # - "answer"
    # - "clarify_missing_info"
    # - "action_completed"
    # - "escalated_policy_conflict"
    # - "escalated_tool_failure"
    # - "escalated_unsafe_or_complex"
    # - "blocked_unauthorized"
    # - "blocked_prompt_injection"
    # - "duplicate_request_handled"
    tools_called: List[ToolCallRecord] = field(default_factory=list)
    escalated: bool = False
    escalation_ticket: Optional[EscalationTicket] = None
    blocked: bool = False
    blocked_reason: Optional[str] = None
    backend_used: str = "hybrid"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "decision_type": self.decision_type,
            "tools_called": [tc.to_dict() for tc in self.tools_called],
            "escalated": self.escalated,
            "escalation_ticket": self.escalation_ticket.to_dict() if self.escalation_ticket else None,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "backend_used": self.backend_used,
            "metadata": self.metadata,
        }

