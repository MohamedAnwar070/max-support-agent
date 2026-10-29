from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .constants import DEFAULT_BACKEND


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: Dict[str, Any]
    status: str
    output: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EscalationTicket:
    ticket_id: str
    category: str
    severity: str
    reason: str
    order_id: Optional[str] = None
    customer_email: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentResponse:
    reply: str
    decision_type: str
    tools_called: List[ToolCallRecord] = field(default_factory=list)
    escalated: bool = False
    escalation_ticket: Optional[EscalationTicket] = None
    blocked: bool = False
    blocked_reason: Optional[str] = None
    backend_used: str = DEFAULT_BACKEND
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
