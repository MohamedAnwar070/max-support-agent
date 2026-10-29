import inspect
from typing import Any, Dict, List, Optional, Set

from .constants import (
    CODE_CONFLICT_VIP_FINAL_SALE,
    CODE_DUPLICATE,
    CODE_MISSING_PARAMS,
    CODE_REQUEST_KEY_MISMATCH,
    CODE_TOOL_ERROR,
    CODE_UNAUTHORIZED,
    STANDARD_RETURN_WINDOW_DAYS,
    STATUS_BLOCKED,
    STATUS_CONFLICT,
    STATUS_DENIED,
    STATUS_DUPLICATE,
    STATUS_ERROR,
    STATUS_MISSING_INFO,
    STATUS_SUCCESS,
    VIP_GOLD_RETURN_WINDOW_DAYS,
    VIP_GOLD_TIER,
    VERIFICATION_FAILED_MESSAGE,
)
from .data_store import DataStore, ToolExecutionError
from .models import EscalationTicket, ToolCallRecord


class SupportToolkit:
    def __init__(self, store: DataStore) -> None:
        self.store = store
        self.call_history: List[ToolCallRecord] = []
        self.last_escalation: Optional[EscalationTicket] = None
        # Emails that passed an order-ownership check; only these may see customer PII in replies.
        self.verified_emails: Set[str] = set()

    def clear_turn_history(self) -> None:
        self.call_history = []
        self.last_escalation = None

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        dispatch_map = {
            "lookup_products": self.lookup_products,
            "lookup_policies": self.lookup_policies,
            "get_order_status": self.get_order_status,
            "process_return_or_cancel": self.process_return_or_cancel,
            "escalate_to_human": self.escalate_to_human,
        }
        if tool_name not in dispatch_map:
            res = {"status": STATUS_ERROR, "code": "UNKNOWN_TOOL", "message": f"Unknown tool: {tool_name}"}
            self.call_history.append(ToolCallRecord(tool_name, arguments, STATUS_ERROR, res))
            return res

        handler = dispatch_map[tool_name]
        allowed = set(inspect.signature(handler).parameters)
        clean_args = {k: v for k, v in (arguments or {}).items() if k in allowed}
        try:
            return handler(**clean_args)
        except TypeError as exc:
            res = {"status": STATUS_ERROR, "code": "INVALID_ARGUMENTS", "message": str(exc)}
            self.call_history.append(ToolCallRecord(tool_name, clean_args, STATUS_ERROR, res))
            return res

    def _verification_failed(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        res = {"status": STATUS_BLOCKED, "code": CODE_UNAUTHORIZED, "message": VERIFICATION_FAILED_MESSAGE}
        self.call_history.append(ToolCallRecord(tool_name, args, STATUS_BLOCKED, res))
        return res

    @staticmethod
    def _owns(order: Optional[Dict[str, Any]], customer_email: str) -> bool:
        return bool(order) and order["customer_email"].strip().lower() == customer_email.strip().lower()

    def lookup_products(self, query: str = "") -> Dict[str, Any]:
        try:
            matches = self.store.query_products(query)
        except ToolExecutionError as exc:
            res = {"status": STATUS_ERROR, "code": CODE_TOOL_ERROR, "message": str(exc)}
            self.call_history.append(ToolCallRecord("lookup_products", {"query": query}, STATUS_ERROR, res))
            return res

        res = {
            "status": STATUS_SUCCESS,
            "count": len(matches),
            "products": matches,
        }
        self.call_history.append(ToolCallRecord("lookup_products", {"query": query}, STATUS_SUCCESS, res))
        return res

    def lookup_policies(self, topic: str = "all") -> Dict[str, Any]:
        try:
            filtered, conflicts = self.store.query_policies(topic)
        except ToolExecutionError as exc:
            res = {"status": STATUS_ERROR, "code": CODE_TOOL_ERROR, "message": str(exc)}
            self.call_history.append(ToolCallRecord("lookup_policies", {"topic": topic}, STATUS_ERROR, res))
            return res

        res = {
            "status": STATUS_SUCCESS,
            "policies": filtered,
            "known_conflicts": conflicts,
        }
        self.call_history.append(ToolCallRecord("lookup_policies", {"topic": topic}, STATUS_SUCCESS, res))
        return res

    def get_order_status(self, order_id: str = "", customer_email: str = "") -> Dict[str, Any]:
        args = {"order_id": order_id, "customer_email": customer_email}
        if not order_id or not order_id.strip() or not customer_email or not customer_email.strip():
            missing = []
            if not order_id or not order_id.strip():
                missing.append("order_id")
            if not customer_email or not customer_email.strip():
                missing.append("customer_email")
            res = {
                "status": STATUS_MISSING_INFO,
                "code": CODE_MISSING_PARAMS,
                "missing_fields": missing,
                "message": f"Missing required parameters: {', '.join(missing)}.",
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, STATUS_MISSING_INFO, res))
            return res

        try:
            order = self.store.get_order_by_id(order_id)
        except ToolExecutionError as exc:
            res = {
                "status": STATUS_ERROR,
                "code": CODE_TOOL_ERROR,
                "message": str(exc),
                "required_action": "Report that the action could not be verified; do not claim success.",
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, STATUS_ERROR, res))
            return res

        if not self._owns(order, customer_email):
            return self._verification_failed("get_order_status", args)

        self.verified_emails.add(customer_email.strip().lower())
        res = {
            "status": STATUS_SUCCESS,
            "order": order,
        }
        self.call_history.append(ToolCallRecord("get_order_status", args, STATUS_SUCCESS, res))
        return res

    def process_return_or_cancel(
        self,
        order_id: str = "",
        customer_email: str = "",
        action: str = "return",
        reason: str = "",
        request_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        norm_action = (action or "return").strip().lower()
        if norm_action not in ("return", "cancel"):
            norm_action = "return"
        effective_request_key = (
            request_key.strip().upper()
            if request_key and request_key.strip()
            else f"REQ-{order_id.strip().upper()}-{norm_action.upper()}"
        )
        args = {
            "order_id": order_id,
            "customer_email": customer_email,
            "action": norm_action,
            "reason": reason,
            "request_key": effective_request_key,
        }

        if not order_id or not order_id.strip() or not customer_email or not customer_email.strip():
            missing = []
            if not order_id or not order_id.strip():
                missing.append("order_id")
            if not customer_email or not customer_email.strip():
                missing.append("customer_email")
            res = {
                "status": STATUS_MISSING_INFO,
                "code": CODE_MISSING_PARAMS,
                "missing_fields": missing,
                "message": f"Cannot process {norm_action} without {', '.join(missing)}.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_MISSING_INFO, res))
            return res

        try:
            order = self.store.get_order_by_id(order_id)
        except ToolExecutionError as exc:
            res = {
                "status": STATUS_ERROR,
                "code": CODE_TOOL_ERROR,
                "message": str(exc),
                "required_action": "Report that the action could not be verified and do not claim success. Escalate via escalate_to_human.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_ERROR, res))
            return res

        if not self._owns(order, customer_email):
            return self._verification_failed("process_return_or_cancel", args)

        self.verified_emails.add(customer_email.strip().lower())

        idempotency_key = f"{order['order_id'].upper()}:{norm_action}"
        try:
            existing = self.store.get_idempotency_record(idempotency_key)
            key_taken = not existing and self.store.request_key_owned_elsewhere(effective_request_key, idempotency_key)
        except ToolExecutionError as exc:
            res = {
                "status": STATUS_ERROR,
                "code": CODE_TOOL_ERROR,
                "message": str(exc),
                "required_action": "Report that the action could not be verified and do not claim success. Escalate via escalate_to_human.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_ERROR, res))
            return res

        if key_taken:
            res = {
                "status": STATUS_BLOCKED,
                "code": CODE_REQUEST_KEY_MISMATCH,
                "message": "The supplied request key does not belong to this order and action. No action was taken.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_BLOCKED, res))
            return res

        if existing:
            res = {
                "status": STATUS_DUPLICATE,
                "code": CODE_DUPLICATE,
                "reference_id": existing["reference_id"],
                "request_key": existing["request_key"],
                "refund_amount": existing["refund_amount"],
                "order_id": order["order_id"],
                "action": norm_action,
                "original_status": existing["status"],
                "message": (
                    f"Duplicate request detected for request_key '{existing['request_key']}'. "
                    f"Returning original action result ({existing['reference_id']}, mock refund ${existing['refund_amount']:.2f}) "
                    "without repeating the action."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DUPLICATE, res))
            return res

        if norm_action == "return":
            has_final_sale = any(item.get("final_sale", False) for item in order.get("items", []))
            is_vip_gold = order.get("loyalty_tier") == VIP_GOLD_TIER

            if order["status"] != "delivered":
                res = {
                    "status": STATUS_DENIED,
                    "code": "ORDER_NOT_DELIVERED",
                    "message": f"Order {order['order_id']} is currently '{order['status']}'. Returns require delivery first.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DENIED, res))
                return res

            max_days = VIP_GOLD_RETURN_WINDOW_DAYS if is_vip_gold else STANDARD_RETURN_WINDOW_DAYS
            days_elapsed = order.get("days_since_delivery") or 0
            if days_elapsed > max_days:
                res = {
                    "status": STATUS_DENIED,
                    "code": "RETURN_WINDOW_EXPIRED",
                    "message": f"Order was delivered {days_elapsed} days ago, exceeding the {max_days}-day return policy window.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DENIED, res))
                return res

            if has_final_sale and is_vip_gold:
                res = {
                    "status": STATUS_CONFLICT,
                    "code": CODE_CONFLICT_VIP_FINAL_SALE,
                    "conflicting_policies": ["POL-RET-02", "POL-VIP-01"],
                    "order_id": order["order_id"],
                    "loyalty_tier": order["loyalty_tier"],
                    "item_names": [i["name"] for i in order["items"]],
                    "message": (
                        "POLICY CONFLICT DETECTED between POL-RET-02 (Final Sale strictly non-returnable) "
                        "and POL-VIP-01 (VIP Gold 14-day return guarantee includes Final Sale items). "
                        "Escalate without guessing via escalate_to_human."
                    ),
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_CONFLICT, res))
                return res

            if has_final_sale:
                res = {
                    "status": STATUS_DENIED,
                    "code": "FINAL_SALE_NON_RETURNABLE",
                    "policy_id": "POL-RET-02",
                    "message": "This order contains a Final Sale item, which is non-returnable under Policy POL-RET-02.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DENIED, res))
                return res

            return_id = self.store.next_return_id()
            self.store.record_return(
                order["order_id"],
                return_id,
                idempotency_key,
                effective_request_key,
                order["total_amount"],
            )
            res = {
                "status": STATUS_SUCCESS,
                "action": "return",
                "reference_id": return_id,
                "request_key": effective_request_key,
                "order_id": order["order_id"],
                "refund_amount": order["total_amount"],
                "message": (
                    f"Mock refund and return processed for order {order['order_id']} under reference {return_id} "
                    f"(request_key: {effective_request_key}). Refund of ${order['total_amount']:.2f} will process within 3-5 business days."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_SUCCESS, res))
            return res

        if order["status"] != "processing":
            res = {
                "status": STATUS_DENIED,
                "code": "CANNOT_CANCEL_NON_PROCESSING_ORDER",
                "policy_id": "POL-CAN-01",
                "message": (
                    f"Order {order['order_id']} is currently in '{order['status']}' status and cannot be cancelled "
                    "per Policy POL-CAN-01. Only orders in 'processing' status can be cancelled before shipment."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DENIED, res))
            return res

        cancel_id = self.store.next_cancel_id()
        self.store.record_cancellation(
            order["order_id"],
            cancel_id,
            idempotency_key,
            effective_request_key,
            order["total_amount"],
        )
        res = {
            "status": STATUS_SUCCESS,
            "action": "cancel",
            "reference_id": cancel_id,
            "request_key": effective_request_key,
            "order_id": order["order_id"],
            "refund_amount": order["total_amount"],
            "message": (
                f"Order {order['order_id']} cancelled under reference {cancel_id} "
                f"(request_key: {effective_request_key}). Full mock refund of ${order['total_amount']:.2f} issued."
            ),
        }
        self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_SUCCESS, res))
        return res

    def escalate_to_human(
        self,
        reason: str,
        category: str = "customer_request",
        severity: str = "medium",
        order_id: Optional[str] = None,
        customer_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        ticket_id = self.store.next_ticket_id()
        ticket = EscalationTicket(
            ticket_id=ticket_id,
            category=category,
            severity=severity,
            reason=reason,
            order_id=order_id,
            customer_email=customer_email,
        )
        self.last_escalation = ticket
        ticket_dict = ticket.to_dict()
        self.store.record_escalation(ticket_dict)
        args = {
            "reason": reason,
            "category": category,
            "severity": severity,
            "order_id": order_id,
            "customer_email": customer_email,
        }
        res = {
            "status": STATUS_SUCCESS,
            "ticket": ticket_dict,
            "message": f"Escalated to Human Support Specialist under Ticket {ticket_id}.",
        }
        self.call_history.append(ToolCallRecord("escalate_to_human", args, STATUS_SUCCESS, res))
        return res
