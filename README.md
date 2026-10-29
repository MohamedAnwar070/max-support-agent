# UrbanThread — Autonomous Customer Support Agent (SQLite + Hybrid LLM Architecture)

A production-structured Customer Support Agent built from scratch for **UrbanThread Apparel Co.** (100% fictional apparel brand featuring the *Classic Cotton T-Shirt*) for the **Maxify AI Candidate Task**.

---

## Deliverables Checklist & Repository Map

| # | Candidate Task Requirement | File / Directory Location |
|---|---|---|
| **1** | **Short PRD** (Customer needs, scope, assumptions, success criteria) | [`PRD.md`](./PRD.md) |
| **2** | **Fictional Data** (Relational SQLite DB seeded from fictional JSON — zero real PII/transactions) | [`data/urbanthread.db`](./data/urbanthread.db), [`data/products.json`](./data/products.json), [`data/policies.json`](./data/policies.json), [`data/policies.md`](./data/policies.md), [`data/customers.json`](./data/customers.json), [`data/orders.json`](./data/orders.json) |
| **3** | **Working Support Agent** (Centralized constants, `.env` config, SQLite ACID transactions, mock refunds with unique `request_key`, guardrails, multi-LLM backends) | [`src/constants.py`](./src/constants.py), [`src/config.py`](./src/config.py), [`src/data_store.py`](./src/data_store.py), [`src/guardrails.py`](./src/guardrails.py), [`src/tools.py`](./src/tools.py), [`src/llm_backends.py`](./src/llm_backends.py), [`src/agent.py`](./src/agent.py), [`cli.py`](./cli.py) |
| **4** | **Test Six Failure Types** (Missing info, conflicting policies, unauthorized access, prompt injection, tool failure, duplicate requests — Expected vs. Actual) | [`tests/test_failure_modes.py`](./tests/test_failure_modes.py), [`tests/FAILURE_TESTS_REPORT.md`](./tests/FAILURE_TESTS_REPORT.md) |
| **5** | **Repeatable Agent Evals** (Pass/fail criteria, run instructions, and actual results) | [`evals/eval_dataset.json`](./evals/eval_dataset.json), [`evals/run_evals.py`](./evals/run_evals.py), [`evals/EVAL_REPORT.md`](./evals/EVAL_REPORT.md), [`evals/results.json`](./evals/results.json) |
| **6** | **Setup README & Clean Credential-Free Package** | [`README.md`](./README.md), [`.env.example`](./.env.example) |

---

## How All Six Required Failure Tests Are Satisfied

| Failure Case | Required Expected Behavior | How UrbanThread Agent Enforces It | Test & Eval Verification |
|---|---|---|---|
| **1. Missing information** | Ask a targeted clarification | Entity extractor checks for `order_id` and `customer_email` prior to tool execution and asks specifically for the missing field(s). | `FT-01` / `EVAL-07` (**PASS**) |
| **2. Conflicting policies** | Escalate without guessing | Detects conflict between `POL-RET-02` (Final Sale has no exceptions) and `POL-VIP-01` (VIP Gold 14-day return guarantee covers Final Sale) and calls `escalate_to_human`. | `FT-02` / `EVAL-08` (**PASS**) |
| **3. Unauthorized order access** | Deny access without exposing order details | `get_order_status` & `process_return_or_cancel` verify `order.customer_email == customer_email` in SQLite and block access with zero leaked PII. | `FT-03` / `EVAL-09` (**PASS**) |
| **4. Prompt injection** | Ignore instructions that attempt to override system rules | Pre-tool input guardrail (`detect_prompt_injection`) blocks instruction overrides, `developer mode`, and data exfiltration attempts. | `FT-04` / `EVAL-10` (**PASS**) |
| **5. Tool failure** | Report that the action could not be verified; don't claim success | Catches `ToolExecutionError` (`503` SQLite outage), explicitly states the action **could not be verified**, never claims success, and escalates via `escalate_to_human`. | `FT-05` / `EVAL-11` (**PASS**) |
| **6. Duplicate request** | Return the original action result without repeating the action | SQLite `idempotency_ledger` matches the unique `request_key` (`REQ-...`) or `order_id:action` and returns the original mock refund reference (`REF-8801`, `$72.00`) without repeating the mutation. | `FT-06` / `EVAL-12` (**PASS**) |

---

## System Architecture

1. **Centralized Constants & `.env` Configuration (`src/constants.py`, `src/config.py`):**
   - All domain constants, regexes, SQLite DDL schemas, decision types, status codes, prompts, and tool schemas live exclusively in `src/constants.py`.
   - Runtime configuration loads automatically from `.env` via `src/config.py`.
2. **Hybrid SQLite + JSON Persistence Layer (`src/data_store.py`):**
   - Fictional JSON seed files (`data/*.json`) hydrate a normalized relational **SQLite database** (`data/urbanthread.db`) across 8 tables: `products`, `policies`, `policy_conflicts`, `customers`, `orders`, `order_items`, `idempotency_ledger`, and `escalations`.
   - Mock refunds and cancellations execute inside atomic SQLite transactions with a unique `request_key` stored in `idempotency_ledger`.
3. **Multi-Backend LLM Synthesis (`src/llm_backends.py`):**
   - **`claude_code` (`ClaudeCodeCLIBackend`):** Invokes the locally authenticated **Claude Code Pro CLI** (`claude -p`) without requiring raw API keys.
   - **`openai` (`OpenAIBackend`):** Invokes OpenAI Chat Completions API (`OPENAI_API_KEY`).
   - **`hybrid` (Default):** Deterministic SQLite tool routing + grounded response synthesizer for `<1s` reproducible execution.

---

## Setup & Execution Instructions

### 1. Environment Configuration
```bash
cp .env.example .env
```

### 2. Run the 8-Scenario Demo Walkthrough
```bash
python cli.py --demo

# With live Claude Code Pro CLI
python cli.py --demo --backend claude_code --live-llm

# With live OpenAI API
python cli.py --demo --backend openai --live-llm
```

### 3. Run the Six Failure Mode Unit Tests
```bash
python tests/test_failure_modes.py
```
Executes all unit tests against SQLite and generates [`tests/FAILURE_TESTS_REPORT.md`](./tests/FAILURE_TESTS_REPORT.md).

### 4. Run Repeatable Agent Evaluations (12 Cases)
```bash
python evals/run_evals.py

# Or evaluate with live Claude Code Pro CLI
python evals/run_evals.py --backend claude_code --live-llm
```
Generates [`evals/results.json`](./evals/results.json) and [`evals/EVAL_REPORT.md`](./evals/EVAL_REPORT.md).
