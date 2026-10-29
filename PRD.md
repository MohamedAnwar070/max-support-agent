# Product Requirements Document (PRD): MaxWear Autonomous Customer Support Agent

**Document Owner:** Engineering & AI Architecture  
**Status:** Approved for v1.0 Release  
**Target Domain:** E-Commerce Apparel ("MaxWear Apparel Co.")

---

## 1. Executive Summary
MaxWear Apparel Co. processes thousands of daily customer inquiries regarding sizing, garment care, shipping timelines, order tracking, returns, and cancellations. This PRD defines the requirements, safety boundaries, and evaluation criteria for an autonomous **AI Customer Support Agent** capable of resolving routine apparel support queries end-to-end while reliably clarifying missing context, blocking unauthorized access or adversarial inputs, and escalating ambiguous or high-risk cases to human specialists.

---

## 2. Customer Needs & Problem Statement

| Customer Persona / Need | Pain Point Today | How the Agent Solves It |
|---|---|---|
| **Pre-Purchase Shopper** | Needs quick answers on sizing fit, fabric composition, stock availability, and return eligibility before buying. | Instant catalog lookup (`lookup_products`) with accurate stock, fit notes, care instructions, and Final Sale warnings. |
| **Anxious Buyer (Order Tracking)** | Wants real-time order status and carrier tracking numbers without waiting in a support queue. | Authenticated order lookup (`get_order_status`) returning real-time fulfillment state and tracking details. |
| **Post-Purchase Returner / Exchanger** | Needs a frictionless way to initiate returns or cancel unshipped orders when sizing or preference changes. | Automated eligibility verification and return/cancellation execution (`create_return_or_cancel`) with idempotency protection. |
| **Edge-Case / Frustrated Customer** | Faces conflicting policy language (e.g., VIP perks vs. Final Sale exclusions) or system errors and gets stuck in unhelpful bot loops. | Deterministic ambiguity & error detection that immediately halts automated execution and triggers a structured human handoff (`escalate_to_human`). |

---

## 3. Scope

### 3.1 In-Scope (v1.0)
1. **Product & Sizing Advisory:** Answering questions on SKU pricing, sizes in stock, fit recommendations, fabric care, and Final Sale status.
2. **Policy Q&A:** Explaining return windows, shipping speeds, exchange procedures, loyalty tier benefits, and damaged-item claims.
3. **Authenticated Order Inquiry:** Providing order status, items ordered, shipping address city/state, and tracking numbers **only after verifying customer ownership** (`order_id` + matching `customer_email` or `customer_id`).
4. **Self-Service Returns & Cancellations:**
   - Initiating returns for `delivered` orders within the 30-day window on eligible (non-Final Sale) items.
   - Cancelling orders currently in `processing` state prior to carrier dispatch.
   - Deduplicating repeated requests via idempotency keys / existing request state.
5. **Proactive Clarification:** Prompting the customer for required identifiers (`order_id`, `customer_email`, SKU/item) when omitted.
6. **Human Escalation (`escalate_to_human`):** Structured ticket generation with severity and context when:
   - Store policies conflict (e.g., VIP Gold 60-day return guarantee vs. Final Sale non-returnable policy).
   - A backend tool fails or times out (`ToolExecutionError`).
   - An action is unsafe, high-value override is requested, or the user explicitly requests a human agent.

### 3.2 Out-of-Scope (v1.0)
- **Payment & Billing Mutations:** Processing live credit card charges, modifying payment methods, or issuing arbitrary goodwill cash credits without an underlying order item.
- **Post-Shipment Logistics Changes:** Rerouting packages already in transit (`shipped` state) with third-party carriers.
- **Unauthenticated Account Modifications:** Changing account emails, passwords, or loyalty tiers via chat.

---

## 4. Assumptions & Technical Architecture

1. **Synthetic Data Isolation:** All products, policies, customer profiles, and order records are 100% fictional (`data/*.json`). No real PII or financial transactions are processed.
2. **Multi-Backend LLM Support:**
   - Supports **OpenAI API** (`gpt-4o-mini` / `gpt-4o`), **Claude Code Agent CLI** (`claude -p` using local Pro authentication without requiring raw API keys in code), and a **Deterministic Policy/Tool Engine** so tests and evaluations are 100% reproducible in any environment.
3. **Defense-in-Depth Guardrails:**
   - **Pre-LLM Input Guardrail:** Scans user prompts for prompt injection, jailbreaks, system-prompt extraction, and privilege escalation.
   - **Tool-Level Authorization:** Every order tool enforces `order.customer_email.lower() == provided_email.lower()` in Python code—never relying solely on the LLM prompt for access control.
   - **Idempotency Layer:** Every state-mutating action checks existing return/cancellation ledgers to prevent duplicate refunds or duplicate RMA generation.

---

## 5. Success Criteria & Evaluation Metrics

| Metric Category | Requirement / Failure Mode Covered | Target Threshold | Verification Method |
|---|---|---|---|
| **Missing Information Handling** | Agent requests `order_id` / `email` before invoking order tools when omitted. | **100% Pass** | `test_01_missing_information` & Eval Suite |
| **Conflicting Policy Escalation** | Agent detects VIP vs. Final Sale policy conflict, refuses to guess, and escalates to human. | **100% Pass** | `test_02_conflicting_policies` & Eval Suite |
| **Access Control & Security** | Zero cross-customer data leakage when Customer A queries Customer B's order. | **100% Block Rate (0 Leaks)** | `test_03_unauthorized_access` & Eval Suite |
| **Prompt Injection Resilience** | Zero system prompt leaks, data dumps, or unauthorized refunds under adversarial injection. | **100% Block Rate** | `test_04_prompt_injection` & Eval Suite |
| **Tool Fault Tolerance** | Graceful degradation + human escalation when backend DB/tool throws `503 Service Unavailable`. | **100% Pass (0 Hallucinations)** | `test_05_tool_failure` & Eval Suite |
| **Idempotency / Duplicate Handling** | Repeated return/cancel requests return existing RMA reference without double-processing. | **100% Pass** | `test_06_duplicate_requests` & Eval Suite |
| **Overall Repeatable Eval Suite** | End-to-end automated evaluation across happy paths + failure scenarios (`evals/run_evals.py`). | **>= 95% Pass Rate (Target: 100%)** | Automated JSON & Markdown Eval Report |

