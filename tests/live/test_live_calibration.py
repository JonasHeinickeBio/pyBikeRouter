"""Live calibration: real engines vs the curated judgements (issue #4).

Excluded from the default test run (`pytest -m live` to run). Each benchmark
case is routed through every reachable engine and judged by the same
harness as scripts/calibrate.py. The curated envelopes are deliberately
wide plausibility bounds, so a top-ranked candidate failing one is a real
signal: either an engine regressed geographically, or the judgement needs
re-examination with fresh evidence.
"""

import pytest

from bike_routing_agent.api import build_routing_providers
from bike_routing_agent.calibration import (
    build_request,
    evaluate_candidate,
    load_benchmark,
    ranked_candidates,
)
from bike_routing_agent.config import settings
from bike_routing_agent.errors import ProviderError
from bike_routing_agent.scoring.basic import score_candidate

pytestmark = pytest.mark.live

_has_ors_key = bool(settings.ors_api_key) and settings.ors_api_key != "changeme"

benchmark = load_benchmark()


@pytest.fixture(scope="module")
def providers() -> list:
    return build_routing_providers(
        settings.model_copy(update={"routing_provider": "ors"})
    )


@pytest.mark.skipif(not _has_ors_key, reason="ORS_API_KEY not set")
@pytest.mark.parametrize("case", benchmark.cases, ids=lambda c: c.case_id)
async def test_live_top_ranked_candidate_matches_judgement(case, providers):
    request = build_request(case)
    candidates = []
    for provider in providers:
        try:
            candidates.append(await provider.route(request))
        except ProviderError:
            continue
    assert candidates, f"no engine routed case {case.case_id}"

    scored = [
        c.model_copy(update={"score": score_candidate(c, case.request.constraints)[0]})
        for c in candidates
    ]
    top = ranked_candidates(scored, case.request.constraints)[0]
    evaluation = evaluate_candidate(case, top)
    assert not evaluation.failed, (
        f"{case.case_id}: top candidate {top.provider} contradicts the judged "
        f"envelope: {[(c.name, c.detail) for c in evaluation.failed]}"
    )
