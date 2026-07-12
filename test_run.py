"""Run the looped agent with the circuit breaker and print the result."""

from __future__ import annotations

from agent import build_agent
from circuit_breaker import CircuitBreaker


def run_demo() -> None:
    """Build the agent, wrap it with the breaker, and execute a sample prompt."""

    breaker = CircuitBreaker()
    wrapped_agent_factory = breaker.wrap(build_agent)

    print("Starting agent run...")
    print("Prompt: Review this Python function for bugs")

    agent = wrapped_agent_factory()
    result = agent.invoke({
        "prompt": "Review this Python function for bugs",
        "plan": "",
        "review": "",
        "fix": "",
        "node_history": [],
        "current_node": "planner",
    })

    print("Final status:", result.get("status", "completed"))
    if result.get("incident_report"):
        print("Incident report:")
        import json
        print(json.dumps(result["incident_report"], indent=2))
    else:
        print("No incident report generated.")


if __name__ == "__main__":
    run_demo()
