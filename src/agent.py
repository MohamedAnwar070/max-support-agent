"""Core CustomerSupportAgent orchestrating guardrails, deterministic tools, and LLM synthesis."""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .data_store import DataStore
from .guardrails import (
    detect_prompt_injection,
    detect_unsafe_request,
    scrub_unauthorized_pii,
)
from .llm_backends import ClaudeCodeCLIBackend, OpenAIBackend
from .models import AgentResponse
from .tools import SupportToolkit


ORDER_ID_PATTERN = re.compile(r"\b(ORD-\d{3,6})\b", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b")


class CustomerSupportAgent:
    """
    Production-grade Apparel Customer Support Agent.

    Supports three LLM execution backends:
      - "openai": Uses OpenAI Chat Completions API (`OPENAI_API_KEY`)
      - "claude_code": Uses locally authenticated Claude Code Pro CLI (`claude -p`)
      - "hybrid" / "auto": Uses deterministic tool routing + optional LLM polishing
    """

    def __init__(
        self,
        store: Optional[DataStore] = None,
        backend: Optional[str] = None,
        use_live_llm: bool = False,
    ) -> None:
        self.store = store or DataStore()
        self.toolkit = SupportToolkit(self.store)
        self.openai_backend = OpenAIBackend()
        self.claude_backend = ClaudeCodeCLIBackend()

        env_backend = os.environ.get("SUPPORT_AGENT_BACKEND", "hybrid").strip().lower()
        self.backend_mode = (backend or env_backend).lower()
        self.use_live_llm = use_live_llm or (
            os.environ.get("USE_LIVE_LLM", "0").strip().lower() in ("1", "true", "yes")
        )

    def _resolve_active_backend_label(self) -> str:
        if self.backend_mode == "openai" and self.openai_backend.is_available():
            return f"openai:{self.openai_backend.model}"
        if self.backend_mode in ("claude", "claude_code") and self.claude_backend.is_available():
            return "claude_code_pro_cli"
        if self.use_live_llm:
            if self.openai_backend.is_available():
                return f"openai:{self.openai_backend.model}"
            if self.claude_backend.is_available():
                return "claude_code_pro_cli"
        return "hybrid_deterministic"

    @staticmethod
    def _extract_entities(
        message: str,
        context_email: Optional[str] = None,
        context_order_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        order_match = ORDER_ID_PATTERN.search(message)
        email_match = EMAIL_PATTERN.search(message)

        order_id = order_match.group(1).upper() if order_match else (context_order_id.upper() if context_order_id else None)
        email = email_match.group(1).lower() if email_match else (context_email.lower() if context_email else None)
        return order_id, email

    @staticmethod
    def _classify_intent(message: str) -> str:
        text = message.lower()

        # Check explicit human escalation request
        if any(p in text for p in ("speak to a human", "talk to a human", "human agent", "real person", "supervisor")):
            return "human_escalation"

        # Check order mutation intents (return / exchange / cancel)
        is_cancel = any(w in text for w in ("cancel", "cancellation"))
        is_return = any(w in text for w in ("return", "refund", "send back", "exchange", "doesn't fit", "does not fit"))

        # Distinguish general policy questions ("what is your return policy?") from action requests ("I want to return...")
        is_policy_question = any(
            p in text
            for p in (
                "what is your",
                "what's your",
                "what are the",
                "policy",
                "policies",
                "how many days",
                "return window",
                "shipping cost",
                "how long does shipping",
            )
        ) and not any(p in text for p in ("i want to", "please return", "please cancel", "return my", "cancel my", "ord-"))

        if is_cancel and not is_policy_question:
            return "cancel_order"
        if is_return and not is_policy_question:
            return "return_order"

        # Check order tracking / status intent
        if any(
            p in text
            for p in (
                "where is my order",
                "order status",
                "track my order",
                "tracking number",
                "status of order",
                "check order",
                "when will my order",
                "ord-",
            )
        ):
            return "order_status"

        if is_policy_question:
            return "policy_question"

        # Check product inquiry
        if any(
            p in text
            for p in (
                "hoodie",
                "jacket",
                "blazer",
                "leggings",
                "denim",
                "jeans",
                "merino",
                "velvet",
                "aerostride",
                "cloudknit",
                "flexcore",
                "selvedge",
                "prod-",
                "in stock",
                "size",
                "sizing",
                "price",
                "how much",
                "wash",
                "care",
                "catalog",
                "products",
            )
        ):
            return "product_question"

        return "general_inquiry"

    def _polish_with_llm_if_enabled(
        self,
        user_message: str,
        decision_type: str,
        draft_reply: str,
    ) -> Tuple[str, str]:
        """Optionally refine the grounded draft reply via OpenAI API or Claude Code Pro CLI."""
        tool_dicts = [tc.to_dict() for tc in self.toolkit.call_history]
        backend_label = self._resolve_active_backend_label()

        if backend_label.startswith("openai:"):
            reply = self.openai_backend.generate_grounded_reply(
                user_message=user_message,
                tool_records=tool_dicts,
                decision_type=decision_type,
                fallback_reply=draft_reply,
            )
            return reply, backend_label

        if backend_label == "claude_code_pro_cli":
            reply = self.claude_backend.generate_grounded_reply(
                user_message=user_message,
                tool_records=tool_dicts,
                decision_type=decision_type,
                fallback_reply=draft_reply,
            )
            return reply, backend_label

        return draft_reply, backend_label

    def handle_message(
        self,
        user_message: str,
        customer_email: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> AgentResponse:
        """
        Process a customer support message end-to-end with guardrails, tools, and verification.
        """
        self.toolkit.clear_turn_history()

        # ------------------------------------------------------------------
        # 1. Pre-Execution Guardrail: Prompt Injection Detection (Failure #4)
        # ------------------------------------------------------------------
        is_injected, injection_rule = detect_prompt_injection(user_message)
        if is_injected:
            reply = (
                "I cannot fulfill instructions to override system rules, enter developer/admin mode, "
                "expose customer records, or issue unverified refunds. I can only assist with legitimate "
                "MaxWear Apparel product, policy, and verified order inquiries."
            )
            return AgentResponse(
                reply=reply,
                decision_type="blocked_prompt_injection",
                tools_called=[],
                escalated=False,
                escalation_ticket=None,
                blocked=True,
                blocked_reason=f"PROMPT_INJECTION:{injection_rule}",
                backend_used=self._resolve_active_backend_label(),
                metadata={"guardrail_triggered": injection_rule},
            )

        # ------------------------------------------------------------------
        # 2. Pre-Execution Guardrail: Unsafe / High-Risk Action Escalation
        # ------------------------------------------------------------------
        extracted_order_id, extracted_email = self._extract_entities(user_message, customer_email, order_id)
        is_unsafe, unsafe_rule = detect_unsafe_request(user_message)
        if is_unsafe:
            esc = self.toolkit.escalate_to_human(
                reason=f"Customer message triggered high-risk safety rule '{unsafe_rule}': {user_message}",
                category="unsafe_action",
                severity="high",
                order_id=extracted_order_id,
                customer_email=extracted_email,
            )
            ticket = self.toolkit.last_escalation
            draft = (
                f"For your security and protection, I am unable to process this request through automated chat. "
                f"I have escalated your case to a Senior Human Support Specialist under Ticket {ticket.ticket_id if ticket else 'ESC-9001'}."
            )
            return AgentResponse(
                reply=draft,
                decision_type="escalated_unsafe_or_complex",
                tools_called=list(self.toolkit.call_history),
                escalated=True,
                escalation_ticket=ticket,
                blocked=False,
                backend_used=self._resolve_active_backend_label(),
            )

        # ------------------------------------------------------------------
        # 3. Intent Classification & Tool Execution
        # ------------------------------------------------------------------
        intent = self._classify_intent(user_message)

        if intent == "human_escalation":
            self.toolkit.escalate_to_human(
                reason=f"Customer explicitly requested a human specialist: {user_message}",
                category="customer_request",
                severity="medium",
                order_id=extracted_order_id,
                customer_email=extracted_email,
            )
            ticket = self.toolkit.last_escalation
            draft = (
                f"I have connected your conversation to a Human Support Specialist under Ticket "
                f"{ticket.ticket_id if ticket else 'ESC-9001'}. A team member will follow up with you shortly."
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type="escalated_unsafe_or_complex",
                authenticated_email=extracted_email,
                escalated=True,
            )

        # ------------------------------------------------------------------
        # 4. Handle Order Return / Cancellation Intents
        # ------------------------------------------------------------------
        if intent in ("return_order", "cancel_order"):
            action = "cancel" if intent == "cancel_order" else "return"

            # Failure Mode #1: Missing Information -> Clarify before calling action tools!
            if not extracted_order_id or not extracted_email:
                missing_parts = []
                if not extracted_order_id:
                    missing_parts.append("your **Order ID** (e.g., `ORD-1001`)")
                if not extracted_email:
                    missing_parts.append("the **email address** used to place the order")
                missing_str = " and ".join(missing_parts)
                draft = (
                    f"I would be happy to help you {action} your order, but I need a bit more information first. "
                    f"Could you please provide {missing_str} so I can verify your account and check eligibility?"
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="clarify_missing_info",
                    authenticated_email=extracted_email,
                )

            # Execute return or cancellation tool
            res = self.toolkit.process_return_or_cancel(
                order_id=extracted_order_id,
                customer_email=extracted_email,
                action=action,
                reason=user_message,
            )
            status = res.get("status")

            # Failure Mode #5: Tool Failure -> Escalate to Human!
            if status == "error" and res.get("code") == "TOOL_EXECUTION_ERROR":
                self.toolkit.escalate_to_human(
                    reason=f"Backend database failure during {action} for {extracted_order_id}: {res.get('message')}",
                    category="tool_failure",
                    severity="high",
                    order_id=extracted_order_id,
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                draft = (
                    f"I apologize, but our order fulfillment system is currently experiencing a temporary service outage "
                    f"(Error 503) and I cannot complete or verify your {action} request for `{extracted_order_id}` right now. "
                    f"To ensure this is handled promptly without any delay to your eligibility window, I have escalated your request "
                    f"to a Human Support Specialist under Ticket **{ticket.ticket_id if ticket else 'ESC-9001'}**."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="escalated_tool_failure",
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            # Failure Mode #3: Unauthorized Order Access -> Block!
            if status == "blocked" and res.get("code") == "UNAUTHORIZED_ORDER_ACCESS":
                draft = (
                    f"I cannot view or {action} order `{extracted_order_id}` because the email address `{extracted_email}` "
                    f"does not match the customer email associated with that order. For security and privacy reasons, "
                    f"no order details or changes can be processed."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="blocked_unauthorized",
                    authenticated_email=extracted_email,
                    blocked=True,
                    blocked_reason="UNAUTHORIZED_ORDER_ACCESS",
                )

            # Failure Mode #2: Conflicting Policies -> Lookup Policy & Escalate to Human!
            if status == "conflict" and res.get("code") == "POLICY_CONFLICT_VIP_FINAL_SALE":
                self.toolkit.lookup_policies(topic="final_sale")
                self.toolkit.escalate_to_human(
                    reason=(
                        f"Policy conflict CONF-VIP-FINALSALE on order {extracted_order_id} for VIP_GOLD customer "
                        f"{extracted_email}: POL-RET-02 (Final Sale strictly non-returnable) conflicts with "
                        f"POL-VIP-01 (VIP Gold 60-day return guarantee includes Final Sale items)."
                    ),
                    category="policy_conflict",
                    severity="high",
                    order_id=extracted_order_id,
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                draft = (
                    f"I reviewed your order `{extracted_order_id}` for the **Archive Velvet Evening Blazer (Final Sale)** "
                    f"under your `VIP_GOLD` membership, and detected a direct conflict between two store policies:\n"
                    f"- **Clearance & Final Sale Policy (`POL-RET-02`)** states that Final Sale items are strictly non-returnable under any circumstances with no exceptions for any tier.\n"
                    f"- **VIP Gold Loyalty Policy (`POL-VIP-01`)** states that `VIP_GOLD` members receive a 60-day return guarantee on all purchases, including Final Sale items.\n\n"
                    f"Rather than guessing or making an unauthorized decision, I have escalated your request to a Human Support Specialist "
                    f"under Ticket **{ticket.ticket_id if ticket else 'ESC-9001'}** to review and resolve this for you."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="escalated_policy_conflict",
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            # Failure Mode #6: Duplicate Requests -> Idempotency Response!
            if status == "duplicate" and res.get("code") == "DUPLICATE_REQUEST_DETECTED":
                ref_id = res.get("reference_id")
                draft = (
                    f"A {action} request for order `{extracted_order_id}` has already been submitted and is active under "
                    f"reference ID **{ref_id}**. Our idempotency check prevented a duplicate {action} or double refund from being created. "
                    f"Your existing request **{ref_id}** remains in progress."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="duplicate_request_handled",
                    authenticated_email=extracted_email,
                )

            # Denied by clear non-conflicting policy (e.g., cancelling an already shipped order)
            if status == "denied_by_policy":
                draft = (
                    f"I am unable to {action} order `{extracted_order_id}`: {res.get('message')}"
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="answer",
                    authenticated_email=extracted_email,
                )

            if status == "error":
                draft = f"I could not find order `{extracted_order_id}` in our system. Please double-check the Order ID."
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="answer",
                    authenticated_email=extracted_email,
                )

            # Success!
            ref_id = res.get("reference_id")
            refund = res.get("refund_amount", 0.0)
            draft = (
                f"Your {action} request for order `{extracted_order_id}` has been processed! "
                f"Your confirmation reference is **{ref_id}**, and a refund of **${refund:.2f}** will be credited "
                f"to your original payment method within 5–7 business days."
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type="action_completed",
                authenticated_email=extracted_email,
            )

        # ------------------------------------------------------------------
        # 5. Handle Order Status / Tracking Intents
        # ------------------------------------------------------------------
        if intent == "order_status":
            # Failure Mode #1: Missing Information
            if not extracted_order_id or not extracted_email:
                missing_parts = []
                if not extracted_order_id:
                    missing_parts.append("your **Order ID** (e.g., `ORD-1002`)")
                if not extracted_email:
                    missing_parts.append("your **email address**")
                draft = (
                    f"I can check your order status right away! Please provide {' and '.join(missing_parts)} "
                    f"so I can verify and look up your order."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="clarify_missing_info",
                    authenticated_email=extracted_email,
                )

            res = self.toolkit.get_order_status(order_id=extracted_order_id, customer_email=extracted_email)
            status = res.get("status")

            # Failure Mode #5: Tool Failure
            if status == "error" and res.get("code") == "TOOL_EXECUTION_ERROR":
                self.toolkit.escalate_to_human(
                    reason=f"Database error looking up order {extracted_order_id}: {res.get('message')}",
                    category="tool_failure",
                    severity="high",
                    order_id=extracted_order_id,
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                draft = (
                    f"I apologize, but our order tracking database is currently unreachable (Error 503), so I cannot "
                    f"retrieve the live status for `{extracted_order_id}` right now. I have escalated your inquiry to "
                    f"a Human Support Specialist under Ticket **{ticket.ticket_id if ticket else 'ESC-9001'}** who will "
                    f"send your tracking update as soon as service is restored."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="escalated_tool_failure",
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            # Failure Mode #3: Unauthorized Order Access
            if status == "blocked" and res.get("code") == "UNAUTHORIZED_ORDER_ACCESS":
                draft = (
                    f"Access denied: The email address `{extracted_email}` is not authorized to access order `{extracted_order_id}`. "
                    f"To protect customer privacy, I cannot share any details about this order."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="blocked_unauthorized",
                    authenticated_email=extracted_email,
                    blocked=True,
                    blocked_reason="UNAUTHORIZED_ORDER_ACCESS",
                )

            if status == "error":
                draft = f"I could not find any order matching `{extracted_order_id}`. Please check the Order ID and try again."
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="answer",
                    authenticated_email=extracted_email,
                )

            order_data = res["order"]
            items_summary = ", ".join(f"{i['name']} (Size {i['size']})" for i in order_data["items"])
            tracking_info = (
                f"Tracking Number: `{order_data['tracking_number']}`"
                if order_data.get("tracking_number")
                else "Tracking number will be assigned once shipped"
            )
            delivery_note = ""
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
                decision_type="answer",
                authenticated_email=extracted_email,
            )

        # ------------------------------------------------------------------
        # 6. Handle Policy Questions
        # ------------------------------------------------------------------
        if intent == "policy_question":
            text_lower = user_message.lower()
            topic = "all"
            if "shipping" in text_lower or "delivery" in text_lower:
                topic = "shipping"
            elif "cancel" in text_lower:
                topic = "cancellations"
            elif "vip" in text_lower or "final sale" in text_lower:
                topic = "all"
            elif "return" in text_lower or "exchange" in text_lower:
                topic = "returns"

            res = self.toolkit.lookup_policies(topic=topic)
            if res.get("status") == "error":
                self.toolkit.escalate_to_human(
                    reason=f"Tool error looking up policy '{topic}': {res.get('message')}",
                    category="tool_failure",
                    severity="medium",
                )
                ticket = self.toolkit.last_escalation
                draft = (
                    f"I apologize, but our policy knowledge base is temporarily unavailable. "
                    f"I have escalated your question to a Human Specialist under Ticket **{ticket.ticket_id if ticket else 'ESC-9001'}**."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="escalated_tool_failure",
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            # Check if the user is asking specifically about VIP Gold + Final Sale policy conflict
            if ("vip" in text_lower and "final sale" in text_lower) or ("vip" in text_lower and "clearance" in text_lower):
                self.toolkit.escalate_to_human(
                    reason="Customer asked about conflicting policies POL-RET-02 (Final Sale) and POL-VIP-01 (VIP Gold).",
                    category="policy_conflict",
                    severity="medium",
                    customer_email=extracted_email,
                )
                ticket = self.toolkit.last_escalation
                draft = (
                    "There is currently a conflict between two of our official policies regarding Final Sale items for VIP Gold members:\n"
                    "- **`POL-RET-02` (Clearance & Final Sale Policy):** States Final Sale items are strictly non-returnable with no exceptions for any tier.\n"
                    "- **`POL-VIP-01` (VIP Gold Guarantee):** States VIP Gold members receive a 60-day return window on all items, including Final Sale items.\n\n"
                    f"Because these two policies conflict, I have escalated your inquiry to a Human Support Specialist under Ticket **{ticket.ticket_id if ticket else 'ESC-9001'}** to confirm eligibility for your specific purchase."
                )
                return self._finalize_response(
                    user_message=user_message,
                    draft_reply=draft,
                    decision_type="escalated_policy_conflict",
                    authenticated_email=extracted_email,
                    escalated=True,
                )

            policies = res.get("policies", [])
            bullet_lines = [f"- **{p['title']} (`{p['policy_id']}`):** {p['rule']}" for p in policies]
            draft = "Here are the official MaxWear Apparel policies for your inquiry:\n" + "\n".join(bullet_lines)
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type="answer",
                authenticated_email=extracted_email,
            )

        # ------------------------------------------------------------------
        # 7. Handle Product & General Catalog Questions
        # ------------------------------------------------------------------
        res = self.toolkit.lookup_products(query=user_message)
        if res.get("status") == "error":
            self.toolkit.escalate_to_human(
                reason=f"Catalog lookup error: {res.get('message')}",
                category="tool_failure",
                severity="medium",
            )
            ticket = self.toolkit.last_escalation
            draft = (
                f"I apologize, our product catalog database is currently unavailable. "
                f"I have created Escalation Ticket **{ticket.ticket_id if ticket else 'ESC-9001'}** for a specialist to assist you."
            )
            return self._finalize_response(
                user_message=user_message,
                draft_reply=draft,
                decision_type="escalated_tool_failure",
                authenticated_email=extracted_email,
                escalated=True,
            )

        products = res.get("products", [])
        product_lines = []
        for p in products:
            sale_tag = " **[FINAL SALE - Non-Returnable per POL-RET-02]**" if p.get("final_sale") else " *(Standard 30-day return eligible)*"
            in_stock_str = ", ".join(p["sizes_in_stock"])
            out_stock_str = ", ".join(p["sizes_out_of_stock"]) if p["sizes_out_of_stock"] else "None"
            product_lines.append(
                f"- **{p['name']} (`{p['product_id']}`)** — **${p['price']:.2f}**{sale_tag}\n"
                f"  - *Sizes in stock:* {in_stock_str} (Out of stock: {out_stock_str})\n"
                f"  - *Fit Guide:* {p['fit_guide']}\n"
                f"  - *Fabric & Care:* {p['fabric_and_care']}"
            )
        draft = "Here are the matching MaxWear Apparel product details:\n" + "\n".join(product_lines)
        return self._finalize_response(
            user_message=user_message,
            draft_reply=draft,
            decision_type="answer",
            authenticated_email=extracted_email,
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
    ) -> AgentResponse:
        polished_reply, backend_label = self._polish_with_llm_if_enabled(
            user_message=user_message,
            decision_type=decision_type,
            draft_reply=draft_reply,
        )
        # Defense-in-depth PII scrubbing
        safe_reply, pii_scrubbed = scrub_unauthorized_pii(
            reply_text=polished_reply,
            authenticated_email=authenticated_email,
            all_orders=self.store.orders,
            all_customers=self.store.customers,
        )
        return AgentResponse(
            reply=safe_reply,
            decision_type=decision_type,
            tools_called=list(self.toolkit.call_history),
            escalated=escalated,
            escalation_ticket=self.toolkit.last_escalation,
            blocked=blocked,
            blocked_reason=blocked_reason,
            backend_used=backend_label,
            metadata={"pii_scrubbed": pii_scrubbed},
        )

