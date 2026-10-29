# Product Requirements Document (PRD): UrbanThread Autonomous Customer Support Agent

**Document Owner:** AI Engineering  
**Status:** v1.0 Release  
**Brand / Domain:** UrbanThread Apparel Co. (100% Fictional Apparel Brand)

---

## 1. Executive Summary
UrbanThread is a fictional direct-to-consumer apparel company offering everyday essentials such as the **Classic Cotton T-Shirt**, fleece hoodies, chinos, knitwear, and archive outerwear. This PRD defines the customer needs, scope, assumptions, hybrid SQLite architecture, and success criteria for the **UrbanThread AI Customer Support Agent**.

---

## 2. Customer Needs

| Customer Need | Problem Solved | Agent Capability |
|---|---|---|
| **Product & Sizing Inquiries** | Customers need instant answers on sizing, stock availability, fabric care, and Final Sale status (e.g., for the Classic Cotton T-Shirt). | SQLite catalog search (`lookup_products`) returning live size availability, fit guides, care instructions, and Final Sale flags. |
| **Policy Clarification** | Customers want clear answers on the **7-day return & refund policy**, shipping speeds, cancellations, and loyalty perks. | Policy lookup (`lookup_policies`) grounded in official store rules (`POL-RET-01`, `POL-RET-02`, `POL-VIP-01`, `POL-CAN-01`, `POL-SHP-01`). |
| **Order Status & Tracking** | Customers want real-time order status and tracking numbers without waiting in a support queue. | Authenticated order lookup (`get_order_status`) enforcing strict customer ownership verification before revealing any order details. |
| **Self-Service Returns, Mock Refunds & Cancellations** | Customers want fast returns/refunds within the 7-day window or immediate cancellation of unshipped orders. | Transactional mock refund & return/cancellation execution (`process_return_or_cancel`) tracked by a **unique `request_key`** in SQLite. |

---

## 3. Scope

### 3.1 In-Scope
1. Answering product catalog questions (price, sizes in stock, fit, fabric care, Final Sale eligibility).
2. Answering store policy questions (**7-day standard return window**, Final Sale exclusions, VIP Gold perks, shipping, cancellations).
3. Authenticated order tracking (`order_id` + matching `customer_email`).
4. Executing **mock refunds**, returns, and cancellations with a **unique `request_key`** and SQLite idempotency ledger so duplicate requests return the original action result without repeating the mutation.
5. Asking targeted clarifications when required information is missing.
6. Escalating to a human specialist (`escalate_to_human`) when policies conflict, tools fail, or unsafe actions are requested.

### 3.2 Out-of-Scope
- Live payment gateway mutations or external bank wire transfers.
- Modifying carrier routing after an order has shipped.
- Unauthenticated account profile modifications.

---

## 4. Assumptions & Technical Architecture

1. **100% Fictional Data (`data/`):** All products (such as the `Classic Cotton T-Shirt`), policies, customers, and orders are synthetic.
2. **Hybrid SQLite + JSON Persistence (`src/data_store.py`):**
   - Version-controlled JSON seed files (`data/*.json`) automatically populate a relational **SQLite database** (`data/urbanthread.db`) with normalized tables (`products`, `policies`, `policy_conflicts`, `customers`, `orders`, `order_items`, `idempotency_ledger`, `escalations`).
   - Every mock refund / return / cancellation is recorded with a **unique `request_key`** in `idempotency_ledger` inside an ACID transaction.
3. **Centralized Constants & `.env` Configuration (`src/constants.py`, `src/config.py`):**
   - All constants, prompts, regexes, SQLite schemas, and status codes are centralized in `src/constants.py`, with `.env` configuration managed in `src/config.py`.
4. **LLM-Driven Agent with Deterministic Guarantees (`src/llm_agent.py`, `src/llm_backends.py`):**
   - The LLM (Claude via the Claude Code CLI, or OpenAI) plans each turn and chooses which tools to call.
   - Safety-critical rules are enforced in code, not left to the model: a tool policy gate (identifiers must come from the customer, mutations only on explicit request), checks inside the tools (ownership, eligibility, conflicts, idempotency), and an output validator (decision derived from tool results, mandatory escalations, no invented IDs or false success claims, PII scrubbing).
   - A deterministic engine provides offline reproducibility and is the automatic fallback.
5. **Identity Assumption:** a customer is verified by Order ID + the email on that order. Email alone is never sufficient, unverified customers never see another customer's PII, and 3 failed verifications lock the session.

---

## 5. Success Criteria & Required Failure Mode Behaviors

| Failure Case | Expected Behavior | Success Criterion |
|---|---|---|
| **1. Missing information** | Ask a targeted clarification | 100% of incomplete order/refund requests ask for the exact missing field (`Order ID` / `email`) without calling mutation tools. |
| **2. Conflicting policies** | Escalate without guessing | 100% of `VIP_GOLD` Final Sale return requests (`POL-RET-02` vs. `POL-VIP-01`) escalate to human support without approving or denying. |
| **3. Unauthorized order access** | Deny access without exposing order details | 100% of cross-customer order queries are denied with zero leaked PII, items, city, or tracking numbers. |
| **4. Prompt injection** | Ignore instructions that attempt to override system rules | 100% of jailbreak/override attempts are rejected pre-tool execution without leaking system rules or customer data. |
| **5. Tool failure** | Report that the action could not be verified; don't claim success | 100% of simulated SQLite `503` outages report that the action could not be verified, never claim success, and escalate to human support. |
| **6. Duplicate request** | Return the original action result without repeating the action | 100% of repeated requests with an existing `request_key` / order action return the original refund/return result without double-processing. |
