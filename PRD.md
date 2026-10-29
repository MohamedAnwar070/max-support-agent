# Product Requirements Document (PRD): Sunnystep Autonomous Customer Support Agent

**Document Owner:** AI Platform & Support Engineering  
**Status:** v1.0 Release  
**Brand / Domain:** Sunnystep (Ergonomic Footwear & Apparel)

---

## 1. Executive Summary
Sunnystep designs ergonomic walking shoes, recovery footwear, and movement apparel. Support volume centers on sizing/fit guidance, garment and shoe care, order tracking, returns, and pre-shipment cancellations. This document specifies the architecture, scope, safety boundaries, and evaluation framework for the **Sunnystep AI Customer Support Agent**.

---

## 2. Customer Needs & Problem Statement

| Customer Segment | Core Need | Agent Capability |
|---|---|---|
| **Prospective Buyer** | Accurate guidance on arch support, sizing fit, stock availability, care instructions, and Final Sale eligibility. | Real-time SQLite product catalog search (`lookup_products`) returning stock matrices, fit notes, and Final Sale flags. |
| **Order Tracker** | Immediate visibility into fulfillment status, shipping carrier tracking, and delivery dates. | Authenticated SQLite order lookup (`get_order_status`) enforcing strict customer email ownership verification. |
| **Return / Cancellation Requester** | Self-service return initiation for delivered items or immediate cancellation of unshipped orders. | Transactional return/cancellation processing (`process_return_or_cancel`) with SQLite `UNIQUE` idempotency keys. |
| **Complex / Edge-Case Customer** | Transparent handling when store policies conflict or backend infrastructure fails, without hallucinated answers. | Deterministic conflict and fault detection that halts mutation and creates a structured human handoff (`escalate_to_human`). |

---

## 3. Scope

### 3.1 In-Scope
1. **Product & Sizing Consultation:** Answering questions on SKU pricing, available sizes, ergonomic fit recommendations, care rules, and Final Sale status.
2. **Policy Q&A:** Explaining the 30-day standard return policy (`POL-RET-01`), Clearance/Final Sale exclusions (`POL-RET-02`), VIP Gold loyalty benefits (`POL-VIP-01`), cancellation rules (`POL-CAN-01`), and shipping timelines (`POL-SHP-01`).
3. **Authenticated Order Lookup:** Returning order state, line items, destination city, and tracking numbers only when `(order_id, customer_email)` matches the relational record.
4. **Transactional Returns & Cancellations:**
   - Creating returns (`RET-*`) for `delivered` orders within the eligible return window.
   - Cancelling (`CAN-*`) orders in `processing` state prior to dispatch.
   - Enforcing idempotency via SQLite `idempotency_ledger` to prevent duplicate RMAs or double refunds.
5. **Missing Context Clarification:** Prompting the user when required identifiers (`order_id`, `customer_email`) are missing.
6. **Human Specialist Escalation (`escalate_to_human`):** Generating structured tickets (`ESC-*`) when policies conflict, tools fail, or unsafe actions are requested.

### 3.2 Out-of-Scope
- Direct credit card re-billing, manual cash/wire transfers, or unverified goodwill payouts.
- Modifying carrier routing after an order has entered `shipped` state.
- Mutating customer identity records (`email`, `loyalty_tier`) through chat.

---

## 4. Assumptions & Hybrid Architecture

1. **Hybrid SQLite + JSON Data Architecture:**
   - Seed datasets live in version-controlled JSON (`data/*.json`) and are automatically loaded into a relational **SQLite database** (`data/sunnystep.db`) with normalized tables (`products`, `policies`, `policy_conflicts`, `customers`, `orders`, `order_items`, `idempotency_ledger`, `escalations`).
2. **Multi-Backend LLM Engine:**
   - Supports **OpenAI API** (`OPENAI_API_KEY`), **Claude Code Pro CLI** (`claude -p` using local Pro authentication), and a **Hybrid Deterministic Engine** controlled via `.env` (`src/config.py`).
3. **Centralized Constants & Configuration:**
   - All prompts, regex patterns, decision enums, error codes, and tool schemas are centralized in `src/constants.py` and `src/config.py`.

---

## 5. Success Criteria

| Failure Mode / Metric | Requirement | Target |
|---|---|---|
| **1. Missing Information** | Request missing `order_id` and `email` before invoking order tools. | **100% Pass** |
| **2. Conflicting Policies** | Detect conflict between `POL-RET-02` (Final Sale) and `POL-VIP-01` (VIP Gold) and escalate to human. | **100% Pass** |
| **3. Unauthorised Order Access** | Block cross-customer order queries with zero PII leakage. | **100% Block Rate** |
| **4. Prompt Injection** | Block instruction overrides, developer mode jailbreaks, and data exfiltration pre-tool execution. | **100% Block Rate** |
| **5. Tool Failure** | Gracefully catch SQLite/DB `503` outages without hallucinating data and escalate to human. | **100% Pass** |
| **6. Duplicate Requests** | Enforce SQLite idempotency ledger on repeated return/cancellation calls. | **100% Pass** |
| **Repeatable Evaluation Suite** | End-to-end automated grading across 12 scenarios (`evals/run_evals.py`). | **100% Pass (12/12)** |
