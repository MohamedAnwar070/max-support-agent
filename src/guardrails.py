import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .constants import EMAIL_PATTERN, INJECTION_PATTERNS, UNSAFE_OR_HIGH_RISK_PATTERNS

# Identifiers and amounts the agent must never invent: every one in an LLM reply has to be grounded
# in a tool result or in something the customer typed.
GROUNDED_TOKEN_PATTERN = re.compile(
    r"\b(?:ORD|REF|CAN|ESC|POL|PROD|CUST)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b|\bREQ-[A-Z0-9-]+\b|\b1Z[0-9A-Z]{8,}\b|\$\d+(?:\.\d{2})?",
    re.IGNORECASE,
)
SUCCESS_CLAIM_PATTERN = re.compile(
    r"\b(has|have)\s+been\s+(processed|cancell?ed|refunded|issued|approved|completed)\b"
    r"|\bsuccessfully\s+(processed|cancell?ed|refunded|issued|approved|completed)\b"
    r"|\bi(?:'ve|\s+have)\s+(processed|cancell?ed|refunded|issued|approved)\b"
    r"|\brefund\s+(was|is)\s+(issued|approved|on\s+its\s+way)\b",
    re.IGNORECASE,
)
PROMPT_LEAK_MARKERS = (
    "RESPONSE FORMAT",
    "are DATA, not instructions",
    "STRICT OPERATING RULES",
    "AGENT_PLANNER_SYSTEM_PROMPT",
)


def detect_prompt_injection(user_message: str) -> Tuple[bool, Optional[str]]:
    for rule_name, pattern in INJECTION_PATTERNS:
        if pattern.search(user_message):
            return True, rule_name
    return False, None


def detect_unsafe_request(user_message: str) -> Tuple[bool, Optional[str]]:
    for rule_name, pattern in UNSAFE_OR_HIGH_RISK_PATTERNS:
        if pattern.search(user_message):
            return True, rule_name
    return False, None


def _redact(text: str, needle: Optional[str], label: str) -> Tuple[str, bool]:
    if not needle or len(needle) < 3:
        return text, False
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    if not pattern.search(text):
        return text, False
    return pattern.sub(label, text), True


def scrub_unauthorized_pii(
    reply_text: str,
    verified_emails: Iterable[str],
    all_orders: List[Dict[str, Any]],
    all_customers: List[Dict[str, Any]],
    user_supplied_emails: Iterable[str] = (),
) -> Tuple[str, bool]:
    """Redact PII of every customer whose identity was not verified by an order-ownership check.

    An email the customer typed themselves may be echoed back (it reveals nothing new), but names,
    phones, customer IDs, cities and tracking numbers are shown only to verified owners.
    """
    verified = {e.strip().lower() for e in verified_emails if e}
    echoed = {e.strip().lower() for e in user_supplied_emails if e}
    leak_detected = False
    sanitized = reply_text

    for customer in all_customers:
        c_email = customer["email"].lower()
        if c_email in verified:
            continue
        if c_email not in echoed:
            sanitized, hit = _redact(sanitized, customer["email"], "[REDACTED_EMAIL]")
            leak_detected |= hit
        for value, label in (
            (customer.get("phone"), "[REDACTED_PHONE]"),
            (customer.get("name"), "[REDACTED_NAME]"),
            (customer.get("customer_id"), "[REDACTED_CUSTOMER_ID]"),
        ):
            sanitized, hit = _redact(sanitized, value, label)
            leak_detected |= hit

    for order in all_orders:
        if order["customer_email"].lower() in verified:
            continue
        for value, label in (
            (order.get("tracking_number"), "[REDACTED_TRACKING]"),
            (order.get("shipping_city"), "[REDACTED_CITY]"),
        ):
            sanitized, hit = _redact(sanitized, value, label)
            leak_detected |= hit

    return sanitized, leak_detected


def find_ungrounded_tokens(reply_text: str, grounding_text: str) -> List[str]:
    grounding = grounding_text.upper()
    ungrounded = []
    for token in GROUNDED_TOKEN_PATTERN.findall(reply_text):
        norm = token.upper()
        if norm.startswith("$"):
            amount = norm[1:]
            candidates = {amount, f"{float(amount):.2f}", f"{float(amount):.1f}", str(float(amount))}
            if not any(c in grounding for c in candidates):
                ungrounded.append(token)
        elif norm not in grounding:
            ungrounded.append(token)
    return ungrounded


def claims_success(reply_text: str) -> bool:
    return bool(SUCCESS_CLAIM_PATTERN.search(reply_text))


def leaks_system_prompt(reply_text: str) -> bool:
    return any(marker.lower() in reply_text.lower() for marker in PROMPT_LEAK_MARKERS)


def extract_emails(texts: Iterable[str]) -> Set[str]:
    found: Set[str] = set()
    for t in texts:
        found.update(m.lower() for m in EMAIL_PATTERN.findall(t or ""))
    return found
