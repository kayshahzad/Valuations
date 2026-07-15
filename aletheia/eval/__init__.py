"""Evaluation & observability layer for Aletheia.

Home-grown eval stack (no third-party eval framework). Currently:

  - ``tracing``  — LangSmith wiring for the LangGraph Stage-4 agents.
    Env-driven and degrades to a no-op when no key is configured, so
    the pipeline runs identically offline / in CI / for users who
    haven't opted in.

Planned (see the eval plan): ``judge`` (Claude LLM-judge), ``rubrics``,
``gate`` (regression gate over the tests/quality_gate snapshot corpus).
"""

from aletheia.eval.tracing import init_langsmith, trace_config, langsmith_enabled

__all__ = ["init_langsmith", "trace_config", "langsmith_enabled"]
