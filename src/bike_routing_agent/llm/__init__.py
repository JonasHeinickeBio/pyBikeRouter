"""LLM-backed free-text request parsing (issue #30).

The language model only turns words into the *same structured request the API
already accepts*. It never produces coordinates, geometry or metrics: place
names stay strings for the geocoder, which turns ambiguity into a clarification
instead of a guess.
"""
