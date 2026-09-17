"""BRouter routing adapter stub.

Not implemented in this milestone. See the Valhalla stub in valhalla.py for
the same rationale -- BRouter is the other candidate second engine tracked
as a follow-up issue (custom gravel/touring profiles in particular).
"""

from __future__ import annotations

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.models import RouteCandidate, RoutingRequest


class BRouterAdapter:
    name = "brouter"

    def __init__(self, *, base_url: str, timeout_s: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def route(self, request: RoutingRequest) -> RouteCandidate:
        raise ProviderUnavailableError(
            "BRouter adapter is not implemented yet", provider=self.name
        )

    async def health(self) -> dict:
        return {"status": "not_implemented"}
