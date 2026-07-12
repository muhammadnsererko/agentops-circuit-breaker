"""Circuit breaker middleware that interrupts a looping LangGraph run."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable


logging.basicConfig(level=logging.INFO, format="%(message)s")


class CircuitBreakerTripped(RuntimeError):
    """Raised when the circuit breaker detects a violation."""


class CircuitBreaker:
    """Enforce hard limits around every node transition in the agent loop."""

    MAX_STEPS = 10
    LOOP_DETECTION = 3
    MAX_COST_USD = 0.05
    MAX_ERRORS = 3

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset breaker state before a new run."""

        self.steps_taken = 0
        self.node_visits: list[str] = []
        self.consecutive_repeat_count = 0
        self.last_node: str | None = None
        self.estimated_cost_usd = 0.0
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.error_count = 0
        self.trigger_reason: str | None = None
        self.tripped = False

    def observe_transition(
        self,
        event: str,
        node_name: str,
        state: dict[str, Any],
        result: dict[str, Any] | None,
        error: Exception | None,
    ) -> None:
        """Observe a node transition and trip the breaker if any threshold is exceeded."""

        if self.tripped:
            return

        if event == "enter":
            return

        if event == "error":
            self.error_count += 1
            print(f"[breaker] error in node {node_name} | consecutive_errors={self.error_count}")
            if self.error_count > self.MAX_ERRORS:
                self._trip(f"consecutive errors exceeded {self.MAX_ERRORS}")
            return

        if event != "exit":
            return

        self.steps_taken += 1
        self.node_visits.append(node_name)
        print(
            f"[breaker] step={self.steps_taken} node={node_name} "
            f"repeat_count={self.consecutive_repeat_count} cost=${self.estimated_cost_usd:.6f}"
        )

        if self.last_node == node_name:
            self.consecutive_repeat_count += 1
        else:
            self.consecutive_repeat_count = 1
            self.last_node = node_name

        if self.consecutive_repeat_count >= self.LOOP_DETECTION:
            self._trip(f"loop detected: {node_name} repeated {self.consecutive_repeat_count} consecutive times")
            return

        if self.steps_taken > self.MAX_STEPS:
            self._trip(f"max steps exceeded: {self.steps_taken} > {self.MAX_STEPS}")
            return

        input_text = self._stringify_state(state)
        output_text = self._stringify_state(result or {})
        input_tokens = max(1, int(len(input_text.split()) * 1.4))
        output_tokens = max(1, int(len(output_text.split()) * 1.4))
        self.token_usage["input_tokens"] += input_tokens
        self.token_usage["output_tokens"] += output_tokens
        self.token_usage["total_tokens"] += input_tokens + output_tokens

        estimated_cost = (
            input_tokens / 1_000_000 * 0.59
            + output_tokens / 1_000_000 * 0.79
        )
        self.estimated_cost_usd += estimated_cost

        if self.estimated_cost_usd > self.MAX_COST_USD:
            self._trip(f"max cost exceeded: ${self.estimated_cost_usd:.6f} > ${self.MAX_COST_USD:.6f}")

    def wrap(self, agent_factory: Callable[..., Any]) -> Callable[..., Any]:
        """Return a wrapped agent factory that resets the breaker and installs the observer."""

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self.reset()
            kwargs.setdefault("observer", self.observe_transition)
            agent = agent_factory(*args, **kwargs)

            class WrappedAgent:
                """Proxy object that catches breaker trips during agent invocation."""

                def __init__(self, inner_agent: Any, breaker: CircuitBreaker) -> None:
                    self.inner_agent = inner_agent
                    self._breaker = breaker

                def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
                    """Invoke the underlying agent and convert breaker trips into a result payload."""

                    try:
                        return self.inner_agent.invoke(state)
                    except CircuitBreakerTripped as exc:
                        logging.info("%s", exc)
                        return {"status": "tripped", "incident_report": self._breaker._build_incident_report()}

                def __getattr__(self, name: str) -> Any:
                    return getattr(self.inner_agent, name)

            return WrappedAgent(agent, self)

        return wrapped

    def _trip(self, reason: str) -> None:
        """Stop execution immediately and write the incident report."""

        self.tripped = True
        self.trigger_reason = reason
        logging.info("CIRCUIT BREAKER TRIPPED: %s", reason)
        self._write_incident_report()
        raise CircuitBreakerTripped(reason)

    def _write_incident_report(self) -> None:
        """Write a structured JSON incident report to the workspace root."""

        report = self._build_incident_report()
        report_path = os.path.join(os.path.dirname(__file__), "incident_report.json")
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        logging.info("Incident report written to %s", report_path)

    def _build_incident_report(self) -> dict[str, Any]:
        """Create the incident report payload."""

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trigger_reason": self.trigger_reason,
            "steps_taken": self.steps_taken,
            "nodes_visited": self.node_visits,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "token_usage": self.token_usage,
        }

    @staticmethod
    def _stringify_state(state: dict[str, Any] | None) -> str:
        """Convert state content to a readable string for token estimation."""

        if not state:
            return ""
        return " ".join(str(value) for value in state.values() if value is not None)
