"""Deterministic tool implementations and OpenAI/Claude tool schemas for the support agent."""

from typing import Any, Dict, List, Optional
from .data_store import DataStore, ToolExecutionError
from .models import EscalationTicket, ToolCallRecord


OPENAI_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_products",
            "description": "Search the MaxWear apparel catalog for product pricing, sizes in stock, fit guides, care instructions, and Final Sale status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Product name, SKU (e.g., PROD-001), category, or keyword (e.g., hoodie, jacket, blazer, leggings, denim). Leave empty to list all products.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_policies",
            "description": "Retrieve official MaxWear store policies for returns, final_sale, vip_loyalty, cancellations, shipping, or escalation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Policy topic: 'returns', 'final_sale', 'vip_loyalty', 'cancellations', 'shipping', 'escalation', or 'all'.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Look up an order's fulfillment status, items, and tracking number. Requires BOTH order_id and customer_email to verify ownership.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The order identifier (e.g., ORD-1001).",
                    },
                    "customer_email": {
                        "type": "string",
                        "description": "The email address of the customer requesting the order details.",
                    },
                },
                "required": ["order_id", "customer_email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_return_or_cancel",
            "description": "Initiate a return for a delivered order or cancel a processing order. Enforces customer ownership, policy compliance, conflict checks, and idempotency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The order identifier (e.g., ORD-1001).",
                    },
                    "customer_email": {
                        "type": "string",
                        "description": "The verified email address of the customer who owns the order.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["return", "cancel"],
                        "description": "Whether to 'return' a delivered order or 'cancel' a processing order.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Customer's stated reason for the return or cancellation.",
                    },
                },
                "required": ["order_id", "customer_email", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Escalate the conversation to a human support specialist when policies conflict, a backend tool fails, or an action is unsafe/out-of-scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Detailed summary of why human intervention is required.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["policy_conflict", "tool_failure", "unsafe_action", "customer_request"],
                        "description": "Category of the escalation.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Urgency level of the escalation.",
                    },
                    "order_id": {
                        "type": "string",
                        "description": "Associated order ID if known.",
                    },
                    "customer_email": {
                        "type": "string",
                        "description": "Associated customer email if known.",
                    },
                },
                "required": ["reason", "category"],
            },
        },
    },
]


