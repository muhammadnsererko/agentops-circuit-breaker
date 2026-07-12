"""LangGraph-based looped review agent for the circuit breaker demo."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import START, StateGraph


load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


class AgentState(TypedDict, total=False):
    """State passed through the review workflow."""

    prompt: str
    plan: str
    review: str
    fix: str
    node_history: list[str]
    current_node: str


class DeterministicFallbackLLM:
    """Provide deterministic responses when no Groq API key is available."""

    def invoke(self, messages: list[Any]) -> SimpleNamespace:
        """Return a lightweight response payload compatible with the chat interface."""

        last_message = messages[-1].content if messages else ""
        if "Create a short review plan" in last_message:
            content = "Plan: inspect the function, identify likely failure modes, and describe a safe repair."
        elif "Review the current implementation" in last_message:
            content = "Issue: the implementation lacks defensive checks and may fail on edge cases."
        else:
            content = "Fix: add input validation, handle edge cases, and keep the loop moving."
        return SimpleNamespace(content=content)


def get_llm() -> Any:
    """Create a ChatGroq client when possible, otherwise fall back to a deterministic local stub."""

    api_key = os.getenv("GROQ_API_KEY")
    if api_key:
        return ChatGroq(model="llama-3.3-70b-versatile", api_key=api_key, temperature=0.2)
    return DeterministicFallbackLLM()


def _invoke_model(llm: ChatGroq, prompt: str, node_name: str) -> str:
    """Send a prompt to the LLM and return the content response."""

    system_prompt = (
        f"You are a {node_name} agent in a code review workflow. "
        "Be concise and keep the loop moving."
    )
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ])
    return str(response.content).strip() or f"{node_name} completed."


def planner_node(state: AgentState, llm: ChatGroq) -> dict[str, Any]:
    """Create an initial review plan for the supplied prompt."""

    prompt = (
        f"Create a short review plan for this request: {state.get('prompt', 'N/A')}"
    )
    plan = _invoke_model(llm, prompt, "planner")
    return {
        "plan": plan,
        "review": "",
        "fix": "",
        "current_node": "planner",
        "node_history": [*(state.get("node_history") or []), "planner"],
    }


def reviewer_node(state: AgentState, llm: ChatGroq) -> dict[str, Any]:
    """Always report issues to simulate a stubborn review loop."""

    prompt = (
        "Review the current implementation and report issues that still need attention. "
        f"Plan: {state.get('plan', 'N/A')}"
    )
    review = _invoke_model(llm, prompt, "reviewer")
    return {
        "review": review,
        "current_node": "reviewer",
        "node_history": [*(state.get("node_history") or []), "reviewer"],
    }


def fixer_node(state: AgentState, llm: ChatGroq) -> dict[str, Any]:
    """Try to fix the reported issues and send the workflow back to review."""

    prompt = (
        "Propose a patch or remediation for the reported issues. "
        f"Review: {state.get('review', 'N/A')}"
    )
    fix = _invoke_model(llm, prompt, "fixer")
    return {
        "fix": fix,
        "current_node": "fixer",
        "node_history": [*(state.get("node_history") or []), "fixer"],
    }


def build_agent(observer: Callable[[str, str, dict[str, Any], dict[str, Any] | None, Exception | None], None] | None = None):
    """Build a LangGraph workflow with instrumentation hooks for the breaker."""

    llm = get_llm()

    def wrap_node(node_name: str, node_fn: Callable[[AgentState, ChatGroq], dict[str, Any]]):
        def wrapped(state: AgentState) -> dict[str, Any]:
            if observer is not None:
                observer("enter", node_name, state, None, None)
            try:
                result = node_fn(state, llm)
            except Exception as exc:  # pragma: no cover - defensive fallback
                if observer is not None:
                    observer("error", node_name, state, None, exc)
                raise
            if observer is not None:
                observer("exit", node_name, state, result, None)
            return result

        return wrapped

    builder = StateGraph(AgentState)
    builder.add_node("planner", wrap_node("planner", planner_node))
    builder.add_node("reviewer", wrap_node("reviewer", reviewer_node))
    builder.add_node("fixer", wrap_node("fixer", fixer_node))
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "reviewer")
    builder.add_edge("reviewer", "fixer")
    builder.add_edge("fixer", "reviewer")
    return builder.compile()
