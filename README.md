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
| **3. Unauthorized order access** | Deny access without exposing order details | `get_order_status` & `process_return_or_cancel` verify `order.customer_email == customer_email` in SQLite; not-found and wrong-owner get one identical reply (no enumeration); 3 failures lock the session. | `FT-03` / `EVAL-09` (**PASS**) |
| **4. Prompt injection** | Ignore instructions that attempt to override system rules | Pre-LLM guardrail blocks overrides, persona hijacks, fake `SYSTEM:` messages and exfiltration; anything that slips through still meets the tool policy gate and output validator. | `FT-04` / `EVAL-10` (**PASS**) |
| **5. Tool failure** | Report that the action could not be verified; don't claim success | Catches `ToolExecutionError` (`503` SQLite outage), explicitly states the action **could not be verified**, never claims success, and escalates via `escalate_to_human`. | `FT-05` / `EVAL-11` (**PASS**) |
| **6. Duplicate request** | Return the original action result without repeating the action | SQLite `idempotency_ledger` keyed by `order_id:action` returns the original mock refund reference (`REF-8801`, `$72.00`) without repeating the mutation; a request key belonging to another order is rejected. | `FT-06` / `EVAL-12` (**PASS**) |

---

## System Architecture

### Two engines, one set of guarantees

| Mode | Who decides | When |
|---|---|---|
| **LLM agent** (`--live-llm`, `--backend claude_code` / `openai`) | The LLM plans each turn: it chooses tools, reads results, calls more tools, then writes the reply ([`src/llm_agent.py`](./src/llm_agent.py)). | Production-style runs |
| **Deterministic** (default `hybrid`) | Rule-based router over the same tools. Also the automatic fallback if the LLM is unavailable or returns unusable output. | Offline, CI, reproducible evals |

### Defence in depth: the LLM proposes, code disposes

```
customer message
  -> [1] input guardrails      regex injection / unsafe-request filter, verification lockout
  -> [2] LLM planner loop      JSON protocol, max 6 steps; customer text wrapped as <customer_message> data
  -> [3] tool policy gate      identifiers must come from the customer; mutation only on an explicit
                               return/cancel request; 1 mutation + 1 escalation per turn
  -> [4] tools (source of truth)  ownership check, return windows, Final Sale, conflicts, idempotency
  -> [5] output validator      decision derived from tool results (not the LLM's claim); mandatory
                               escalations auto-created; invented IDs, false success claims and
                               system-prompt leaks rejected -> safe template
  -> [6] PII scrubber          other customers' email/name/phone/city/tracking redacted unless verified
```

Key security properties (each covered by a test):

- **No cross-customer data.** The idempotency ledger is scoped to `order_id:action`; a request key from another order is rejected (`REQUEST_KEY_MISMATCH`) instead of returning that order's refund.
- **No order enumeration.** "Order not found" and "order belongs to someone else" return the identical message.
- **Brute-force lockout.** 3 failed verifications lock order lookups for the session and open a security ticket.
- **Email alone is not identity.** `/verify` needs email **and** a matching Order ID; PII is unlocked only for emails that passed an ownership check.
- **The LLM cannot fabricate identity or act unasked.** It can only use order IDs / emails / request keys the customer typed, and can only mutate an order the customer explicitly asked to return or cancel. Exchanges are escalated, never silently refunded.
- **Sandboxed Claude CLI.** `claude -p` runs with `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, no session persistence, prompt over stdin, from a temp directory, so injected text cannot read files or run commands.

### Modules

1. **Constants & config** ([`src/constants.py`](./src/constants.py), [`src/config.py`](./src/config.py)): regexes, schemas, decision/status codes, planner prompt; `.env` loading.
2. **SQLite persistence** ([`src/data_store.py`](./src/data_store.py)): JSON seeds hydrate 8 tables; refunds and cancellations are atomic transactions recorded in `idempotency_ledger`.
3. **Tools** ([`src/tools.py`](./src/tools.py)): the five tools, with ownership, eligibility, conflict and duplicate checks enforced inside the tool.
4. **LLM agent** ([`src/llm_agent.py`](./src/llm_agent.py)): planner loop and `ToolPolicyGate`.
5. **Backends** ([`src/llm_backends.py`](./src/llm_backends.py)): `claude_code` (local Claude Code CLI, no API key) and `openai` (Chat Completions, JSON mode).
6. **Guardrails** ([`src/guardrails.py`](./src/guardrails.py)): injection filter, grounding / success-claim / prompt-leak validators, PII scrubber.

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

### 3. Run the Tests
```bash
python tests/test_failure_modes.py   # six failure types + security regressions (10 tests)
python tests/test_llm_agent.py       # LLM-driven agent vs. a scripted adversarial model (16 tests)
```
`test_failure_modes.py` also generates [`tests/FAILURE_TESTS_REPORT.md`](./tests/FAILURE_TESTS_REPORT.md). `test_llm_agent.py` replaces the LLM with a scripted model that deliberately misbehaves (invents emails, claims false success, skips escalations, leaks PII) and asserts that every layer still holds. Tests and evals use an in-memory database, so `data/urbanthread.db` is never modified.

### 4. Run Repeatable Agent Evaluations (16 Cases)
```bash
python evals/run_evals.py                                      # deterministic engine
python evals/run_evals.py --backend claude_code --live-llm     # LLM decides (Claude Code CLI)
python evals/run_evals.py --backend openai --live-llm          # LLM decides (OpenAI)
```
Deterministic runs write [`evals/results.json`](./evals/results.json) / [`evals/EVAL_REPORT.md`](./evals/EVAL_REPORT.md); LLM-driven runs write [`evals/results_llm_agent.json`](./evals/results_llm_agent.json) / [`evals/EVAL_REPORT_LLM_AGENT.md`](./evals/EVAL_REPORT_LLM_AGENT.md).

| Engine | Result |
|---|---|
| Deterministic | 16/16 |
| LLM agent (Claude via Claude Code CLI) | 16/16 |
