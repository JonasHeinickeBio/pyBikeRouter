# Documentation

Documentation for the bike-routing-agent service.

| Document | Covers |
| --- | --- |
| [architecture.md](architecture.md) | Layers, LangGraph workflow, state contract, terminal statuses, error model, design principles |
| [api.md](api.md) | HTTP endpoints, request/response schemas, status semantics, artifact downloads |
| [configuration.md](configuration.md) | Environment variables, `.env`, provider profile mapping, startup validation, Docker/compose |
| [providers.md](providers.md) | Provider protocols, the full openrouteservice client, ORS routing adapter, geocoders, BRouter/Valhalla adapters |
| [providers-comparison.md](providers-comparison.md) | Real-world ORS vs BRouter measurement, findings, options for running both |
| [geocoding.md](geocoding.md) | Nominatim vs Pelias backends, confidence semantics, clarification policy, caching |
| [scoring-and-exports.md](scoring-and-exports.md) | Deterministic scoring, uncertainty notes, explanation policy, GeoJSON/GPX export |
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
