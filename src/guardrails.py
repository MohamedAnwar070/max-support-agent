"""Security and safety guardrails for prompt injection, unsafe actions, and PII protection."""

import re
from typing import Any, Dict, List, Optional, Tuple


# Patterns indicative of prompt injection, jailbreaking, or unauthorized privilege escalation
INJECTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (
        "instruction_override",
        re.compile(
            r"(ignore|disregard|forget|override)\s+(all\s+)?(previous|prior|above|system|your)\s+(instructions|rules|prompts|policies|guardrails)",
            re.IGNORECASE,
        ),
    ),
    (
        "persona_hijack",
        re.compile(
            r"(you\s+are\s+now|act\s+as|switch\s+to|enter)\s+(developer\s+mode|admin\s+mode|adminbot|dan\b|unrestricted|god\s+mode|root)",
            re.IGNORECASE,
        ),
    ),
    (
        "data_exfiltration",
        re.compile(
            r"(dump|list|output|print|export|show\s+me)\s+(all\s+)?(customer\s+emails|all\s+orders|all\s+customers|database|system\s+prompt|hidden\s+instructions|api\s+keys)",
            re.IGNORECASE,
        ),
    ),
    (
        "verification_bypass",
        re.compile(
            r"(without\s+checking|bypass\s+verification|skip\s+authentication|issue\s+a\s+\$?\d+\s+refund\s+immediately|force\s+approve\s+refund)",
            re.IGNORECASE,
        ),
    ),
]


# Unsafe or out-of-scope requests requiring human escalation
UNSAFE_OR_HIGH_RISK_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (
        "chargeback_or_legal_threat",
        re.compile(r"\b(lawyer|lawsuit|sue\s+you|legal\s+action|fraud\s+department)\b", re.IGNORECASE),
    ),
    (
        "manual_cash_override",
        re.compile(r"\b(wire\s+transfer|send\s+cash|venmo\s+me|crypto\s+refund|change\s+credit\s+card\s+number)\b", re.IGNORECASE),
    ),
]


def detect_prompt_injection(user_message: str) -> Tuple[bool, Optional[str]]:
    """
    Scan incoming user message for prompt injection or adversarial jailbreak attempts.

    Returns:
        (is_injected, matched_rule_name)
    """
    for rule_name, pattern in INJECTION_PATTERNS:
        if pattern.search(user_message):
            return True, rule_name
    return False, None


def detect_unsafe_request(user_message: str) -> Tuple[bool, Optional[str]]:
    """
    Scan incoming user message for unsafe/out-of-scope financial or legal demands.

    Returns:
        (is_unsafe, matched_rule_name)
    """
    for rule_name, pattern in UNSAFE_OR_HIGH_RISK_PATTERNS:
        if pattern.search(user_message):
            return True, rule_name
    return False, None


def scrub_unauthorized_pii(
    reply_text: str,
    authenticated_email: Optional[str],
    all_orders: List[Dict[str, Any]],
    all_customers: List[Dict[str, Any]],
) -> Tuple[str, bool]:
    """
    Defense-in-depth output filter: ensures no other customer's email, tracking number,
    or phone number is ever leaked in the agent's text response.

    Returns:
        (sanitized_reply, leak_detected)
    """
    auth_norm = authenticated_email.strip().lower() if authenticated_email else None
    leak_detected = False
    sanitized = reply_text

    for customer in all_customers:
        c_email = customer["email"].lower()
        if c_email != auth_norm:
            if c_email in sanitized.lower():
                leak_detected = True
                sanitized = re.sub(re.escape(customer["email"]), "[REDACTED_EMAIL]", sanitized, flags=re.IGNORECASE)
            if customer.get("phone") and customer["phone"] in sanitized:
                leak_detected = True
                sanitized = sanitized.replace(customer["phone"], "[REDACTED_PHONE]")

    for order in all_orders:
        o_email = order["customer_email"].lower()
        if o_email != auth_norm:
            tracking = order.get("tracking_number")
            if tracking and tracking in sanitized:
                leak_detected = True
                sanitized = sanitized.replace(tracking, "[REDACTED_TRACKING]")

    return sanitized, leak_detected

