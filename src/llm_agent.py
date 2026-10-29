import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Set

from .constants import (
    AGENT_PLANNER_SYSTEM_PROMPT,
    CANCEL_KEYWORDS,
    CODE_GATE_REJECTED,
    EXCHANGE_KEYWORDS,
    MAX_AGENT_STEPS,
    ORDER_ID_PATTERN,
    REQUEST_KEY_PATTERN,
    RETURN_KEYWORDS,
)
from .tools import SupportToolkit

ALLOWED_TOOLS = (
    "lookup_products",
    "lookup_policies",
    "get_order_status",
    "process_return_or_cancel",
    "escalate_to_human",
)
IDENTITY_TOOLS = ("get_order_status", "process_return_or_cancel")
MAX_TOOL_OUTPUT_CHARS = 4000
MAX_ESCALATIONS_PER_TURN = 1
MAX_MUTATIONS_PER_TURN = 1


class CompletionBackend(Protocol):
    label: str

    def is_available(self) -> bool: ...

    def complete(self, system_prompt: str, user_prompt: str) -> Optional[str]: ...


def normalize_order_id(raw: str) -> str:
    match = ORDER_ID_PATTERN.search(raw or "")
    if not match:
        return (raw or "").strip().upper()
    return f"ORD-{match.group(1)}" if match.group(1) else match.group(2).upper()


def extract_order_ids(text: str) -> Set[str]:
    found = set()
    for m in ORDER_ID_PATTERN.finditer(text or ""):
        found.add(f"ORD-{m.group(1)}" if m.group(1) else m.group(2).upper())
    return found


def requested_actions(text: str) -> Set[str]:
    """Mutations the customer explicitly asked for. An exchange alone never authorises a refund."""
    lower = (text or "").lower()
    actions: Set[str] = set()
    wants_exchange = any(k in lower for k in EXCHANGE_KEYWORDS)
    if any(k in lower for k in RETURN_KEYWORDS) and not (wants_exchange and "refund" not in lower):
        actions.add("return")
    if any(k in lower for k in CANCEL_KEYWORDS):
        actions.add("cancel")
    return actions


@dataclass
class CustomerFacts:
    """What the customer has actually said. The LLM may only act on identifiers in here."""

    current_message: str
    order_ids: Set[str] = field(default_factory=set)
    emails: Set[str] = field(default_factory=set)
    request_keys: Set[str] = field(default_factory=set)
    actions: Set[str] = field(default_factory=set)


@dataclass
class LLMRunResult:
    reply: Optional[str]
    llm_decision: str
    steps: int
    gate_rejections: List[Dict[str, Any]]
    trace: List[Dict[str, Any]]


class ToolPolicyGate:
    """Deterministic checks between the LLM and the tools. The LLM proposes, the gate disposes."""

    def __init__(self, facts: CustomerFacts) -> None:
        self.facts = facts
        self.mutations = 0
        self.escalations = 0

    def review(self, tool: str, args: Dict[str, Any]) -> Optional[str]:
        """Return a rejection reason, or None after sanitising args in place."""
        if tool not in ALLOWED_TOOLS:
            return f"Unknown tool '{tool}'."

        if tool in IDENTITY_TOOLS:
            order_id = normalize_order_id(str(args.get("order_id", "")))
            email = str(args.get("customer_email", "")).strip().lower()
            if not order_id or order_id not in self.facts.order_ids:
                return "order_id was not provided by the customer. Ask the customer for their Order ID."
            if not email or email not in self.facts.emails:
                return "customer_email was not provided by the customer. Ask the customer for the email on the order."
            args["order_id"], args["customer_email"] = order_id, email

        if tool == "process_return_or_cancel":
            action = str(args.get("action", "")).strip().lower()
            if action not in self.facts.actions:
                return (
                    f"The customer has not explicitly requested a '{action}'. Do not mutate orders without an explicit "
                    "request; exchanges must be escalated with category exchange_request."
                )
            if self.mutations >= MAX_MUTATIONS_PER_TURN:
                return "Only one order mutation is allowed per turn."
            key = str(args.get("request_key") or "").strip().upper()
            if key and key not in self.facts.request_keys:
                args.pop("request_key", None)
            args["action"] = action
            self.mutations += 1

        if tool == "escalate_to_human":
            if self.escalations >= MAX_ESCALATIONS_PER_TURN:
                return "An escalation ticket was already created this turn; reuse its ticket ID."
            if args.get("order_id") and normalize_order_id(str(args["order_id"])) not in self.facts.order_ids:
                args.pop("order_id")
            if args.get("customer_email") and str(args["customer_email"]).strip().lower() not in self.facts.emails:
                args.pop("customer_email")
            self.escalations += 1

        return None


