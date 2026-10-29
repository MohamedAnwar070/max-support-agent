import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import get_settings
from .constants import (
    ACCOUNT_OR_ORDER_HISTORY_KEYWORDS,
    ACTION_TRIGGER_PHRASES,
    AFFIRMATIVE_FOLLOWUP_WORDS,
    BRAND_NAME,
    CANCEL_KEYWORDS,
    CODE_CONFLICT_VIP_FINAL_SALE,
    CODE_DUPLICATE,
    CODE_REQUEST_KEY_MISMATCH,
    CODE_TOOL_ERROR,
    CODE_UNAUTHORIZED,
    CUSTOMER_ID_PATTERN,
    DECISION_ACTION_COMPLETED,
    DECISION_ANSWER,
    DECISION_BLOCKED_INJECTION,
    DECISION_BLOCKED_UNAUTHORIZED,
    DECISION_CLARIFY,
    DECISION_DUPLICATE,
    DECISION_LOCKED,
    DECISION_ESCALATED_CONFLICT,
    DECISION_ESCALATED_EXCHANGE,
    DECISION_ESCALATED_TOOL_FAILURE,
    DECISION_ESCALATED_UNSAFE,
    EMAIL_PATTERN,
    EXCHANGE_KEYWORDS,
    FAREWELL_KEYWORDS,
    GREETING_KEYWORDS,
    HUMAN_ESCALATION_KEYWORDS,
    MAX_FAILED_VERIFICATIONS,
    ORDER_ID_PATTERN,
    ORDER_STATUS_PHRASES,
    POLICY_QUESTION_PHRASES,
    PRODUCT_INQUIRY_KEYWORDS,
    PURCHASE_INTENT_KEYWORDS,
    REQUEST_KEY_PATTERN,
    RETURN_KEYWORDS,
    STANDARD_RETURN_WINDOW_DAYS,
    STATUS_BLOCKED,
    STATUS_CONFLICT,
    STATUS_DENIED,
    STATUS_DUPLICATE,
    STATUS_ERROR,
    STATUS_SUCCESS,
)
from .data_store import DataStore
from .guardrails import (
    claims_success,
    detect_prompt_injection,
    detect_unsafe_request,
    extract_emails,
    find_ungrounded_tokens,
    leaks_system_prompt,
    scrub_unauthorized_pii,
)
from .llm_agent import CompletionBackend, LLMAgentRunner, LLMRunResult, collect_customer_facts
from .llm_backends import ClaudeCodeCLIBackend, OpenAIBackend
from .models import AgentResponse
from .tools import SupportToolkit


