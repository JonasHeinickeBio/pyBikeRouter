"""Central configuration and provider-mapping tables.

Provider-specific strings (e.g. ORS profile names) are kept here so graph
nodes and scoring code stay provider-neutral.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ors_api_key: str = ""
    ors_base_url: str = "https://api.openrouteservice.org"
    ors_timeout_s: float = 10.0
    ors_max_retries: int = 2

    geocoder_base_url: str = "https://nominatim.openstreetmap.org"
    geocoder_timeout_s: float = 5.0
    geocoder_user_agent: str = "bike-routing-agent/0.1"
    geocoder_cache_ttl_s: int = 3600
    geocoder_ambiguity_margin: float = 0.05
    geocoder_min_confidence: float = 0.3

    valhalla_base_url: str = "http://localhost:8002"

    export_dir: str = "exports"
    log_level: str = "INFO"


settings = Settings()

# Internal bike type -> ORS cycling profile. Kept configurable and isolated
# here because engine profiles are an approximation of real-world bike
# categories (gravel riding in particular has no dedicated ORS profile).
ORS_PROFILE_MAP: dict[str, str] = {
    "road": "cycling-road",
    "gravel": "cycling-regular",
    "touring": "cycling-regular",
    "mountain": "cycling-mountain",
    "city": "cycling-regular",
}
