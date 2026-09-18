"""Command-line interface for bike-routing-agent.

Entry point (installed as ``bike-router`` via the console script, runnable as
``python -m bike_routing_agent.cli``). The CLI is organised into subcommand
groups -- ``route``, ``serve``, ``docker``, ``config``, ``providers`` -- each
implemented in :mod:`bike_routing_agent.cli.commands`.
"""

from bike_routing_agent.cli.main import main

__all__ = ["main"]
