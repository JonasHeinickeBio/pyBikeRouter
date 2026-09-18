"""Central configuration and provider-mapping tables.

Provider-specific strings (e.g. ORS profile names) are kept here so graph
nodes and scoring code stay provider-neutral.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The public openrouteservice does not serve the Pelias geocoding endpoints
# (they 404, same as /v2/health), so Pelias-backed geocoding is only usable
# against a self-hosted ORS instance.
PUBLIC_ORS_BASE_URLS = frozenset({"https://api.openrouteservice.org"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ors_api_key: str = ""
    ors_base_url: str = "https://api.openrouteservice.org"
    ors_timeout_s: float = 10.0
    ors_max_retries: int = 2

    # "nominatim" (default): OSM Nominatim search API, works with the public
    # ORS. "pelias": Pelias search served by a self-hosted ORS instance;
    # rejected at startup when ors_base_url points at the public API.
    geocoder_provider: Literal["nominatim", "pelias"] = "nominatim"
    geocoder_base_url: str = "https://nominatim.openstreetmap.org"
    geocoder_timeout_s: float = 5.0
    geocoder_user_agent: str = "bike-routing-agent/0.1"
    geocoder_cache_ttl_s: int = 3600
    geocoder_ambiguity_margin: float = 0.05
    geocoder_min_confidence: float = 0.3

    valhalla_base_url: str = "http://localhost:8002"
    valhalla_timeout_s: float = 30.0
    valhalla_max_retries: int = 1

    # Routing engine selection. "brouter" routes against a local/self-hosted
    # BRouter RouteServer (see docker/compose.yaml, profile "brouter") and
    # "valhalla" against a self-hosted Valhalla meili server; "all" queries
    # ors + brouter + valhalla in parallel and lets scoring pick the best
    # candidate. No automatic fallback between engines is attempted.
    routing_provider: Literal["ors", "brouter", "valhalla", "all"] = "ors"
    brouter_base_url: str = "http://127.0.0.1:17777"
    brouter_timeout_s: float = 30.0
    brouter_max_retries: int = 1

    export_dir: str = "exports"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_pelias_requires_self_hosted_ors(self) -> Settings:
        if (
            self.geocoder_provider == "pelias"
            and self.ors_base_url.rstrip("/") in PUBLIC_ORS_BASE_URLS
        ):
            raise ValueError(
                "geocoder_provider='pelias' requires a self-hosted openrouteservice "
                "backend: the public api.openrouteservice.org does not expose the "
                "Pelias geocoding endpoints (they return 404). Use "
                "geocoder_provider='nominatim' (the default) for the public API."
            )
        return self

    @model_validator(mode="after")
    def _check_brouter_settings_when_active(self) -> Settings:
        if self.routing_provider == "brouter":
            if self.brouter_timeout_s <= 0:
                raise ValueError(
                    "brouter_timeout_s must be > 0 when routing_provider='brouter' "
                    f"(got {self.brouter_timeout_s})"
                )
            if self.brouter_max_retries < 0:
                raise ValueError(
                    "brouter_max_retries must be >= 0 when routing_provider='brouter' "
                    f"(got {self.brouter_max_retries})"
                )
        return self

    @model_validator(mode="after")
    def _check_valhalla_settings_when_active(self) -> Settings:
        if self.routing_provider in ("valhalla", "all"):
            if self.valhalla_timeout_s <= 0:
                raise ValueError(
                    "valhalla_timeout_s must be > 0 when routing_provider='valhalla' "
                    f"(got {self.valhalla_timeout_s})"
                )
            if self.valhalla_max_retries < 0:
                raise ValueError(
                    "valhalla_max_retries must be >= 0 when routing_provider='valhalla' "
                    f"(got {self.valhalla_max_retries})"
                )
        return self


settings = Settings()

# Internal bike type -> ORS cycling profile. Kept configurable and isolated
# here because engine profiles are an approximation of real-world bike
# categories (gravel riding in particular has no dedicated ORS profile).
# ORS 9.x ships exactly four cycling profiles (regular/mountain/road/electric;
# the former cycling-recreational was removed), so several bike types share a
# profile here. ebike is the only new type with dedicated ORS support;
# commuter and recumbent are approximated with cycling-regular on ORS and get
# their distinct behaviour from the stock BRouter profiles below.
ORS_PROFILE_MAP: dict[str, str] = {
    "road": "cycling-road",
    "gravel": "cycling-regular",
    "touring": "cycling-regular",
    "mountain": "cycling-mountain",
    "city": "cycling-regular",
    "ebike": "cycling-electric",
    "commuter": "cycling-regular",
    "recumbent": "cycling-regular",
}

# Internal bike type -> BRouter profile name (the profile= request parameter).
# A "custom_" prefix is BRouter's convention: the server resolves
# "custom_<name>" to the file <name>.brf under its CUSTOMPROFILESPATH mount,
# which we point at docker/brouter/profiles/ so the gravel and touring
# profiles are versioned in this repository (see docs/providers.md).
# Non-prefixed entries are stock profiles shipped with the BRouter image.
BROUTER_PROFILE_MAP: dict[str, str] = {
    "road": "fastbike",
    "gravel": "custom_gravel-v1",
    "touring": "custom_touring-v1",
    "mountain": "mtb",
    "city": "trekking",
    # Stock profiles shipped with the BRouter image (misc/profiles2 in the
    # upstream repo). BRouter's cost model has no e-assist term, so ebike
    # rides use the fastbike profile (faster target speed, cycle-infrastructure
    # preference). fastbike-verylowtraffic is the community commuter profile;
    # vm-forum-liegerad-schnell is the recumbent (Liegerad) forum profile.
    "ebike": "fastbike",
    "commuter": "fastbike-verylowtraffic",
    "recumbent": "vm-forum-liegerad-schnell",
}

# Internal bike type -> Valhalla costing model. Current Valhalla bicycle
# costing has no per-bike-type option (unlike ORS/BRouter profiles), so all
# types map to the single "bicycle" costing; bike-type differentiation comes
# from the other engines and the scoring step. Kept as a map so a future
# Valhalla costing (e.g. e-assist) can be wired in here only.
VALHALLA_PROFILE_MAP: dict[str, str] = {
    "road": "bicycle",
    "gravel": "bicycle",
    "touring": "bicycle",
    "mountain": "bicycle",
    "city": "bicycle",
    "ebike": "bicycle",
    "commuter": "bicycle",
    "recumbent": "bicycle",
}
