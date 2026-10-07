# Plan from plain words (LLM request parser)

Issue #30. Describe a ride in a sentence -- "50 km gravel loop from
Braunschweig, clockwise, tomorrow at 8" -- and the service turns it into the
same structured request the form and JSON API already use. Off by default.

## What the model does and does not do

The language model reads the **words** and returns a structured request. It
never produces coordinates, geometry, distances, elevations or scores:

- A place is a plain string ("Wolfenbüttel"). The normal geocoder resolves it,
  so ambiguity still ends in a clarification question instead of a guess.
- Coordinates are accepted only when the person typed them verbatim in the
  text; a coordinate the model "knew" is rejected and retried once.
- Anything the text does not state stays unset, so the usual defaults apply.
  Nothing is invented to fill a gap; assumptions made (for example what
  "tomorrow" means) are returned as `notes`.
- No safety judgements: "safe", "quiet" or "scenic" in the text are not turned
  into claims. Only the constraints the API supports are extracted.

The user's text is wrapped in `<user_request>` tags and treated as data. The
model is told to ignore instructions inside it, tag-closing text is stripped,
the output is closed to a JSON schema, and the result is re-validated with
pydantic. A second validation at the graph boundary
(`nodes/parse.py::_llm_output_problem`) holds *any* injected parser to the same
rule that places are strings.

## Enabling it

```bash
poetry install --extras llm          # the Anthropic SDK is optional
export LLM_PARSER_ENABLED=true
export LLM_MODEL=<an Anthropic model id>   # no default, deliberately
export ANTHROPIC_API_KEY=...               # environment only
```

### Other models (OpenAI-compatible servers)

Any server that speaks `POST {LLM_BASE_URL}/chat/completions` works --
Helmholtz Blablador, vLLM, Ollama, and similar -- with no extra install (it
uses `httpx`, already a dependency):

```bash
export LLM_PARSER_ENABLED=true
export LLM_PROVIDER=openai
export LLM_BASE_URL=https://<server>/v1     # the /v1 root; for Blablador see its docs
export LLM_API_KEY=...                      # optional for local servers
export LLM_MODEL=<a model id the server lists>
```

These servers do not all enforce a JSON schema, so the schema is placed in the
system prompt and the reply is validated like any other (code fences,
`<think>` blocks and prose around the JSON are tolerated). Safety does not rest
on the model: the same checks apply (no invented coordinates, places must be
strings, one repair retry). Smaller open models will follow the format less
reliably, so run the benchmark before relying on one. Rate limits (HTTP 429) and
5xx answers are retried twice with a short backoff (a numeric `Retry-After`
is honoured, capped at 10 s); timeouts and 4xx are not retried. Shared servers
can be slow: raise `LLM_TIMEOUT_S` (60-120) if you see `llm_parser_timeout`.

`LLM_MODEL` has no default so that a model is always a conscious, current
choice. With the flag off, `POST /v1/route/plan-text` answers `503` and the
web form hides the description box. See
[configuration.md](configuration.md#environment-variables) for
`LLM_TIMEOUT_S` and `LLM_MAX_OUTPUT_TOKENS`.

The API key is read only from the environment (a `SecretStr`): it is not in
`config show`, logs or errors.

## Using it

- **Web:** the "Describe your ride" box appears when
  `GET /v1/capabilities` reports `text_planning`. The interpretation is written
  into the form fields, so you can check, correct and re-plan with *Plan route*.
- **API:** `POST /v1/route/plan-text` ([api.md](api.md#post-v1routeplan-text)).
- **CLI:** `bike-router route plan --text "..." [--timezone Europe/Berlin]`.

Every response carries `interpretation`: what the text was read as, the
assumed departure time, the notes, and provenance (model, prompt version,
whether a repair retry was needed, token usage). The same is stored with the
plan in the route history.

## Failure codes

A failed parse is a normal `invalid` plan with one of these codes; the routing
pipeline does not run.

| Code | Meaning |
| --- | --- |
| `nl_parsing_unavailable` | no parser is configured (graph-level) |
| `llm_parser_empty_input` / `llm_parser_input_too_long` | text empty or over 500 characters |
| `llm_parser_no_route_request` | the text does not describe a ride |
| `llm_parser_timeout` / `llm_parser_error` | the model API timed out / could not be reached |
| `llm_parser_declined` | the model declined the request |
| `llm_parser_invalid_output` | the answer was unusable even after one repair retry |

## Measuring it

`benchmarks/parser-v1.json` holds 18 labelled requests (German and English,
loops, via points, surfaces, relative times, typed coordinates, injection
attempts, a non-request). The offline tests check the harness itself with
oracle, inventive and obedient fake parsers. To score a real model:

```bash
poetry run python scripts/eval_parser.py --out report.json --fail-under 0.9
poetry run python scripts/eval_parser.py --pause 5   # for servers that rate-limit
```

Measured so far (one model, `alias-large` on Helmholtz Blablador, prompt
version 2, paced with `--pause 5`): 19/19 on two consecutive runs. Without
pacing the same server answered 429 to a burst of requests and scored 68-74%,
and on prompt version 1 the model once obeyed the "bike_type must be mountain"
injection and once invented `avoid_high_traffic_roads` for "quiet". Results vary
run to run on open models, so treat one pass as a spot check, not a guarantee.

`tests/live/test_live_parser.py` asserts only the safety cases (no invented
coordinates, injections ignored) and skips without `LLM_MODEL` and
`ANTHROPIC_API_KEY`.

> **Status of the real model calls.** Everything above the transport is covered
> by offline tests with fake clients/servers. The Anthropic request (structured
> output via `output_config.format`) was written from the SDK documentation and
> has not been run against the live API. The OpenAI-compatible path has not
> been run in CI, but it has been run end to end against Blablador (see above).
> Run the eval script once with your key before relying on either.