class CustomerSupportAgent:
    def __init__(
        self,
        store: Optional[DataStore] = None,
        backend: Optional[str] = None,
        use_live_llm: Optional[bool] = None,
        llm_backend: Optional[CompletionBackend] = None,
    ) -> None:
        settings = get_settings()
        self.store = store or DataStore()
        self.toolkit = SupportToolkit(self.store)
        self.openai_backend = OpenAIBackend()
        self.claude_backend = ClaudeCodeCLIBackend()

        self.backend_mode = (backend or settings.backend).lower()
        self.use_live_llm = settings.use_live_llm if use_live_llm is None else use_live_llm
        self.llm_backend: Optional[CompletionBackend] = llm_backend or self._select_llm_backend()
        self.failed_verifications = 0

        self.session_order_id: Optional[str] = None
        self.session_email: Optional[str] = None
        self.session_product_id: Optional[str] = None
        self.pending_intent: Optional[str] = None
        self.verified_customer: Optional[Dict[str, Any]] = None
        self.conversation_history: List[Dict[str, str]] = []

    def reset_session(self) -> None:
        self.session_order_id = None
        self.session_email = None
        self.session_product_id = None
        self.pending_intent = None
        self.verified_customer = None
        self.conversation_history = []
        self.toolkit.verified_emails.clear()
        self.failed_verifications = 0

    def _select_llm_backend(self) -> Optional[CompletionBackend]:
        if self.backend_mode == "openai" and self.openai_backend.is_available():
            return self.openai_backend
        if self.backend_mode in ("claude", "claude_code") and self.claude_backend.is_available():
            return self.claude_backend
        if self.use_live_llm:
            for candidate in (self.openai_backend, self.claude_backend):
                if candidate.is_available():
                    return candidate
        return None

    def verify_customer_session(
        self,
        email: str,
        order_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        # Knowing an email address is not proof of identity: require a matching Order ID as well.
        if not order_id:
            return False, "Verification requires both your email address and an Order ID on that account."
        if self.failed_verifications >= MAX_FAILED_VERIFICATIONS:
            return False, "Too many failed verification attempts. Please contact a human specialist."
        norm_ord = order_id.strip().upper()
        if not norm_ord.startswith("ORD-"):
            norm_ord = f"ORD-{norm_ord}"
        order = self.store.get_order_by_id(norm_ord)
        cust = self.store.get_customer_by_email(email)
        if not cust or not order or order["customer_email"].lower() != email.strip().lower():
            self.failed_verifications += 1
            return False, "Verification failed: could not verify that Order ID with that email address."
        self.session_order_id = order["order_id"]
        self.toolkit.verified_emails.add(cust["email"].lower())

        self.session_email = cust["email"].lower()
        self.verified_customer = cust
        orders = self.store.get_orders_by_email(cust["email"])
        order_ids = ", ".join(o["order_id"] for o in orders) or "None"
        return (
            True,
            f"Verified customer: {cust['name']} ({cust['customer_id']} | {cust['email']} | Tier: {cust['loyalty_tier']} | Orders: {order_ids})",
        )

    def _resolve_active_backend_label(self) -> str:
        if self.llm_backend is not None:
            return f"llm_agent:{self.llm_backend.label}"
        return "hybrid_sqlite_deterministic"

    @staticmethod
    def _extract_entities(
        message: str,
        context_email: Optional[str] = None,
        context_order_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        order_match = ORDER_ID_PATTERN.search(message)
        email_match = EMAIL_PATTERN.search(message)
        req_key_match = REQUEST_KEY_PATTERN.search(message)

        order_id: Optional[str] = None
        if order_match:
            digits_only = order_match.group(1)
            full_ord = order_match.group(2)
            order_id = f"ORD-{digits_only}" if digits_only else full_ord.upper()
        elif context_order_id:
            order_id = context_order_id.upper()

        email = (
            email_match.group(1).lower()
            if email_match
            else (context_email.lower() if context_email else None)
        )
        request_key = req_key_match.group(1).upper() if req_key_match else None
        return order_id, email, request_key

    @staticmethod
    def _classify_intent(
        message: str,
        pending_intent: Optional[str] = None,
        session_product_id: Optional[str] = None,
    ) -> str:
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", message)
        text = spaced.strip().lower()
        clean_punct = re.sub(r"[^\w\s]", "", text).strip()

        if any(clean_punct == f or clean_punct.startswith(f + " ") for f in FAREWELL_KEYWORDS):
            return "farewell"

        if any(p in text for p in HUMAN_ESCALATION_KEYWORDS):
            return "human_escalation"

        is_cancel = any(w in text for w in CANCEL_KEYWORDS)
        is_return = any(w in text for w in RETURN_KEYWORDS)
        is_exchange = any(w in text for w in EXCHANGE_KEYWORDS)

        is_policy_question = any(p in text for p in POLICY_QUESTION_PHRASES) and not any(
            p in text for p in ACTION_TRIGGER_PHRASES
        )

        if is_exchange and "refund" not in text:
            return "exchange_request"
        if is_cancel and not is_policy_question:
            return "cancel_order"
        if is_return and not is_policy_question:
            return "return_order"

        has_order_id = bool(ORDER_ID_PATTERN.search(message))
        has_email = bool(EMAIL_PATTERN.search(message))
        has_cust_id = bool(CUSTOMER_ID_PATTERN.search(message))

        if pending_intent and (has_order_id or has_email):
            return pending_intent

        if has_cust_id or any(k in text for k in ACCOUNT_OR_ORDER_HISTORY_KEYWORDS):
            return "order_status"

        if has_order_id or any(p in text for p in ORDER_STATUS_PHRASES):
            return "order_status"

        if any(p in text for p in PURCHASE_INTENT_KEYWORDS):
            return "purchase_inquiry"

        if session_product_id and (
            clean_punct in AFFIRMATIVE_FOLLOWUP_WORDS
            or any(clean_punct.startswith(w + " ") for w in AFFIRMATIVE_FOLLOWUP_WORDS)
        ):
            return "purchase_inquiry"

        mentions_product = any(re.search(rf"\b{re.escape(p)}", text) for p in PRODUCT_INQUIRY_KEYWORDS)
        if is_policy_question and mentions_product:
            return "combined_product_and_policy"

        if is_policy_question:
            return "policy_question"

        if mentions_product:
            return "product_question"

        if any(clean_punct == g or clean_punct.startswith(g + " ") for g in GREETING_KEYWORDS):
            return "greeting"

        return "unrecognized_or_general"

    def handle_message(
        self,
        user_message: str,
        customer_email: Optional[str] = None,
        order_id: Optional[str] = None,
        use_session_memory: bool = False,
    ) -> AgentResponse:
        self.toolkit.clear_turn_history()

        is_injected, injection_rule = detect_prompt_injection(user_message)
        if is_injected:
            reply = (
                f"I cannot fulfill instructions that attempt to override system rules, enter developer/admin mode, "
                f"expose customer records, or issue unverified refunds. I can only assist with legitimate "
                f"{BRAND_NAME} product, policy, and verified order inquiries."
            )
            return AgentResponse(
                reply=reply,
                decision_type=DECISION_BLOCKED_INJECTION,
                tools_called=[],
                escalated=False,
                escalation_ticket=None,
                blocked=True,
                blocked_reason=f"PROMPT_INJECTION:{injection_rule}",
                backend_used=self._resolve_active_backend_label(),
                metadata={"guardrail_triggered": injection_rule},
            )

        fallback_email = customer_email or (self.session_email if use_session_memory else None)
        fallback_order = order_id or (self.session_order_id if use_session_memory else None)
        extracted_order_id, extracted_email, extracted_req_key = self._extract_entities(
            user_message, fallback_email, fallback_order
        )

        is_unsafe, unsafe_rule = detect_unsafe_request(user_message)
        if is_unsafe:
            self.pending_intent = None
            self.toolkit.escalate_to_human(
                reason=f"Customer message triggered high-risk safety rule '{unsafe_rule}': {user_message}",
                category="unsafe_action",
                severity="high",
                order_id=extracted_order_id,
                customer_email=extracted_email,
            )
            ticket = self.toolkit.last_escalation
            ticket_id = ticket.ticket_id if ticket else "ESC-9001"
            draft = (
                f"Because your request involves `{unsafe_rule}`, I cannot resolve this automatically. "
                f"I have escalated your case to a Human Support Specialist under Ticket **{ticket_id}**."
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ESCALATED_UNSAFE,
                authenticated_email=extracted_email,
                escalated=True,
            )

        if self.failed_verifications >= MAX_FAILED_VERIFICATIONS and (extracted_order_id or extracted_email):
            return self._lockout_response(user_message)

        if self.llm_backend is not None:
            llm_response = self._handle_with_llm(user_message, customer_email, order_id, use_session_memory)
            if llm_response is not None:
                return llm_response

        intent = self._classify_intent(
            user_message,
            pending_intent=self.pending_intent if use_session_memory else None,
            session_product_id=self.session_product_id if use_session_memory else None,
        )

        if intent == "farewell":
            self.pending_intent = None
            draft = (
                f"Thank you for chatting with {BRAND_NAME} Customer Support! "
                "Please reach out anytime if you need help with a product, policy, or order."
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ANSWER,
                authenticated_email=extracted_email,
            )

        if intent == "human_escalation":
            self.pending_intent = None
            self.toolkit.escalate_to_human(
                reason=f"Customer explicitly requested a human specialist: {user_message}",
                category="customer_request",
                severity="medium",
                order_id=extracted_order_id,
                customer_email=extracted_email,
            )
            ticket = self.toolkit.last_escalation
            ticket_id = ticket.ticket_id if ticket else "ESC-9001"
            draft = (
                f"I have connected your conversation to a Human Support Specialist under Ticket "
                f"**{ticket_id}**. A team member will follow up with you shortly."
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ESCALATED_UNSAFE,
                authenticated_email=extracted_email,
                escalated=True,
            )

        if intent == "exchange_request":
            self.pending_intent = None
            ticket_id = self._escalate(
                reason=f"Exchange request (no automated exchange flow): {user_message}",
                category="exchange_request",
                severity="low",
                order_id=extracted_order_id,
                customer_email=extracted_email,
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=exchange_reply(ticket_id),
                decision_type=DECISION_ESCALATED_EXCHANGE,
                authenticated_email=extracted_email,
                escalated=True,
            )

        if intent == "greeting":
            draft = (
                f"Hello! I am the **{BRAND_NAME} Customer Support Agent**. I can help you with:\n"
                f"1. **Customer & Order Verification:** Share your **Order ID** (e.g., `ORD-1002`) and **email address** to track an order, cancel a processing order, or request a 7-day return & mock refund.\n"
                f"2. **Product Stock, Sizing & Care:** Ask about our *Classic Cotton T-Shirt* (`PROD-001`), *Metro Fleece Zip Hoodie* (`PROD-002`), chinos, sweaters, or clearance outerwear.\n"
                f"3. **Store Policies:** Ask about our **7-day return policy (`POL-RET-01`)**, Final Sale rules (`POL-RET-02`), or VIP Gold perks (`POL-VIP-01`).\n\n"
                f"How can I help you today?"
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ANSWER,
                authenticated_email=extracted_email,
            )

        if intent in ("return_order", "cancel_order"):
            action = "cancel" if intent == "cancel_order" else "return"

            if not extracted_order_id or not extracted_email:
                if use_session_memory:
                    self.pending_intent = intent
                    if extracted_order_id:
                        self.session_order_id = extracted_order_id
                    if extracted_email:
                        self.session_email = extracted_email
                missing_parts = []
                if not extracted_order_id:
                    missing_parts.append("your **Order ID** (e.g., `ORD-1001`)")
                if not extracted_email:
                    missing_parts.append("the **email address** used to place the order")
                missing_str = " and ".join(missing_parts)
                draft = (
                    f"To help you {action} your {BRAND_NAME} order and process a mock refund, I need a targeted clarification first: "
                    f"could you please provide {missing_str} so I can verify ownership and eligibility?"
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_CLARIFY,
                    authenticated_email=extracted_email,
                )

            if use_session_memory:
                self.pending_intent = None
                self.session_order_id = extracted_order_id
                self.session_email = extracted_email

            res = self.toolkit.process_return_or_cancel(
                order_id=extracted_order_id,
                customer_email=extracted_email,
                action=action,
                reason=user_message,
                request_key=extracted_req_key,
            )
            status = res.get("status")

            if status == STATUS_ERROR and res.get("code") == CODE_TOOL_ERROR:
                self.toolkit.escalate_to_human(
                    reason=f"SQLite database failure during {action} for {extracted_order_id}: {res.get('message')}",
                    category="tool_failure",
                    severity="high",
                    order_id=extracted_order_id,
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                ticket_id = ticket.ticket_id if ticket else "ESC-9001"
                draft = (
                    f"Your {action} and refund request for `{extracted_order_id}` **could not be verified** due to a temporary "
                    f"backend database outage (Error 503), so I cannot claim or confirm that the action succeeded. "
                    f"I have escalated your request to a Human Support Specialist under Ticket **{ticket_id}** to verify and complete it safely."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ESCALATED_TOOL_FAILURE,
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            if status == STATUS_BLOCKED and res.get("code") == CODE_REQUEST_KEY_MISMATCH:
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=request_key_mismatch_reply(extracted_order_id),
                    decision_type=DECISION_BLOCKED_UNAUTHORIZED,
                    authenticated_email=extracted_email,
                    blocked=True,
                    blocked_reason=CODE_REQUEST_KEY_MISMATCH,
                )

            if status == STATUS_BLOCKED and res.get("code") == CODE_UNAUTHORIZED:
                draft = verification_failed_reply(extracted_order_id, extracted_email)
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_BLOCKED_UNAUTHORIZED,
                    authenticated_email=extracted_email,
                    blocked=True,
                    blocked_reason=CODE_UNAUTHORIZED,
                )

            if status == STATUS_CONFLICT and res.get("code") == CODE_CONFLICT_VIP_FINAL_SALE:
                self.toolkit.lookup_policies(topic="final_sale")
                self.toolkit.escalate_to_human(
                    reason=(
                        f"Policy conflict CONF-VIP-FINALSALE on order {extracted_order_id} for VIP_GOLD customer "
                        f"{extracted_email}: POL-RET-02 conflicts with POL-VIP-01."
                    ),
                    category="policy_conflict",
                    severity="high",
                    order_id=extracted_order_id,
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                ticket_id = ticket.ticket_id if ticket else "ESC-9001"
                draft = (
                    f"I reviewed order `{extracted_order_id}` for the **Archive Distressed Denim Jacket (Final Sale)** "
                    f"under your `VIP_GOLD` membership and found a direct conflict between two store policies:\n"
                    f"- **Clearance & Final Sale Policy (`POL-RET-02`)** states that Final Sale items are strictly non-returnable with no exceptions for any tier.\n"
                    f"- **VIP Gold Loyalty Policy (`POL-VIP-01`)** states that `VIP_GOLD` members receive a 14-day return and refund guarantee on all purchases, including Final Sale items.\n\n"
                    f"Rather than guessing, I have escalated your request to a Human Support Specialist under Ticket **{ticket_id}** for resolution."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ESCALATED_CONFLICT,
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            if status == STATUS_DUPLICATE and res.get("code") == CODE_DUPLICATE:
                ref_id = res.get("reference_id")
                req_key = res.get("request_key")
                refund_amt = res.get("refund_amount", 0.0)
                draft = (
                    f"This {action} request for order `{extracted_order_id}` was already processed under unique request key "
                    f"**`{req_key}`**. Returning the original action result without repeating the action: "
                    f"Confirmation Reference **{ref_id}** (Mock Refund: **${refund_amt:.2f}**)."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_DUPLICATE,
                    authenticated_email=extracted_email,
                )

            if status == STATUS_DENIED:
                draft = f"I am unable to {action} order `{extracted_order_id}`: {res.get('message')}"
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ANSWER,
                    authenticated_email=extracted_email,
                )

            if status == STATUS_ERROR:
                draft = f"I could not find order `{extracted_order_id}` in our system. Please double-check the Order ID."
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ANSWER,
                    authenticated_email=extracted_email,
                )

            ref_id = res.get("reference_id")
            req_key = res.get("request_key")
            refund = res.get("refund_amount", 0.0)
            draft = (
                f"Your {action} and mock refund for order `{extracted_order_id}` have been processed! "
                f"Reference ID: **{ref_id}** | Unique Request Key: **`{req_key}`** | "
                f"Mock Refund Amount: **${refund:.2f}** (credited to your original payment method within 3–5 business days)."
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ACTION_COMPLETED,
                authenticated_email=extracted_email,
            )

        if intent == "order_status":
            if not extracted_order_id or not extracted_email:
                if use_session_memory:
                    self.pending_intent = "order_status"
                    if extracted_order_id:
                        self.session_order_id = extracted_order_id
                    if extracted_email:
                        self.session_email = extracted_email
                missing_parts = []
                if not extracted_order_id:
                    missing_parts.append("your **Order ID** (e.g., `ORD-1001`)")
                if not extracted_email:
                    missing_parts.append("your **email address**")
                draft = (
                    f"To protect customer privacy, I must verify your customer account before sharing order or purchase details. "
                    f"Could you please provide {' and '.join(missing_parts)}?"
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_CLARIFY,
                    authenticated_email=extracted_email,
                )

            if use_session_memory:
                self.pending_intent = None

            res = self.toolkit.get_order_status(order_id=extracted_order_id, customer_email=extracted_email)
            status = res.get("status")

            if status == STATUS_ERROR and res.get("code") == CODE_TOOL_ERROR:
                self.toolkit.escalate_to_human(
                    reason=f"SQLite error looking up order {extracted_order_id}: {res.get('message')}",
                    category="tool_failure",
                    severity="high",
                    order_id=extracted_order_id,
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                ticket_id = ticket.ticket_id if ticket else "ESC-9001"
                draft = (
                    f"The status for order `{extracted_order_id}` **could not be verified** due to a temporary database outage (Error 503). "
                    f"I have escalated your inquiry to a Human Support Specialist under Ticket **{ticket_id}**."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ESCALATED_TOOL_FAILURE,
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            if status == STATUS_BLOCKED and res.get("code") == CODE_UNAUTHORIZED:
                draft = verification_failed_reply(extracted_order_id, extracted_email)
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_BLOCKED_UNAUTHORIZED,
                    authenticated_email=extracted_email,
                    blocked=True,
                    blocked_reason=CODE_UNAUTHORIZED,
                )

            if status == STATUS_ERROR:
                draft = f"I could not find any order matching `{extracted_order_id}`. Please check the Order ID and try again."
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ANSWER,
                    authenticated_email=extracted_email,
                )

            if use_session_memory and status == STATUS_SUCCESS:
                self.session_order_id = extracted_order_id
                self.session_email = extracted_email

            order_data = res["order"]
            items_summary = ", ".join(f"{i['name']} (Size {i['size']})" for i in order_data["items"])
            tracking_info = (
                f"Tracking Number: `{order_data['tracking_number']}`"
                if order_data.get("tracking_number")
                else "Tracking number will be assigned once shipped"
            )
            if order_data.get("delivery_date"):
                delivery_note = f"Delivered on {order_data['delivery_date']} to {order_data['shipping_city']}."
            elif order_data.get("estimated_delivery"):
                delivery_note = f"Estimated delivery: {order_data['estimated_delivery']} to {order_data['shipping_city']}."
            else:
                delivery_note = f"Destination: {order_data['shipping_city']}."

            draft = (
                f"Here is the status for order **{order_data['order_id']}**:\n"
                f"- **Status:** `{order_data['status']}`\n"
                f"- **Items:** {items_summary} (Total: ${order_data['total_amount']:.2f})\n"
                f"- **Tracking:** {tracking_info}\n"
                f"- **Delivery:** {delivery_note}"
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ANSWER,
                authenticated_email=extracted_email,
            )

        if intent == "combined_product_and_policy":
            prod_res = self.toolkit.lookup_products(query=user_message)
            pol_res = self.toolkit.lookup_policies(topic="all")
            if prod_res.get("status") == STATUS_ERROR or pol_res.get("status") == STATUS_ERROR:
                self.toolkit.escalate_to_human(
                    reason="Database error during combined product and policy lookup.",
                    category="tool_failure",
                    severity="medium",
                )
                ticket = self.toolkit.last_escalation
                ticket_id = ticket.ticket_id if ticket else "ESC-9001"
                draft = (
                    f"Your inquiry **could not be verified** due to a temporary database outage. "
                    f"I have escalated your request to a Human Specialist under Ticket **{ticket_id}**."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ESCALATED_TOOL_FAILURE,
                    authenticated_email=extracted_email,
                    escalated=True,
                )
            products = prod_res.get("products", [])
            policies = pol_res.get("policies", [])
            prod_summary = "\n".join(
                f"- **{p['name']} (`{p['product_id']}`)** — **${p['price']:.2f}** (Sizes: {', '.join(p['sizes_in_stock'])})"
                for p in products
            )
            pol_summary = "\n".join(f"- **{p['title']} (`{p['policy_id']}`):** {p['rule']}" for p in policies)
            draft = f"**Official {BRAND_NAME} Policies:**\n{pol_summary}"
            if prod_summary:
                draft = f"**{BRAND_NAME} Products:**\n{prod_summary}\n\n" + draft
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ANSWER,
                authenticated_email=extracted_email,
            )

        if intent == "policy_question":
            text_lower = user_message.lower()
            topic = "all"
            if "shipping" in text_lower or "delivery" in text_lower:
                topic = "shipping"
            elif "cancel" in text_lower:
                topic = "cancellations"
            elif "vip" in text_lower or "final sale" in text_lower:
                topic = "all"
            elif "return" in text_lower or "exchange" in text_lower or "refund" in text_lower:
                topic = "returns"

            res = self.toolkit.lookup_policies(topic=topic)
            if res.get("status") == STATUS_ERROR:
                self.toolkit.escalate_to_human(
                    reason=f"Tool error looking up policy '{topic}': {res.get('message')}",
                    category="tool_failure",
                    severity="medium",
                )
                ticket = self.toolkit.last_escalation
                ticket_id = ticket.ticket_id if ticket else "ESC-9001"
                draft = (
                    f"Policy details **could not be verified** because our policy database is temporarily unavailable. "
                    f"I have escalated your question to a Human Specialist under Ticket **{ticket_id}**."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ESCALATED_TOOL_FAILURE,
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            if ("vip" in text_lower and "final sale" in text_lower) or ("vip" in text_lower and "clearance" in text_lower):
                self.toolkit.escalate_to_human(
                    reason="Customer asked about conflicting policies POL-RET-02 and POL-VIP-01.",
                    category="policy_conflict",
                    severity="medium",
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                ticket_id = ticket.ticket_id if ticket else "ESC-9001"
                draft = (
                    "There is a direct conflict between two of our official policies regarding Final Sale items for VIP Gold members:\n"
                    "- **`POL-RET-02` (Clearance & Final Sale Policy):** States Final Sale items are strictly non-returnable with no exceptions for any tier.\n"
                    "- **`POL-VIP-01` (VIP Gold Guarantee):** States VIP Gold members receive a 14-day return and refund window on all items, including Final Sale items.\n\n"
                    f"Rather than guessing, I have escalated your inquiry to a Human Support Specialist under Ticket **{ticket_id}**."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ESCALATED_CONFLICT,
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            policies = res.get("policies", [])
            bullet_lines = [f"- **{p['title']} (`{p['policy_id']}`):** {p['rule']}" for p in policies]
            draft = f"Here are the official {BRAND_NAME} policies for your inquiry:\n" + "\n".join(bullet_lines)
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ANSWER,
                authenticated_email=extracted_email,
            )

        if intent in ("product_question", "purchase_inquiry"):
            query_to_use = user_message
            if intent == "purchase_inquiry" and use_session_memory and self.session_product_id:
                if not any(k in user_message.lower() for k in ("shirt", "hoodie", "jacket", "chino", "pants", "sweater")):
                    query_to_use = self.session_product_id

            res = self.toolkit.lookup_products(query=query_to_use)
            if res.get("status") == STATUS_ERROR:
                self.toolkit.escalate_to_human(
                    reason=f"Catalog lookup error: {res.get('message')}",
                    category="tool_failure",
                    severity="medium",
                )
                ticket = self.toolkit.last_escalation
                ticket_id = ticket.ticket_id if ticket else "ESC-9001"
                draft = (
                    f"Product availability **could not be verified** because our catalog database is temporarily unavailable. "
                    f"I have created Escalation Ticket **{ticket_id}** for a specialist to assist you."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type=DECISION_ESCALATED_TOOL_FAILURE,
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            products = res.get("products", [])
            if use_session_memory and len(products) == 1:
                self.session_product_id = products[0]["product_id"]

            product_lines = []
            for p in products:
                sale_tag = (
                    " **[FINAL SALE - Non-Returnable per POL-RET-02]**"
                    if p.get("final_sale")
                    else f" *(Standard {STANDARD_RETURN_WINDOW_DAYS}-day return eligible per POL-RET-01)*"
                )
                in_stock_str = ", ".join(p["sizes_in_stock"])
                out_stock_str = ", ".join(p["sizes_out_of_stock"]) if p["sizes_out_of_stock"] else "None"
                product_lines.append(
                    f"- **{p['name']} (`{p['product_id']}`)** — **${p['price']:.2f}**{sale_tag}\n"
                    f"  - *Sizes in stock:* {in_stock_str} (Out of stock: {out_stock_str})\n"
                    f"  - *Fit Guide:* {p['fit_guide']}\n"
                    f"  - *Fabric & Care:* {p['fabric_and_care']}"
                )

            checkout_note = (
                "\n\n*(Note: As a Customer Support Agent, I can verify stock and sizing, while new purchases are completed directly on the UrbanThread storefront checkout.)*"
                if intent == "purchase_inquiry"
                else ""
            )
            draft = f"Here are the matching {BRAND_NAME} product details:\n" + "\n".join(product_lines) + checkout_note
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type=DECISION_ANSWER,
                authenticated_email=extracted_email,
            )

        draft = (
            f"I'm the **{BRAND_NAME} Customer Support Agent**. To make sure I give you the right information, "
            f"could you clarify what you need help with?\n"
            f"- **For an existing order (tracking, return/refund, or cancellation):** Please share your **Order ID** (e.g., `ORD-1001`) and the **email address** on the order so I can verify your account first.\n"
            f"- **For product or policy questions:** Ask about a specific item (like our *Classic Cotton T-Shirt* or *Metro Fleece Zip Hoodie*) or our **7-day return policy (`POL-RET-01`)**."
        )
        return self._finalize_response(
            user_message=user_message,
            draft_reply=draft,
            decision_type=DECISION_CLARIFY,
            authenticated_email=extracted_email,
        )

    def _escalate(
        self,
        reason: str,
        category: str,
        severity: str,
        order_id: Optional[str] = None,
        customer_email: Optional[str] = None,
    ) -> str:
        self.toolkit.escalate_to_human(
            reason=reason, category=category, severity=severity, order_id=order_id, customer_email=customer_email
        )
        ticket = self.toolkit.last_escalation
        return ticket.ticket_id if ticket else "ESC-9001"

    def _lockout_response(self, user_message: str) -> AgentResponse:
        ticket_id = self._escalate(
            reason=f"{self.failed_verifications} failed order verifications in one session; further lookups locked.",
            category="unsafe_action",
            severity="high",
        )
        return self._finalize_response(
            user_message=user_message,
            draft_reply=(
                "For account security I can't look up or change orders in this conversation after several failed verification "
                f"attempts. A Human Support Specialist will verify your identity under Ticket **{ticket_id}**."
            ),
            decision_type=DECISION_LOCKED,
            authenticated_email=None,
            escalated=True,
            blocked=True,
            blocked_reason="VERIFICATION_LOCKOUT",
        )

    def _prior_user_messages(self, use_session_memory: bool) -> List[str]:
        if not use_session_memory:
            return []
        return [t["content"] for t in self.conversation_history if t["role"] == "user"][-6:]

    def _handle_with_llm(
        self,
        user_message: str,
        customer_email: Optional[str],
        order_id: Optional[str],
        use_session_memory: bool,
    ) -> Optional[AgentResponse]:
        # A session identity only counts once it has passed an ownership check.
        verified_session = use_session_memory and self.session_email in self.toolkit.verified_emails
        facts = collect_customer_facts(
            current_message=user_message,
            prior_user_messages=self._prior_user_messages(use_session_memory),
            explicit_email=customer_email or (self.session_email if verified_session else None),
            explicit_order_id=order_id or (self.session_order_id if verified_session else None),
        )
        transcript = self.conversation_history[-8:] if use_session_memory else []
        run = LLMAgentRunner(self.llm_backend, self.toolkit).run(facts, transcript)
        if run is None:
            return None
        return self._finalize_llm_turn(user_message, run, transcript, facts.order_ids)

    def _finalize_llm_turn(
        self,
        user_message: str,
        run: LLMRunResult,
        transcript: List[Dict[str, str]],
        customer_order_ids: Set[str],
    ) -> Optional[AgentResponse]:
        records = self.toolkit.call_history
        by_code = {r.output.get("code"): r for r in records}
        escalated_categories = [r.arguments.get("category") for r in records if r.tool_name == "escalate_to_human"]
        mutation = next((r for r in records if r.tool_name == "process_return_or_cancel"), None)
        lookup = next((r for r in records if r.tool_name == "get_order_status"), None)
        order_id = (mutation or lookup).arguments.get("order_id") if (mutation or lookup) else None
        email = (mutation or lookup).arguments.get("customer_email") if (mutation or lookup) else None
        if not order_id and len(customer_order_ids) == 1:
            order_id = next(iter(customer_order_ids))

        # Mandatory escalations are enforced in code even when the LLM skips them.
        if CODE_TOOL_ERROR in by_code and "tool_failure" not in escalated_categories:
            self._escalate(
                f"Backend tool failure during agent turn: {by_code[CODE_TOOL_ERROR].output.get('message')}",
                "tool_failure", "high", order_id, email,
            )
        if CODE_CONFLICT_VIP_FINAL_SALE in by_code and "policy_conflict" not in escalated_categories:
            self._escalate(
                f"Policy conflict POL-RET-02 vs POL-VIP-01 on {order_id}.", "policy_conflict", "high", order_id, email
            )

        ticket = self.toolkit.last_escalation
        ticket_id = ticket.ticket_id if ticket else None
        action = (mutation.arguments.get("action") if mutation else None) or "return"
        out = mutation.output if mutation else {}

        # The decision comes from what the tools actually did, never from the LLM's own claim.
        blocked, blocked_reason, required = False, None, []
        if CODE_TOOL_ERROR in by_code:
            decision, required = DECISION_ESCALATED_TOOL_FAILURE, ["could not be verified", ticket_id]
            safe = tool_failure_reply(action if mutation else "request", order_id, ticket_id)
        elif CODE_CONFLICT_VIP_FINAL_SALE in by_code or "policy_conflict" in escalated_categories:
            decision, required = DECISION_ESCALATED_CONFLICT, ["POL-RET-02", "POL-VIP-01", ticket_id]
            safe = conflict_reply(order_id, ticket_id)
        elif CODE_REQUEST_KEY_MISMATCH in by_code:
            decision, blocked, blocked_reason = DECISION_BLOCKED_UNAUTHORIZED, True, CODE_REQUEST_KEY_MISMATCH
            safe = request_key_mismatch_reply(order_id)
        elif CODE_UNAUTHORIZED in by_code:
            decision, blocked, blocked_reason = DECISION_BLOCKED_UNAUTHORIZED, True, CODE_UNAUTHORIZED
            blocked_args = by_code[CODE_UNAUTHORIZED].arguments
            safe = verification_failed_reply(blocked_args.get("order_id"), blocked_args.get("customer_email"))
        elif mutation and mutation.status == STATUS_DUPLICATE:
            decision, required = DECISION_DUPLICATE, [out.get("reference_id"), out.get("request_key")]
            safe = duplicate_reply(action, order_id, out)
        elif mutation and mutation.status == STATUS_SUCCESS:
            decision, required = DECISION_ACTION_COMPLETED, [out.get("reference_id"), out.get("request_key")]
            safe = action_completed_reply(action, order_id, out)
        elif ticket_id:
            is_exchange = ticket is not None and ticket.category == "exchange_request"
            decision = DECISION_ESCALATED_EXCHANGE if is_exchange else DECISION_ESCALATED_UNSAFE
            required = [ticket_id]
            safe = exchange_reply(ticket_id) if is_exchange else (
                f"I have escalated your request to a Human Support Specialist under Ticket **{ticket_id}**."
            )
        elif mutation and mutation.status == STATUS_DENIED:
            decision, required = DECISION_ANSWER, [out.get("policy_id")]
            safe = f"I am unable to {action} order `{order_id}`: {out.get('message')}"
        else:
            decision = DECISION_CLARIFY if run.llm_decision.startswith("clarif") else DECISION_ANSWER
            safe = None

        # Blocked outcomes never use LLM wording: the fixed text is the only thing guaranteed not to hint.
        if blocked:
            run.reply = None

        grounding = json.dumps([r.to_dict() for r in records], default=str) + "\n".join(
            [user_message] + [t["content"] for t in transcript]
        )
        reply, rejection = run.reply, None
        ungrounded = find_ungrounded_tokens(reply or "", grounding)
        if not reply:
            rejection = "empty_or_withheld_reply"
        elif leaks_system_prompt(reply):
            rejection = "system_prompt_leak"
        elif ungrounded:
            rejection = f"ungrounded_tokens:{ungrounded}"
        elif claims_success(reply) and decision not in (DECISION_ACTION_COMPLETED, DECISION_DUPLICATE):
            rejection = "unverified_success_claim"
        elif any(tok and tok.lower() not in reply.lower() for tok in required):
            rejection = "missing_required_facts"

        if rejection:
            if safe is None:
                if records:
                    # Read-only lookups only: the deterministic router answers from the same data.
                    return None
                decision = DECISION_CLARIFY
                safe = (
                    "Could you tell me a bit more about what you need: a product, a store policy, "
                    "or an existing order (with its Order ID and email)?"
                )
            reply = safe

        return self._finalize_response(
            user_message=user_message,
            draft_reply=reply,
            decision_type=decision,
            authenticated_email=None,
            escalated=self.toolkit.last_escalation is not None,
            blocked=blocked,
            blocked_reason=blocked_reason,
            extra_metadata={
                "llm_steps": run.steps,
                "llm_trace": run.trace,
                "gate_rejections": run.gate_rejections,
                "llm_reply_rejected": rejection,
            },
        )

    def _finalize_response(
        self,
        user_message: str,
        draft_reply: str,
        decision_type: str,
        authenticated_email: Optional[str],
        escalated: bool = False,
        blocked: bool = False,
        blocked_reason: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentResponse:
        # authenticated_email is only what the customer claimed; PII is unlocked solely by emails
        # that passed an order-ownership check (toolkit.verified_emails).
        safe_reply, pii_scrubbed = scrub_unauthorized_pii(
            reply_text=draft_reply,
            verified_emails=self.toolkit.verified_emails,
            all_orders=self.store.orders,
            all_customers=self.store.customers,
            user_supplied_emails=extract_emails([user_message]),
        )
        if any(r.output.get("code") == CODE_UNAUTHORIZED for r in self.toolkit.call_history):
            self.failed_verifications += 1
        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": safe_reply})
        return AgentResponse(
            reply=safe_reply,
            decision_type=decision_type,
            tools_called=list(self.toolkit.call_history),
            escalated=escalated,
            escalation_ticket=self.toolkit.last_escalation,
            blocked=blocked,
            blocked_reason=blocked_reason,
            backend_used=self._resolve_active_backend_label(),
            metadata={"pii_scrubbed": pii_scrubbed, **(extra_metadata or {})},
        )


def verification_failed_reply(order_id: Optional[str], email: Optional[str]) -> str:
    # Identical whether the order is missing or owned by someone else, so order IDs cannot be enumerated.
    return (
        f"I couldn't verify order `{order_id}` with the email address `{email}`, so I can't share or change anything on it. "
        "Please double-check both the Order ID and the email used at checkout."
    )


def request_key_mismatch_reply(order_id: Optional[str]) -> str:
    return (
        f"The request key you supplied doesn't belong to this request for order `{order_id}`, so I took no action. "
        "Please resend the request without a request key, or with the key from your original confirmation."
    )


def tool_failure_reply(action: str, order_id: Optional[str], ticket_id: Optional[str]) -> str:
    target = f" for `{order_id}`" if order_id else ""
    return (
        f"Your {action}{target} **could not be verified** because of a temporary database outage (Error 503), so I cannot "
        f"confirm that anything succeeded. I have escalated it to a Human Support Specialist under Ticket **{ticket_id}**."
    )


def conflict_reply(order_id: Optional[str], ticket_id: Optional[str]) -> str:
    return (
        f"Order `{order_id}` falls under two store policies that directly conflict:\n"
        "- **`POL-RET-02` (Clearance & Final Sale):** Final Sale items are non-returnable with no exceptions for any tier.\n"
        "- **`POL-VIP-01` (VIP Gold Guarantee):** VIP Gold members get a 14-day return guarantee, including Final Sale items.\n\n"
        f"Rather than guessing, I have escalated your request to a Human Support Specialist under Ticket **{ticket_id}**."
    )


def duplicate_reply(action: str, order_id: Optional[str], out: Dict[str, Any]) -> str:
    return (
        f"This {action} request for order `{order_id}` was already processed under request key **`{out.get('request_key')}`**. "
        f"Returning the original result without repeating the action: Reference **{out.get('reference_id')}** "
        f"(Mock Refund: **${float(out.get('refund_amount') or 0):.2f}**)."
    )


def action_completed_reply(action: str, order_id: Optional[str], out: Dict[str, Any]) -> str:
    return (
        f"Your {action} and mock refund for order `{order_id}` have been processed. Reference ID: **{out.get('reference_id')}** | "
        f"Request Key: **`{out.get('request_key')}`** | Mock Refund: **${float(out.get('refund_amount') or 0):.2f}** "
        "(credited to your original payment method within 3-5 business days)."
    )


def exchange_reply(ticket_id: Optional[str]) -> str:
    return (
        "I can't process size or item exchanges automatically, and I won't turn an exchange into a refund unless you ask. "
        f"I've passed your exchange request to a Human Support Specialist under Ticket **{ticket_id}**. "
        "If you'd prefer a refund instead, just ask for a return."
    )
