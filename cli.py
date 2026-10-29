import argparse

from src.agent import CustomerSupportAgent
from src.constants import BRAND_NAME, DEFAULT_BACKEND, DEMO_SCENARIOS


def _print_reference_guide(agent: CustomerSupportAgent) -> None:
    print("\n--- Fictional SQLite Records Available for Live Testing ---")
    for o in agent.store.orders:
        item_names = ", ".join(f"{i['name']} ({i['size']})" for i in o["items"])
        req_note = f" | req_key={o['request_key']} ({o['active_return_id']})" if o.get("request_key") else ""
        print(
            f"  * {o['order_id']} | owner={o['customer_email']} ({o['loyalty_tier']}) | "
            f"status={o['status']} | ${o['total_amount']:.2f} | {item_names}{req_note}"
        )
    print("\n--- Interactive Chat Commands ---")
    print("  /orders    : Show live SQLite orders table")
    print("  /products  : Show UrbanThread product catalog")
    print("  /policies  : Show store policies (7-day return rule, Final Sale, VIP Gold)")
    print("  /fail on   : Simulate SQLite 503 database outage (Failure #5)")
    print("  /fail off  : Restore normal SQLite database operation")
    print("  /reset     : Reset SQLite database and multi-turn session memory")
    print("  exit       : Quit interactive chat")
    print("-" * 76)


def run_demo(backend: str, use_live_llm: bool) -> None:
    agent = CustomerSupportAgent(backend=backend, use_live_llm=use_live_llm)
    print("=" * 80)
    print(f"{BRAND_NAME} Customer Support Agent — Live Scenario Walkthrough (Backend: {agent._resolve_active_backend_label()})")
    print("=" * 80)

    for title, prompt in DEMO_SCENARIOS:
        agent.store.simulate_db_failure = False
        actual_prompt = prompt
        if prompt.startswith("__SIMULATE_DB_FAILURE__"):
            agent.store.simulate_db_failure = True
            actual_prompt = prompt.replace("__SIMULATE_DB_FAILURE__", "")

        print(f"\n>>> SCENARIO: {title}")
        print(f"Customer: {actual_prompt}")
        resp = agent.handle_message(actual_prompt, use_session_memory=False)
        tools_str = ", ".join(f"{t.tool_name}({t.status})" for t in resp.tools_called) or "None (Blocked Pre-Tool)"
        print(f"[Decision: {resp.decision_type} | Tools: {tools_str} | Escalated: {resp.escalated} | Blocked: {resp.blocked}]")
        print(f"Agent:\n{resp.reply}")
        print("-" * 80)


def run_interactive(backend: str, use_live_llm: bool) -> None:
    agent = CustomerSupportAgent(backend=backend, use_live_llm=use_live_llm)
    print("=" * 76)
    print(f"{BRAND_NAME} — Live Multi-Turn Customer Support Chat (SQLite + Hybrid LLM)")
    print(f"Active Backend: {agent._resolve_active_backend_label()}")
    print("=" * 76)
    _print_reference_guide(agent)

    while True:
        try:
            user_input = input("\nCustomer: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting chat.")
            break

        if not user_input:
            continue
        cmd = user_input.lower()
        if cmd in ("exit", "quit", "/exit", "/quit"):
            break
        if cmd in ("reset", "/reset"):
            agent.store.reset()
            agent.reset_session()
            print("[SQLite database and multi-turn session memory reset.]")
            continue
        if cmd == "/orders":
            _print_reference_guide(agent)
            continue
        if cmd == "/products":
            for p in agent.store.products:
                sale_flag = " [FINAL SALE]" if p["final_sale"] else " [7-Day Returnable]"
                print(f"  * {p['product_id']}: {p['name']} (${p['price']:.2f}){sale_flag} | Sizes: {', '.join(p['sizes_in_stock'])}")
            continue
        if cmd == "/policies":
            pols, _ = agent.store.query_policies("all")
            for pol in pols:
                print(f"  * {pol['policy_id']} ({pol['title']}): {pol['rule']}")
            continue
        if cmd == "/fail on":
            agent.store.simulate_db_failure = True
            print("[Simulated SQLite 503 Tool Failure: ENABLED]")
            continue
        if cmd == "/fail off":
            agent.store.simulate_db_failure = False
            print("[Simulated SQLite 503 Tool Failure: DISABLED]")
            continue

        resp = agent.handle_message(user_input, use_session_memory=True)
        tools_str = ", ".join(f"{t.tool_name}({t.status})" for t in resp.tools_called) or "None"
        print(
            f"\n[Decision: {resp.decision_type} | Tools: {tools_str} | "
            f"Escalated: {resp.escalated} | Blocked: {resp.blocked} | Backend: {resp.backend_used}]"
        )
        print(f"Agent:\n{resp.reply}")


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{BRAND_NAME} Customer Support Agent CLI")
    parser.add_argument(
        "--backend",
        choices=["hybrid", "openai", "claude_code"],
        default=DEFAULT_BACKEND,
        help="LLM backend to use (hybrid, openai, or claude_code)",
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Use live OpenAI API or Claude Code Pro CLI to synthesize responses",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the 8-scenario automated demo walkthrough and exit",
    )
    args = parser.parse_args()

    if args.demo:
        run_demo(backend=args.backend, use_live_llm=args.live_llm)
    else:
        run_interactive(backend=args.backend, use_live_llm=args.live_llm)


if __name__ == "__main__":
    main()
