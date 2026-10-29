"""In-memory data store backed by fictional JSON files with fault-injection and idempotency tracking."""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class ToolExecutionError(RuntimeError):
    """Raised when a backend tool or database operation fails or times out."""
    pass


class DataStore:
    """Manages fictional products, policies, customers, and orders in memory."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent.parent / "data"
        self.data_dir = Path(data_dir)
        self._raw_products: List[Dict[str, Any]] = self._load_json("products.json")
        self._raw_policies: Dict[str, Any] = self._load_json("policies.json")
        self._raw_customers: List[Dict[str, Any]] = self._load_json("customers.json")
        self._raw_orders: List[Dict[str, Any]] = self._load_json("orders.json")

        # Mutable working state (resettable between tests/evals)
        self.products: List[Dict[str, Any]] = []
        self.policies: Dict[str, Any] = {}
        self.customers: List[Dict[str, Any]] = []
        self.orders: List[Dict[str, Any]] = []
        self.escalations: List[Dict[str, Any]] = []
        self.idempotency_ledger: Dict[str, Dict[str, Any]] = {}
        self.simulate_db_failure: bool = False
        self._ticket_counter: int = 9000
        self._return_counter: int = 8802
        self._cancel_counter: int = 7701

        self.reset()

    def _load_json(self, filename: str) -> Any:
        file_path = self.data_dir / filename
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def reset(self) -> None:
        """Reset working state to a clean copy of the fictional dataset."""
        self.products = copy.deepcopy(self._raw_products)
        self.policies = copy.deepcopy(self._raw_policies)
        self.customers = copy.deepcopy(self._raw_customers)
        self.orders = copy.deepcopy(self._raw_orders)
        self.escalations = []
        self.idempotency_ledger = {}
        self.simulate_db_failure = False
        self._ticket_counter = 9000
        self._return_counter = 8802
        self._cancel_counter = 7701

        # Pre-populate idempotency ledger for orders that already have an active return/cancellation in seed data
        for order in self.orders:
            if order.get("active_return_id"):
                key = f"{order['order_id'].upper()}:return"
                self.idempotency_ledger[key] = {
                    "reference_id": order["active_return_id"],
                    "action": "return",
                    "order_id": order["order_id"],
                    "status": order["status"],
                    "created_at": order.get("return_created_at", "2026-10-28T14:20:00Z"),
                }
            if order.get("cancellation_id"):
                key = f"{order['order_id'].upper()}:cancel"
                self.idempotency_ledger[key] = {
                    "reference_id": order["cancellation_id"],
                    "action": "cancel",
                    "order_id": order["order_id"],
                    "status": order["status"],
                }

    def check_health(self) -> None:
        """Raise ToolExecutionError if database failure simulation is active."""
        if self.simulate_db_failure:
            raise ToolExecutionError(
                "503 Service Unavailable: Order & Fulfillment Database connection timed out after 5000ms."
            )

    def next_ticket_id(self) -> str:
        self._ticket_counter += 1
        return f"ESC-{self._ticket_counter}"

    def next_return_id(self) -> str:
        self._return_counter += 1
        return f"RET-{self._return_counter}"

    def next_cancel_id(self) -> str:
        self._cancel_counter += 1
        return f"CAN-{self._cancel_counter}"

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        self.check_health()
        norm_id = order_id.strip().upper()
        for order in self.orders:
            if order["order_id"].upper() == norm_id:
                return order
        return None

    def get_customer_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        self.check_health()
        norm_email = email.strip().lower()
        for customer in self.customers:
            if customer["email"].lower() == norm_email:
                return customer
        return None

