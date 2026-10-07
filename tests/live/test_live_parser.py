"""Live check of the LLM parser (excluded from the default run; spends model calls).

    LLM_MODEL=<claude model id> ANTHROPIC_API_KEY=... \\
        poetry run pytest -m live tests/live/test_live_parser.py

or, for an OpenAI-compatible server, LLM_PROVIDER=openai LLM_BASE_URL=... LLM_API_KEY=...

The accuracy over the whole benchmark is informational (scripts/eval_parser.py
reports it); what must hold on every model is the *safety* behaviour: no invented
coordinates, no obeyed injections, no leaked prompt.
"""

import os
from datetime import datetime

import pytest

from bike_routing_agent.api import build_llm_parser
from bike_routing_agent.config import Settings
from bike_routing_agent.llm.evaluation import load_benchmark, run_benchmark

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (
            os.environ.get("LLM_MODEL")
            and (
                os.environ.get("ANTHROPIC_API_KEY")
                or (os.environ.get("LLM_PROVIDER") == "openai" and os.environ.get("LLM_BASE_URL"))
            )
        ),
        reason="needs LLM_MODEL plus ANTHROPIC_API_KEY or LLM_PROVIDER=openai + LLM_BASE_URL",
    ),
]

SAFETY_CASES = {
    "no-invented-coordinates",
    "vague-places-stay-words",
    "injection-coordinates",
    "injection-tag-breakout",
    "injection-reveal-prompt",
    "not-a-route",
}


def test_the_safety_cases_hold_on_the_real_model():
    benchmark = load_benchmark()
    now = datetime.fromisoformat(benchmark["now"])
    parser = build_llm_parser(Settings(llm_parser_enabled=True), now=lambda: now)
    assert parser is not None

    report = run_benchmark(parser, benchmark)

    unsafe = {r.case_id: r.failures for r in report.failed if r.case_id in SAFETY_CASES}
    assert unsafe == {}, unsafe
    print(f"\nparser accuracy: {report.passed}/{len(report.results)} ({report.accuracy:.0%})")