class SupportToolkit:
    """Executes support tools against the DataStore and records audit logs."""

    def __init__(self, store: DataStore) -> None:
        self.store = store
        self.call_history: List[ToolCallRecord] = []
        self.last_escalation: Optional[EscalationTicket] = None

    def clear_turn_history(self) -> None:
        self.call_history = []
        self.last_escalation = None

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch a tool call by name and record the result."""
        dispatch_map = {
            "lookup_products": self.lookup_products,
            "lookup_policies": self.lookup_policies,
            "get_order_status": self.get_order_status,
            "process_return_or_cancel": self.process_return_or_cancel,
            "escalate_to_human": self.escalate_to_human,
        }
        if tool_name not in dispatch_map:
            res = {"status": "error", "code": "UNKNOWN_TOOL", "message": f"Unknown tool: {tool_name}"}
            self.call_history.append(ToolCallRecord(tool_name, arguments, "error", res))
            return res

        return dispatch_map[tool_name](**arguments)

    def lookup_products(self, query: str = "") -> Dict[str, Any]:
        try:
            self.store.check_health()
        except ToolExecutionError as exc:
            res = {"status": "error", "code": "TOOL_EXECUTION_ERROR", "message": str(exc)}
            self.call_history.append(ToolCallRecord("lookup_products", {"query": query}, "error", res))
            return res

        q = (query or "").strip().lower()
        if not q or q in ("all", "catalog", "products"):
            matches = self.store.products
        else:
            tokens = [t for t in q.replace("-", " ").split() if len(t) > 1]
            matches = []
            for p in self.store.products:
                haystack = (
                    f"{p['product_id']} {p['name']} {p['category']} {p['description']} {p['fabric_and_care']}"
                ).lower()
                if q in haystack or any(tok in haystack for tok in tokens):
                    matches.append(p)
            if not matches:
                matches = self.store.products

        res = {
            "status": "success",
            "count": len(matches),
            "products": matches,
        }
        self.call_history.append(ToolCallRecord("lookup_products", {"query": query}, "success", res))
        return res

    def lookup_policies(self, topic: str = "all") -> Dict[str, Any]:
        try:
            self.store.check_health()
        except ToolExecutionError as exc:
            res = {"status": "error", "code": "TOOL_EXECUTION_ERROR", "message": str(exc)}
            self.call_history.append(ToolCallRecord("lookup_policies", {"topic": topic}, "error", res))
            return res

        t = (topic or "all").strip().lower()
        all_policies = self.store.policies.get("policies", [])
        conflicts = self.store.policies.get("known_conflicts", [])

        if t in ("all", ""):
            filtered = all_policies
        else:
            filtered = [
                p for p in all_policies
                if t in p["topic"].lower() or t in p["title"].lower() or t in p["rule"].lower()
            ]
            if not filtered:
                filtered = all_policies

        res = {
            "status": "success",
            "policies": filtered,
            "known_conflicts": conflicts,
        }
        self.call_history.append(ToolCallRecord("lookup_policies", {"topic": topic}, "success", res))
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
                "status": "missing_info",
                "code": "MISSING_REQUIRED_PARAMETERS",
                "missing_fields": missing,
                "message": f"Cannot look up order without {', '.join(missing)}. Please ask the customer to provide {', '.join(missing)}.",
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, "missing_info", res))
            return res

        try:
            order = self.store.get_order_by_id(order_id)
        except ToolExecutionError as exc:
            res = {
                "status": "error",
                "code": "TOOL_EXECUTION_ERROR",
                "message": str(exc),
                "required_action": "Do not fabricate order status. Apologize for the technical outage and escalate via escalate_to_human.",
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, "error", res))
            return res

        if order is None:
            res = {
                "status": "error",
                "code": "ORDER_NOT_FOUND",
                "message": f"No order found matching ID '{order_id}'.",
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, "error", res))
            return res

        # Enforce strict customer ownership verification
        if order["customer_email"].strip().lower() != customer_email.strip().lower():
            res = {
                "status": "blocked",
                "code": "UNAUTHORIZED_ORDER_ACCESS",
                "message": (
                    f"Access denied: Customer email '{customer_email}' does not match the owner on record "
                    f"for order '{order_id}'. Do NOT disclose any details about this order."
                ),
            }
            self.call_history.append(ToolCallRecord("get_order_status", args, "blocked", res))
            return res

        res = {
            "status": "success",
            "order": {
                "order_id": order["order_id"],
                "customer_name": order["customer_name"],
                "customer_email": order["customer_email"],
                "loyalty_tier": order["loyalty_tier"],
                "status": order["status"],
                "order_date": order["order_date"],
                "delivery_date": order.get("delivery_date"),
                "days_since_delivery": order.get("days_since_delivery"),
                "estimated_delivery": order.get("estimated_delivery"),
                "shipping_city": order["shipping_city"],
                "tracking_number": order.get("tracking_number"),
                "total_amount": order["total_amount"],
                "items": order["items"],
                "active_return_id": order.get("active_return_id"),
                "cancellation_id": order.get("cancellation_id"),
            },
        }
        self.call_history.append(ToolCallRecord("get_order_status", args, "success", res))
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

        # 1. Missing Info Check
        if not order_id or not order_id.strip() or not customer_email or not customer_email.strip():
            missing = []
            if not order_id or not order_id.strip():
                missing.append("order_id")
            if not customer_email or not customer_email.strip():
                missing.append("customer_email")
            res = {
                "status": "missing_info",
                "code": "MISSING_REQUIRED_PARAMETERS",
                "missing_fields": missing,
                "message": f"Cannot process {norm_action} without {', '.join(missing)}. Clarify with the customer first.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "missing_info", res))
            return res

        # 2. Database Lookup (with Tool Failure handling)
        try:
            order = self.store.get_order_by_id(order_id)
        except ToolExecutionError as exc:
            res = {
                "status": "error",
                "code": "TOOL_EXECUTION_ERROR",
                "message": str(exc),
                "required_action": "Backend database is unavailable. Do not confirm the action. Escalate via escalate_to_human.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "error", res))
            return res

        if order is None:
            res = {
                "status": "error",
                "code": "ORDER_NOT_FOUND",
                "message": f"Order '{order_id}' was not found.",
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "error", res))
            return res

        # 3. Ownership Authorization Check (Failure Mode #3: Unauthorised Order Access)
        if order["customer_email"].strip().lower() != customer_email.strip().lower():
            res = {
                "status": "blocked",
                "code": "UNAUTHORIZED_ORDER_ACCESS",
                "message": (
                    f"Security Block: '{customer_email}' is not authorized to modify or view order '{order_id}'. "
                    "No changes were made."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "blocked", res))
            return res

        # 4. Idempotency / Duplicate Request Check (Failure Mode #6: Duplicate Requests)
        idempotency_key = f"{order['order_id'].upper()}:{norm_action}"
        if idempotency_key in self.store.idempotency_ledger:
            existing = self.store.idempotency_ledger[idempotency_key]
            res = {
                "status": "duplicate",
                "code": "DUPLICATE_REQUEST_DETECTED",
                "reference_id": existing["reference_id"],
                "order_id": order["order_id"],
                "action": norm_action,
                "message": (
                    f"Idempotency guard triggered: A {norm_action} request for order {order['order_id']} "
                    f"has already been processed under reference ID {existing['reference_id']}. "
                    "No duplicate transaction or second refund was created."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "duplicate", res))
            return res

        # 5. Handle Return Logic & Policy Conflict Detection
        if norm_action == "return":
            has_final_sale = any(item.get("final_sale", False) for item in order.get("items", []))
            is_vip_gold = order.get("loyalty_tier") == "VIP_GOLD"

            # Failure Mode #2: Conflicting Policies (VIP_GOLD + Final Sale item)
            if has_final_sale and is_vip_gold:
                res = {
                    "status": "conflict",
                    "code": "POLICY_CONFLICT_VIP_FINAL_SALE",
                    "conflicting_policies": ["POL-RET-02", "POL-VIP-01"],
                    "order_id": order["order_id"],
                    "loyalty_tier": order["loyalty_tier"],
                    "item_names": [i["name"] for i in order["items"]],
                    "message": (
                        "POLICY CONFLICT DETECTED: Order ORD-1001 contains a Final Sale item ('Archive Velvet Evening Blazer (Final Sale)'). "
                        "Policy POL-RET-02 states Final Sale items are strictly non-returnable with zero exceptions for any tier, "
                        "whereas Policy POL-VIP-01 states VIP_GOLD members get a 60-day return guarantee on ALL purchases including Final Sale items. "
                        "Per Governance Policy POL-ESC-01, do NOT approve or deny this return automatically—escalate to a human specialist via escalate_to_human."
                    ),
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "conflict", res))
                return res

            # Non-VIP Final Sale item -> Clear policy denial
            if has_final_sale and not is_vip_gold:
                res = {
                    "status": "denied_by_policy",
                    "code": "FINAL_SALE_NON_RETURNABLE",
                    "policy_id": "POL-RET-02",
                    "message": "This order contains a Final Sale item, which is non-returnable under Policy POL-RET-02.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "denied_by_policy", res))
                return res

            if order["status"] != "delivered":
                res = {
                    "status": "denied_by_policy",
                    "code": "ORDER_NOT_DELIVERED",
                    "message": f"Order {order['order_id']} is currently '{order['status']}'. Returns can only be initiated after delivery.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "denied_by_policy", res))
                return res

            max_days = 60 if is_vip_gold else 30
            days_elapsed = order.get("days_since_delivery") or 0
            if days_elapsed > max_days:
                res = {
                    "status": "denied_by_policy",
                    "code": "RETURN_WINDOW_EXPIRED",
                    "message": f"Order was delivered {days_elapsed} days ago, exceeding the {max_days}-day return window.",
                }
                self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "denied_by_policy", res))
                return res

            # Approve and record return
            return_id = self.store.next_return_id()
            order["status"] = "return_initiated"
            order["active_return_id"] = return_id
            self.store.idempotency_ledger[idempotency_key] = {
                "reference_id": return_id,
                "action": "return",
                "order_id": order["order_id"],
                "status": "return_initiated",
            }
            res = {
                "status": "success",
                "action": "return",
                "reference_id": return_id,
                "order_id": order["order_id"],
                "refund_amount": order["total_amount"],
                "message": (
                    f"Return successfully initiated for order {order['order_id']}. "
                    f"RMA Reference: {return_id}. Refund of ${order['total_amount']:.2f} will process within 5-7 business days."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "success", res))
            return res

        # 6. Handle Cancellation Logic
        if order["status"] != "processing":
            res = {
                "status": "denied_by_policy",
                "code": "CANNOT_CANCEL_NON_PROCESSING_ORDER",
                "policy_id": "POL-CAN-01",
                "message": (
                    f"Order {order['order_id']} is currently in '{order['status']}' status and cannot be cancelled "
                    "per Policy POL-CAN-01. Only orders in 'processing' status can be cancelled before shipment."
                ),
            }
            self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "denied_by_policy", res))
            return res

        cancel_id = self.store.next_cancel_id()
        order["status"] = "cancelled"
        order["cancellation_id"] = cancel_id
        self.store.idempotency_ledger[idempotency_key] = {
            "reference_id": cancel_id,
            "action": "cancel",
            "order_id": order["order_id"],
            "status": "cancelled",
        }
        res = {
            "status": "success",
            "action": "cancel",
            "reference_id": cancel_id,
            "order_id": order["order_id"],
            "refund_amount": order["total_amount"],
            "message": (
                f"Order {order['order_id']} has been cancelled. "
                f"Cancellation Reference: {cancel_id}. Full refund of ${order['total_amount']:.2f} issued."
            ),
        }
        self.call_history.append(ToolCallRecord("process_return_or_cancel", args, "success", res))
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
        self.store.escalations.append(ticket.to_dict())
        args = {
            "reason": reason,
            "category": category,
            "severity": severity,
            "order_id": order_id,
            "customer_email": customer_email,
        }
        res = {
            "status": "success",
            "ticket": ticket.to_dict(),
            "message": f"Escalated to Human Support Specialist under Ticket {ticket_id} (Category: {category}, Severity: {severity}).",
        }
        self.call_history.append(ToolCallRecord("escalate_to_human", args, "success", res))
        return res

