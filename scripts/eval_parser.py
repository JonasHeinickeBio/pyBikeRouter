"""Measure the free-text parser against benchmarks/parser-v1.json (issue #30).

Spends real model calls (about one per case, two when a reply needs repair), so
it is a manual step, never part of CI:

    LLM_MODEL=<claude model id> ANTHROPIC_API_KEY=... \\
        poetry run python scripts/eval_parser.py [--out parser-report.json] [--fail-under 0.9]

or against an OpenAI-compatible server (Blablador, vLLM, Ollama):

    LLM_PROVIDER=openai LLM_BASE_URL=https://.../v1 LLM_API_KEY=... LLM_MODEL=<model> \\
        poetry run python scripts/eval_parser.py

It prints one line per case (pass/fail and why) and the overall accuracy, so a
prompt or model change can be judged by numbers rather than by feel. The clock
the parser sees is the benchmark's fixed ``now``, so time phrases have one right
answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bike_routing_agent.api import build_llm_parser
from bike_routing_agent.config import Settings
from bike_routing_agent.llm.evaluation import DEFAULT_PATH, load_benchmark, run_benchmark


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    args.add_argument("--benchmark", type=Path, default=DEFAULT_PATH)
    args.add_argument("--out", type=Path, help="write the full report as JSON")
    args.add_argument(
        "--fail-under", type=float, default=0.0, help="exit 1 below this accuracy (default: never)"
    )
    args.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help="seconds to wait between cases, for servers that rate-limit (HTTP 429)",
    )
    options = args.parse_args()

    cfg = Settings()
    if not cfg.llm_model:
        print("set LLM_MODEL to the model id to evaluate", file=sys.stderr)
        return 2
    benchmark = load_benchmark(options.benchmark)
    now = datetime.fromisoformat(benchmark["now"])
    # The same construction the service uses, with the benchmark's fixed clock.
    parser = build_llm_parser(cfg.model_copy(update={"llm_parser_enabled": True}), now=lambda: now)
    assert parser is not None
    if options.pause > 0:
        inner = parser

        def paced(text: str, **kwargs: Any) -> dict[str, Any]:
            time.sleep(options.pause)
            return inner(text, **kwargs)

        parser = paced  # type: ignore[assignment]

    report = run_benchmark(parser, benchmark)
    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"{mark}  {result.case_id}")
        for failure in result.failures:
            print(f"        - {failure}")
    print(
        f"\n{report.passed}/{len(report.results)} cases passed "
        f"({report.accuracy:.0%}) with {cfg.llm_model}"
    )

    if options.out:
        options.out.write_text(
            json.dumps(
                {
                    "model": cfg.llm_model,
                    "accuracy": report.accuracy,
                    "results": [r.__dict__ for r in report.results],
                },
                indent=2,
            )
        )
    return 0 if report.accuracy >= options.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
