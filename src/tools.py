from typing import Any, Dict, List, Optional

from .constants import (
    CODE_CONFLICT_VIP_FINAL_SALE,
    CODE_DUPLICATE,
    CODE_MISSING_PARAMS,
    CODE_ORDER_NOT_FOUND,
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
)
from .data_store import DataStore, ToolExecutionError
from .models import EscalationTicket, ToolCallRecord


class SupportToolkit:
    def __init__(self, store: DataStore) -> None:
        self.store = store
        self.call_history: List[ToolCallRecord] = []
        self.last_escalation: Optional[EscalationTicket] = None

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

        return dispatch_map[tool_name](**arguments)

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
                "required_action": "Escalate via escalate_to_human without fabricating order status.",
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, STATUS_ERROR, res))
            return res

        if order is None:
            res = {
                "status": STATUS_ERROR,
                "code": CODE_ORDER_NOT_FOUND,
                "message": f"No order found matching ID '{order_id}'.",
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, STATUS_ERROR, res))
            return res

        if order["customer_email"].strip().lower() != customer_email.strip().lower():
            res = {
                "status": STATUS_BLOCKED,
                "code": CODE_UNAUTHORIZED,
                "message": (
                    f"Access denied: Customer email '{customer_email}' does not match the owner on record "
                    f"for order '{order_id}'."
                ),
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, STATUS_BLOCKED, res))
            return res

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
    ) -> Dict[str, Any]:
        norm_action = (action or "return").strip().lower()
        if norm_action not in ("return", "cancel"):
            norm_action = "return"
        args = {
            "order_id": order_id,
            "customer_email": customer_email,
            "action": norm_action,
            "reason": reason,
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
                "required_action": "Escalate via escalate_to_human due to SQLite database error.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_ERROR, res))
            return res

        if order is None:
            res = {
                "status": STATUS_ERROR,
                "code": CODE_ORDER_NOT_FOUND,
                "message": f"Order '{order_id}' was not found.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_ERROR, res))
            return res

        if order["customer_email"].strip().lower() != customer_email.strip().lower():
            res = {
                "status": STATUS_BLOCKED,
                "code": CODE_UNAUTHORIZED,
                "message": f"Security Block: '{customer_email}' is not authorized to access order '{order_id}'.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_BLOCKED, res))
            return res

        idempotency_key = f"{order['order_id'].upper()}:{norm_action}"
        existing = self.store.get_idempotency_record(idempotency_key)
        if existing:
            res = {
                "status": STATUS_DUPLICATE,
                "code": CODE_DUPLICATE,
                "reference_id": existing["reference_id"],
                "order_id": order["order_id"],
                "action": norm_action,
                "message": (
                    f"Idempotency guard triggered: A {norm_action} request for order {order['order_id']} "
                    f"has already been processed under reference ID {existing['reference_id']}."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DUPLICATE, res))
            return res

        if norm_action == "return":
            has_final_sale = any(item.get("final_sale", False) for item in order.get("items", []))
            is_vip_gold = order.get("loyalty_tier") == VIP_GOLD_TIER

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
                        "and POL-VIP-01 (VIP Gold 60-day return guarantee includes Final Sale items). "
                        "Escalate via escalate_to_human."
                    ),
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_CONFLICT, res))
                return res

            if has_final_sale and not is_vip_gold:
                res = {
                    "status": STATUS_DENIED,
                    "code": "FINAL_SALE_NON_RETURNABLE",
                    "policy_id": "POL-RET-02",
                    "message": "This order contains a Final Sale item, which is non-returnable under Policy POL-RET-02.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DENIED, res))
                return res

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
                    "message": f"Order was delivered {days_elapsed} days ago, exceeding the {max_days}-day return window.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, STATUS_DENIED, res))
                return res

            return_id = self.store.next_return_id()
            self.store.record_return(order["order_id"], return_id, idempotency_key)
            res = {
                "status": STATUS_SUCCESS,
                "action": "return",
                "reference_id": return_id,
                "order_id": order["order_id"],
                "refund_amount": order["total_amount"],
                "message": (
                    f"Return initiated for order {order['order_id']} under RMA {return_id}. "
                    f"Refund of ${order['total_amount']:.2f} will process within 5-7 business days."
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
        self.store.record_cancellation(order["order_id"], cancel_id, idempotency_key)
        res = {
            "status": STATUS_SUCCESS,
            "action": "cancel",
            "reference_id": cancel_id,
            "order_id": order["order_id"],
            "refund_amount": order["total_amount"],
            "message": (
                f"Order {order['order_id']} cancelled under reference {cancel_id}. "
                f"Full refund of ${order['total_amount']:.2f} issued."
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
