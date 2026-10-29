# Sunnystep — Autonomous Customer Support Agent (SQLite + Hybrid LLM Architecture)

A production-structured Customer Support Agent built from scratch for **Sunnystep** (Ergonomic Footwear & Apparel) as part of the **Maxify AI Candidate Task**.

---

## Deliverables Checklist & Repository Map

| # | Candidate Task Requirement | File / Directory Location |
|---|---|---|
| **1** | **Short PRD** (Customer needs, scope, assumptions, success criteria) | [`PRD.md`](./PRD.md) |
| **2** | **Fictional Data** (Relational SQLite DB seeded from fictional JSON — zero real PII/transactions) | [`data/sunnystep.db`](./data/sunnystep.db), [`data/products.json`](./data/products.json), [`data/policies.json`](./data/policies.json), [`data/policies.md`](./data/policies.md), [`data/customers.json`](./data/customers.json), [`data/orders.json`](./data/orders.json) |
| **3** | **Working Support Agent** (Centralized constants, `.env` config, SQLite ACID transactions, guardrails, multi-LLM backends) | [`src/constants.py`](./src/constants.py), [`src/config.py`](./src/config.py), [`src/data_store.py`](./src/data_store.py), [`src/guardrails.py`](./src/guardrails.py), [`src/tools.py`](./src/tools.py), [`src/llm_backends.py`](./src/llm_backends.py), [`src/agent.py`](./src/agent.py), [`cli.py`](./cli.py) |
| **4** | **Test Six Failure Types** (Missing info, conflicting policies, unauthorized access, prompt injection, tool failure, duplicate requests — Expected vs. Actual) | [`tests/test_failure_modes.py`](./tests/test_failure_modes.py), [`tests/FAILURE_TESTS_REPORT.md`](./tests/FAILURE_TESTS_REPORT.md) |
| **5** | **Repeatable Agent Evals** (Pass/fail criteria, run instructions, and actual results) | [`evals/eval_dataset.json`](./evals/eval_dataset.json), [`evals/run_evals.py`](./evals/run_evals.py), [`evals/EVAL_REPORT.md`](./evals/EVAL_REPORT.md), [`evals/results.json`](./evals/results.json) |
| **6** | **Setup README & Clean Credential-Free Package** | [`README.md`](./README.md), [`.env.example`](./.env.example) |

---

## System Architecture

1. **Centralized Constants & `.env` Configuration (`src/constants.py`, `src/config.py`):**
   - All domain constants, regexes, SQLite DDL schemas, decision types, status codes, prompts, and tool schemas live in `src/constants.py`.
   - Runtime configuration loads automatically from `.env` via `src/config.py`.
2. **Hybrid SQLite + JSON Persistence Layer (`src/data_store.py`):**
   - Version-controlled JSON seed files (`data/*.json`) hydrate a normalized relational **SQLite database** (`data/sunnystep.db`) across 8 tables: `products`, `policies`, `policy_conflicts`, `customers`, `orders`, `order_items`, `idempotency_ledger`, and `escalations`.
   - State-mutating operations (`record_return`, `record_cancellation`, `record_escalation`) execute inside atomic SQLite transactions, enforcing primary-key idempotency on `idempotency_ledger`.
3. **Defense-in-Depth Guardrails (`src/guardrails.py`):**
   - Pre-execution prompt injection detection blocks instruction overrides, persona hijacks (`developer mode`), and data exfiltration before any SQLite tool can execute.
   - Post-execution PII scrubbing verifies zero cross-customer email, phone, or tracking leakage.
4. **Multi-Backend LLM Synthesis (`src/llm_backends.py`):**
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
