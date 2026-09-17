"""Valhalla routing adapter stub.

Not implemented in this milestone -- tracked as a follow-up issue to add
Valhalla as a second routing engine alongside ORS. This stub exists so the
RoutingProvider protocol has a second implementer to test against and so the
graph's provider selection can be exercised without a live Valhalla service.
"""

from __future__ import annotations

import httpx

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.models import RouteCandidate, RoutingRequest


class ValhallaAdapter:
    name = "valhalla"

    def __init__(self, *, base_url: str, timeout_s: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def route(self, request: RoutingRequest) -> RouteCandidate:
        raise ProviderUnavailableError(
            "Valhalla adapter is not implemented yet", provider=self.name
        )

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(f"{self._base_url}/status")
            return {"status": "ok" if response.status_code == 200 else "degraded"}
        except httpx.HTTPError as exc:
            return {"status": "unavailable", "error": str(exc)}
