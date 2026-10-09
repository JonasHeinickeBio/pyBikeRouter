# Documentation

Documentation for the bike-routing-agent service.

| Document | Covers |
| --- | --- |
| [architecture.md](architecture.md) | Layers, LangGraph workflow, state contract, terminal statuses, error model, design principles |
| [cli.md](cli.md) | The `bike-router` command: plan (alternatives, sight stops, GPX/GeoJSON), points of interest, BRouter tiles, history, status |
| [api.md](api.md) | HTTP endpoints, request/response schemas, status semantics, artifact downloads |
| [configuration.md](configuration.md) | Environment variables, `.env`, provider profile mapping, startup validation, Docker/compose |
| [providers.md](providers.md) | Provider protocols, the full openrouteservice client, ORS routing adapter, geocoders, BRouter/Valhalla adapters |
| [profile-evaluation.md](profile-evaluation.md) | Do the BRouter profiles deliver what each bike type promises? 40-route evidence, the gravel-v2 change, open findings (commuter, recumbent) |
| [providers-comparison.md](providers-comparison.md) | Real-world ORS vs BRouter measurement, findings, options for running both |
| [geocoding.md](geocoding.md) | Nominatim vs Pelias backends, confidence semantics, clarification policy, caching |
| [enrichment.md](enrichment.md) | OSM surface enrichment, Overpass prototype, unknown-is-unknown data-quality policy |
| [scoring-and-exports.md](scoring-and-exports.md) | Deterministic scoring, score calibration benchmark, uncertainty notes, explanation policy, GeoJSON/GPX export |
| [mobile.md](mobile.md) | iPhone use: installable home-screen app served over Tailscale |
| [llm-parser.md](llm-parser.md) | Plan from plain words: the LLM request parser, its guarantees, failure codes, evaluation benchmark |
| [pois.md](pois.md) | Points of interest: Overpass + Wikidata/Wikipedia, fame ranking, map filter, add to route, routing past the most famous sights |
| [weather.md](weather.md) | Forecast along the route: free keyless providers (Open-Meteo, MET Norway), wind vs. travel direction, advisories, UI |
| [persistence.md](persistence.md) | PostGIS route history, artifact storage backends, provenance queries |
| [self-hosted.md](self-hosted.md) | Local openrouteservice and/or Nominatim from one OSM extract: pick a setup for your machine, measured hardware requirements, small-PC recipes, bootstrap |
| [testing.md](testing.md) | Test layout, mocks vs live tests, CI gates |
| [roadmap.md](roadmap.md) | Deliberate milestone boundaries and planned follow-up work |

## Where to start

- Running the service: root [README](../README.md) (Setup), then
  [configuration.md](configuration.md).
- Understanding a response: [api.md](api.md), then
  [scoring-and-exports.md](scoring-and-exports.md).
- Adding a routing or geocoding backend: [providers.md](providers.md).
- Contributing: [architecture.md](architecture.md) and
  [testing.md](testing.md).