def _neutralize(text: str) -> str:
    # Customer text is wrapped in tags; strip angle brackets so it cannot close or forge them.
    return (text or "").replace("<", "‹").replace(">", "›")


def parse_agent_json(raw: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("action") not in ("call_tool", "final"):
        return None
    if data["action"] == "call_tool" and not isinstance(data.get("arguments", {}), dict):
        return None
    return data


class LLMAgentRunner:
    def __init__(self, backend: CompletionBackend, toolkit: SupportToolkit, max_steps: int = MAX_AGENT_STEPS) -> None:
        self.backend = backend
        self.toolkit = toolkit
        self.max_steps = max_steps

    def _build_prompt(
        self,
        facts: CustomerFacts,
        transcript: List[Dict[str, str]],
        observations: List[Dict[str, Any]],
        note: Optional[str],
    ) -> str:
        history = "\n".join(f"{t['role']}: {_neutralize(t['content'])}" for t in transcript) or "(none)"
        obs_lines = []
        for i, o in enumerate(observations, 1):
            result = json.dumps(o["result"], default=str)[:MAX_TOOL_OUTPUT_CHARS]
            obs_lines.append(f"{i}. {o['tool']}({json.dumps(o['arguments'], default=str)}) -> {result}")
        prompt = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            f"<customer_message>\n{_neutralize(facts.current_message)}\n</customer_message>\n\n"
            "Identifiers the customer has provided (the only ones you may use): "
            f"order_ids={sorted(facts.order_ids)}, emails={sorted(facts.emails)}, request_keys={sorted(facts.request_keys)}\n\n"
            "Tool results so far this turn:\n" + ("\n".join(obs_lines) or "(none)") + "\n\n"
        )
        if note:
            prompt += f"NOTE: {note}\n\n"
        return prompt + "Respond with exactly one JSON object."

    def run(self, facts: CustomerFacts, transcript: List[Dict[str, str]]) -> Optional[LLMRunResult]:
        """Returns None when the backend produced nothing usable before any tool ran (caller falls back)."""
        gate = ToolPolicyGate(facts)
        observations: List[Dict[str, Any]] = []
        rejections: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []
        note: Optional[str] = None
        invalid_outputs = 0

        for step in range(1, self.max_steps + 1):
            raw = self.backend.complete(AGENT_PLANNER_SYSTEM_PROMPT, self._build_prompt(facts, transcript, observations, note))
            note = None
            data = parse_agent_json(raw) if raw else None
            if data is None:
                invalid_outputs += 1
                trace.append({"step": step, "invalid_output": (raw or "")[:300]})
                if raw is None or invalid_outputs > 1:
                    break
                note = "Your previous output was not a single valid JSON object in the required format."
                continue

            trace.append({"step": step, "thought": str(data.get("thought", ""))[:300], "action": data["action"], "tool": data.get("tool")})
            if data["action"] == "final":
                return LLMRunResult(
                    reply=str(data.get("reply") or "").strip() or None,
                    llm_decision=str(data.get("decision") or "answer").lower(),
                    steps=step,
                    gate_rejections=rejections,
                    trace=trace,
                )

            tool = str(data.get("tool", ""))
            args = dict(data.get("arguments") or {})
            reason = gate.review(tool, args)
            if reason:
                result: Dict[str, Any] = {"status": "rejected", "code": CODE_GATE_REJECTED, "message": reason}
                rejections.append({"tool": tool, "arguments": args, "reason": reason})
            else:
                result = self.toolkit.execute(tool, args)
            observations.append({"tool": tool, "arguments": args, "result": result})

        if not self.toolkit.call_history and not rejections:
            return None
        return LLMRunResult(reply=None, llm_decision="answer", steps=len(trace), gate_rejections=rejections, trace=trace)


def collect_customer_facts(
    current_message: str,
    prior_user_messages: List[str],
    explicit_email: Optional[str],
    explicit_order_id: Optional[str],
) -> CustomerFacts:
    from .guardrails import extract_emails

    texts = prior_user_messages + [current_message]
    facts = CustomerFacts(current_message=current_message)
    for t in texts:
        facts.order_ids |= extract_order_ids(t)
        facts.request_keys |= {k.upper() for k in REQUEST_KEY_PATTERN.findall(t)}
    facts.emails = extract_emails(texts)
    if explicit_email:
        facts.emails.add(explicit_email.strip().lower())
    if explicit_order_id:
        facts.order_ids.add(normalize_order_id(explicit_order_id))

    facts.actions = requested_actions(current_message)
    if not facts.actions and prior_user_messages:
        # A follow-up such as "ORD-1005, marcus@example.com" continues the most recent request.
        facts.actions = requested_actions(prior_user_messages[-1])
    return facts
