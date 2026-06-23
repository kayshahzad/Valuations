"""Regression: the retry path must survive a validation error whose message
embeds the failed JSON completion (full of `{`/`}`), and over-long evidence
quotes must truncate instead of sinking the whole bundle.

Before the fix, a recoverable validation error crashed the retry with
``ValueError: unmatched '{' in format spec`` because the error string (containing
JSON braces) was fed straight into ChatPromptTemplate.from_template (f-string).
"""

from __future__ import annotations

from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from aletheia.qualitative.extractors._llm_invoke import invoke_with_retry


class _Tiny(BaseModel):
    value: int


_PROMPT = "Analyse {ticker} from:\n{source_text}\nReturn JSON."


def _factory(structured):
    class _LLM:
        def with_structured_output(self, schema):
            return structured
    return lambda: _LLM()


def test_retry_survives_brace_filled_validation_error():
    """First attempt raises an error whose text contains the failed JSON
    completion (braces); the retry must build a valid prompt and succeed."""
    calls = {"n": 0}

    def _run(_):
        calls["n"] += 1
        if calls["n"] == 1:
            # message mimics the real failure: embeds JSON + a {type=...} spec
            raise ValueError(
                'Failed to parse from completion {"proposals": [{"x": 1}]}. '
                "Got: 1 validation error ... {type=string_too_long, input_value=...}")
        return _Tiny(value=42)

    bundle, err = invoke_with_retry(
        llm_factory=_factory(RunnableLambda(_run)),
        schema=_Tiny, prompt_template=_PROMPT,
        ticker="JPM", source_text="Item 1: Business ...")

    assert err is None
    assert bundle is not None and bundle.value == 42
    assert calls["n"] == 2          # it actually retried, didn't crash


def test_retry_exhaustion_returns_error_not_crash():
    """Persistent brace-filled failures exhaust retries and return the error —
    never raise the f-string ValueError."""
    def _run(_):
        raise ValueError('bad {json} {type=x} completion {"a": {"b": 1}}')

    bundle, err = invoke_with_retry(
        llm_factory=_factory(RunnableLambda(_run)),
        schema=_Tiny, prompt_template=_PROMPT,
        ticker="JPM", source_text="Item 1 ...")

    assert bundle is None
    assert isinstance(err, Exception)


def test_evidence_quote_truncates_instead_of_rejecting():
    from aletheia.qualitative.extractors.hitl_proposer import EvidenceQuote, _QUOTE_MAX
    long_q = "JPMorgan " + "x" * (_QUOTE_MAX + 50)
    e = EvidenceQuote(question_id="recognition", quote=long_q, source="10-K Item 1")
    assert len(e.quote) <= _QUOTE_MAX
    assert e.quote.endswith("…")
    # an in-bound quote is untouched
    ok = "JPMorgan serves millions of customers under the J.P. Morgan and Chase brands."
    assert EvidenceQuote(question_id="r", quote=ok, source="s").quote == ok
