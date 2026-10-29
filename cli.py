import argparse

from src.agent import CustomerSupportAgent
from src.constants import BRAND_NAME, DEFAULT_BACKEND, DEMO_SCENARIOS


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
        resp = agent.handle_message(actual_prompt)
        tools_str = ", ".join(f"{t.tool_name}({t.status})" for t in resp.tools_called) or "None (Blocked Pre-Tool)"
        print(f"[Decision: {resp.decision_type} | Tools: {tools_str} | Escalated: {resp.escalated} | Blocked: {resp.blocked}]")
        print(f"Agent:\n{resp.reply}")
        print("-" * 80)


def run_interactive(backend: str, use_live_llm: bool) -> None:
    agent = CustomerSupportAgent(backend=backend, use_live_llm=use_live_llm)
    print("=" * 72)
    print(f"{BRAND_NAME} — Interactive Customer Support Agent (SQLite + Hybrid LLM)")
    print(f"Active Backend: {agent._resolve_active_backend_label()}")
    print("Type 'exit' or 'quit' to quit, 'reset' to re-seed SQLite database.")
    print("=" * 72)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        if user_input.lower() == "reset":
            agent.store.reset()
            print("[SQLite database reset to initial seed state.]")
            continue

        resp = agent.handle_message(user_input)
        tools_str = ", ".join(f"{t.tool_name}({t.status})" for t in resp.tools_called) or "None"
        print(f"\n[Decision: {resp.decision_type} | Tools: {tools_str} | Backend: {resp.backend_used}]")
        print(f"Agent: {resp.reply}")


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
