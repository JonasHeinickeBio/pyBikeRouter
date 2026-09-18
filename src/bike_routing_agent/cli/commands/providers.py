"""``bike-router providers`` group: show configured providers and mappings."""

from __future__ import annotations

import argparse
import json
from typing import IO


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    providers = subparsers.add_parser("providers", help="inspect provider configuration")
    providers_sub = providers.add_subparsers(dest="command", metavar="<command>")
    if providers_sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")

    providers_sub.add_parser("list", help="print provider names and bike-type mappings")
    return providers


def run(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent.config import (
        BROUTER_PROFILE_MAP,
        ORS_PROFILE_MAP,
        PUBLIC_ORS_BASE_URLS,
        VALHALLA_PROFILE_MAP,
        Settings,
    )

    if args.command != "list":
        print("unknown providers command", file=stderr)
        return 2

    cfg = Settings()
    profile_maps = {
        "ors": ORS_PROFILE_MAP,
        "brouter": BROUTER_PROFILE_MAP,
        "valhalla": VALHALLA_PROFILE_MAP,
    }
    base_urls = {
        "ors": cfg.ors_base_url,
        "brouter": cfg.brouter_base_url,
        "valhalla": cfg.valhalla_base_url,
    }
    if cfg.routing_provider == "all":
        bike_type_profiles: object = {
            name: dict(sorted(mapping.items())) for name, mapping in profile_maps.items()
        }
    else:
        bike_type_profiles = dict(sorted(profile_maps[cfg.routing_provider].items()))
    payload = {
        "geocoder": {
            "configured": cfg.geocoder_provider,
            "available": ["nominatim", "pelias"],
            "base_url": (
                cfg.geocoder_base_url
                if cfg.geocoder_provider == "nominatim"
                else cfg.ors_base_url
            ),
            "note": "pelias requires a self-hosted ORS (public API: "
            + ", ".join(sorted(PUBLIC_ORS_BASE_URLS))
            + " does not serve it)",
        },
        "routing": {
            "configured": cfg.routing_provider,
            "available": ["ors", "brouter", "valhalla", "all"],
            "base_url": base_urls.get(cfg.routing_provider),
            "api_key_configured": bool(cfg.ors_api_key)
            if cfg.routing_provider in ("ors", "all")
            else None,
        },
        "bike_type_profiles": bike_type_profiles,
    }
    print(json.dumps(payload, indent=2), file=stdout)
    return 0
