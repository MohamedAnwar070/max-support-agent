from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .config import get_settings
from .constants import (
    INITIAL_CANCEL_COUNTER,
    INITIAL_RETURN_COUNTER,
    INITIAL_TICKET_COUNTER,
    SQLITE_SCHEMA_DDL,
)


class ToolExecutionError(RuntimeError):
    pass


class DataStore:
    def __init__(
        self,
        data_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        settings = get_settings()
        self.data_dir = Path(data_dir or settings.data_dir)
        self.db_path = Path(db_path or settings.sqlite_db_path)

        self._raw_products: List[Dict[str, Any]] = self._load_json("products.json")
        self._raw_policies: Dict[str, Any] = self._load_json("policies.json")
        self._raw_customers: List[Dict[str, Any]] = self._load_json("customers.json")
        self._raw_orders: List[Dict[str, Any]] = self._load_json("orders.json")

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        self.simulate_db_failure: bool = False
        self._ticket_counter: int = INITIAL_TICKET_COUNTER
        self._return_counter: int = INITIAL_RETURN_COUNTER
        self._cancel_counter: int = INITIAL_CANCEL_COUNTER

        self.reset()

    def _load_json(self, filename: str) -> Any:
        file_path = self.data_dir / filename
        return json.loads(file_path.read_text(encoding="utf-8"))

    def reset(self) -> None:
        self.simulate_db_failure = False
        self._ticket_counter = INITIAL_TICKET_COUNTER
        self._return_counter = INITIAL_RETURN_COUNTER
        self._cancel_counter = INITIAL_CANCEL_COUNTER

        with self.conn:
            self.conn.executescript(SQLITE_SCHEMA_DDL)
            for table in (
                "order_items",
                "orders",
                "products",
                "policies",
                "policy_conflicts",
                "customers",
                "idempotency_ledger",
                "escalations",
            ):
                self.conn.execute(f"DELETE FROM {table}")

            for p in self._raw_products:
                self.conn.execute(
                    """
                    INSERT INTO products (
                        product_id, name, category, price, original_price, currency,
                        sizes_in_stock_json, sizes_out_of_stock_json, fit_guide,
                        fabric_and_care, final_sale, clearance, description
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p["product_id"],
                        p["name"],
                        p["category"],
                        p["price"],
                        p.get("original_price"),
                        p["currency"],
                        json.dumps(p["sizes_in_stock"]),
                        json.dumps(p["sizes_out_of_stock"]),
                        p["fit_guide"],
                        p["fabric_and_care"],
                        int(bool(p.get("final_sale"))),
                        int(bool(p.get("clearance"))),
                        p["description"],
                    ),
                )

            for pol in self._raw_policies.get("policies", []):
                self.conn.execute(
                    """
                    INSERT INTO policies (
                        policy_id, topic, title, rule, applies_to, return_window_days
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pol["policy_id"],
                        pol["topic"],
                        pol["title"],
                        pol["rule"],
                        pol["applies_to"],
                        pol.get("return_window_days"),
                    ),
                )

            for conf in self._raw_policies.get("known_conflicts", []):
                self.conn.execute(
                    """
                    INSERT INTO policy_conflicts (
                        conflict_id, policies_involved_json, condition, description, required_action
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        conf["conflict_id"],
                        json.dumps(conf["policies_involved"]),
                        conf["condition"],
                        conf["description"],
                        conf["required_action"],
                    ),
                )

            for c in self._raw_customers:
                self.conn.execute(
                    """
                    INSERT INTO customers (
                        customer_id, name, email, loyalty_tier, joined_date, phone
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c["customer_id"],
                        c["name"],
                        c["email"],
                        c["loyalty_tier"],
                        c["joined_date"],
                        c["phone"],
                    ),
                )

            for o in self._raw_orders:
                self.conn.execute(
                    """
                    INSERT INTO orders (
                        order_id, customer_id, customer_email, customer_name, loyalty_tier,
                        status, order_date, delivery_date, days_since_delivery,
                        estimated_delivery, shipping_city, tracking_number, total_amount,
                        active_return_id, cancellation_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        o["order_id"],
                        o["customer_id"],
                        o["customer_email"],
                        o["customer_name"],
                        o["loyalty_tier"],
                        o["status"],
                        o["order_date"],
                        o.get("delivery_date"),
                        o.get("days_since_delivery"),
                        o.get("estimated_delivery"),
                        o["shipping_city"],
                        o.get("tracking_number"),
                        o["total_amount"],
                        o.get("active_return_id"),
                        o.get("cancellation_id"),
                    ),
                )
                for item in o.get("items", []):
                    self.conn.execute(
                        """
                        INSERT INTO order_items (
                            order_id, product_id, name, size, quantity, unit_price, final_sale
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            o["order_id"],
                            item["product_id"],
                            item["name"],
                            item["size"],
                            item["quantity"],
                            item["unit_price"],
                            int(bool(item.get("final_sale"))),
                        ),
                    )
                if o.get("active_return_id"):
                    self.conn.execute(
                        """
                        INSERT INTO idempotency_ledger (
                            idempotency_key, reference_id, action, order_id, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"{o['order_id'].upper()}:return",
                            o["active_return_id"],
                            "return",
                            o["order_id"],
                            o["status"],
                            o.get("return_created_at", "2026-10-28T14:20:00Z"),
                        ),
                    )
                if o.get("cancellation_id"):
                    self.conn.execute(
                        """
                        INSERT INTO idempotency_ledger (
                            idempotency_key, reference_id, action, order_id, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"{o['order_id'].upper()}:cancel",
                            o["cancellation_id"],
                            "cancel",
                            o["order_id"],
                            o["status"],
                            "2026-10-28T14:20:00Z",
                        ),
                    )

    def check_health(self) -> None:
        if self.simulate_db_failure:
            raise ToolExecutionError(
                "503 Service Unavailable: SQLite Order & Fulfillment Database connection timed out."
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

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "product_id": row["product_id"],
            "name": row["name"],
            "category": row["category"],
            "price": row["price"],
            "original_price": row["original_price"],
            "currency": row["currency"],
            "sizes_in_stock": json.loads(row["sizes_in_stock_json"]),
            "sizes_out_of_stock": json.loads(row["sizes_out_of_stock_json"]),
            "fit_guide": row["fit_guide"],
            "fabric_and_care": row["fabric_and_care"],
            "final_sale": bool(row["final_sale"]),
            "clearance": bool(row["clearance"]),
            "description": row["description"],
        }

    def _hydrate_order(self, row: sqlite3.Row) -> Dict[str, Any]:
        item_rows = self.conn.execute(
            "SELECT product_id, name, size, quantity, unit_price, final_sale FROM order_items WHERE order_id = ?",
            (row["order_id"],),
        ).fetchall()
        items = [
            {
                "product_id": ir["product_id"],
                "name": ir["name"],
                "size": ir["size"],
                "quantity": ir["quantity"],
                "unit_price": ir["unit_price"],
                "final_sale": bool(ir["final_sale"]),
            }
            for ir in item_rows
        ]
        return {
            "order_id": row["order_id"],
            "customer_id": row["customer_id"],
            "customer_email": row["customer_email"],
            "customer_name": row["customer_name"],
            "loyalty_tier": row["loyalty_tier"],
            "status": row["status"],
            "order_date": row["order_date"],
            "delivery_date": row["delivery_date"],
            "days_since_delivery": row["days_since_delivery"],
            "estimated_delivery": row["estimated_delivery"],
            "shipping_city": row["shipping_city"],
            "tracking_number": row["tracking_number"],
            "total_amount": row["total_amount"],
            "active_return_id": row["active_return_id"],
            "cancellation_id": row["cancellation_id"],
            "items": items,
        }

    @property
    def products(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM products ORDER BY product_id").fetchall()
        return [self._row_to_product(r) for r in rows]

    @property
    def customers(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM customers ORDER BY customer_id").fetchall()
        return [dict(r) for r in rows]

    @property
    def orders(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM orders ORDER BY order_id").fetchall()
        return [self._hydrate_order(r) for r in rows]

    @property
    def escalations(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM escalations ORDER BY ticket_id").fetchall()
        return [dict(r) for r in rows]

    def query_products(self, query: str = "") -> List[Dict[str, Any]]:
        self.check_health()
        all_prods = self.products
        q = (query or "").strip().lower()
        if not q or q in ("all", "catalog", "products"):
            return all_prods

        tokens = [t for t in q.replace("-", " ").split() if len(t) > 1]
        matches = []
        for p in all_prods:
            haystack = (
                f"{p['product_id']} {p['name']} {p['category']} {p['description']} {p['fabric_and_care']}"
            ).lower()
            if q in haystack or any(tok in haystack for tok in tokens):
                matches.append(p)
        return matches or all_prods

    def query_policies(self, topic: str = "all") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        self.check_health()
        pol_rows = self.conn.execute("SELECT * FROM policies ORDER BY policy_id").fetchall()
        all_policies = [dict(r) for r in pol_rows]

        conf_rows = self.conn.execute("SELECT * FROM policy_conflicts ORDER BY conflict_id").fetchall()
        conflicts = [
            {
                "conflict_id": r["conflict_id"],
                "policies_involved": json.loads(r["policies_involved_json"]),
                "condition": r["condition"],
                "description": r["description"],
                "required_action": r["required_action"],
            }
            for r in conf_rows
        ]

        t = (topic or "all").strip().lower()
        if t in ("all", ""):
            return all_policies, conflicts

        filtered = [
            p
            for p in all_policies
            if t in p["topic"].lower() or t in p["title"].lower() or t in p["rule"].lower()
        ]
        return (filtered or all_policies), conflicts

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        self.check_health()
        norm_id = order_id.strip().upper()
        row = self.conn.execute(
            "SELECT * FROM orders WHERE UPPER(order_id) = ?",
            (norm_id,),
        ).fetchone()
        return self._hydrate_order(row) if row else None

    def get_idempotency_record(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        self.check_health()
        row = self.conn.execute(
            "SELECT * FROM idempotency_ledger WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return dict(row) if row else None

    def record_return(self, order_id: str, return_id: str, idempotency_key: str) -> None:
        self.check_health()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.conn:
            self.conn.execute(
                "UPDATE orders SET status = 'return_initiated', active_return_id = ? WHERE UPPER(order_id) = ?",
                (return_id, order_id.upper()),
            )
            self.conn.execute(
                """
                INSERT INTO idempotency_ledger (
                    idempotency_key, reference_id, action, order_id, status, created_at
                ) VALUES (?, ?, 'return', ?, 'return_initiated', ?)
                """,
                (idempotency_key, return_id, order_id.upper(), now_iso),
            )

    def record_cancellation(self, order_id: str, cancel_id: str, idempotency_key: str) -> None:
        self.check_health()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.conn:
            self.conn.execute(
                "UPDATE orders SET status = 'cancelled', cancellation_id = ? WHERE UPPER(order_id) = ?",
                (cancel_id, order_id.upper()),
            )
            self.conn.execute(
                """
                INSERT INTO idempotency_ledger (
                    idempotency_key, reference_id, action, order_id, status, created_at
                ) VALUES (?, ?, 'cancel', ?, 'cancelled', ?)
                """,
                (idempotency_key, cancel_id, order_id.upper(), now_iso),
            )

    def record_escalation(self, ticket_dict: Dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO escalations (
                    ticket_id, category, severity, reason, order_id, customer_email
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_dict["ticket_id"],
                    ticket_dict["category"],
                    ticket_dict["severity"],
                    ticket_dict["reason"],
                    ticket_dict.get("order_id"),
                    ticket_dict.get("customer_email"),
                ),
            )
