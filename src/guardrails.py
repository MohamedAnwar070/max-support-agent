import re
from typing import Any, Dict, List, Optional, Tuple

from .constants import INJECTION_PATTERNS, UNSAFE_OR_HIGH_RISK_PATTERNS


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


def scrub_unauthorized_pii(
    reply_text: str,
    authenticated_email: Optional[str],
    all_orders: List[Dict[str, Any]],
    all_customers: List[Dict[str, Any]],
) -> Tuple[str, bool]:
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
