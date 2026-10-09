"""``bike-router chat``: plan routes by talking to the bot, in the terminal.

The same conversation as ``POST /v1/chat`` and the web chat (docs/chat.md), run in-process: a
one-line request, free text (with the LLM parser), a step-by-step dialogue, then changes,
alternatives, questions, sights and files. Interactive by default; ``--message`` scripts the
turns of one conversation (repeatable) and prints each reply.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from typing import IO, Any

from bike_routing_agent.chat.models import ChatReply
from bike_routing_agent.chat.service import ChatService

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
QUIT_WORDS = {"quit", "exit", ":q", "bye"}
PROMPT = "you> "


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    chat = subparsers.add_parser("chat", help="plan routes by chatting (interactive)")
    sub = chat.add_subparsers(dest="command", metavar="<command>")
    if sub is None:  # pragma: no cover - argparse types only
        raise RuntimeError("subparsers are required")
    start = sub.add_parser("start", help="start a conversation")
    start.add_argument(
        "--message",
        "-m",
        action="append",
        default=[],
        metavar="TEXT",
        help="say this (repeatable: the turns of one conversation); without it the chat is "
        "interactive",
    )
    start.add_argument("--timezone", default=None, metavar="IANA", help="e.g. Europe/Berlin")
    start.add_argument("--format", choices=("text", "json"), default="text")
    return chat


def _default_service() -> ChatService:
    from bike_routing_agent import api

    if api._chat_service is None:
        raise RuntimeError("the chat is switched off (CHAT_ENABLED=false)")
    return api._chat_service


def _show_files(text: str) -> str:
    """Artifact URLs are paths on the API; in a terminal the files are in the export folder."""
    if "/v1/routes/" not in text:
        return text
    from bike_routing_agent.config import settings

    return text.replace("/v1/routes/", f"{settings.export_dir.rstrip('/')}/")


def _render(reply: ChatReply, numbered: list[str]) -> str:
    lines = [f"bot> {_show_files(reply.reply)}".replace("\n", "\n     ")]
    if reply.suggestions:
        digits = all(s.isdigit() for s in reply.suggestions)
        numbered[:] = [] if digits else list(reply.suggestions)
        shown = (
            " | ".join(reply.suggestions)
            if digits
            else "  ".join(f"[{i}] {s}" for i, s in enumerate(reply.suggestions, 1))
        )
        lines.append(f"     {shown}")
    else:
        numbered[:] = []
    return "\n".join(lines)


def _json_line(reply: ChatReply) -> str:
    data: dict[str, Any] = reply.model_dump(mode="json")
    if data.get("plan"):  # the route itself is large; the summary is in `reply`
        plan = data["plan"]
        data["plan"] = {
            "status": plan.get("status"),
            "candidates": len(plan.get("candidates") or []),
            "artifacts": plan.get("artifacts"),
        }
    return json.dumps(data, ensure_ascii=False)


def run(
    args: argparse.Namespace,
    stdout: IO[str],
    stderr: IO[str],
    *,
    service_factory: Callable[[], ChatService] | None = None,
    reader: Callable[[str], str] = input,
) -> int:
    if args.command != "start":
        print("unknown chat command", file=stderr)
        return EXIT_USAGE
    try:
        service = (service_factory or _default_service)()
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=stderr)
        return EXIT_FAILURE

    state: dict[str, Any] = {"sid": None, "numbered": []}

    # One event loop for the whole conversation: the providers keep per-loop state (BRouter's
    # request queue), which must not be carried from one loop to the next.
    loop = asyncio.new_event_loop()

    def turn(text: str) -> ChatReply:
        message = text.strip()
        if message.isdigit() and 1 <= int(message) <= len(state["numbered"]):
            message = state["numbered"][int(message) - 1]
        reply = loop.run_until_complete(
            service.send(message, session_id=state["sid"], timezone=args.timezone)
        )
        state["sid"] = reply.session_id
        return reply

    def show(reply: ChatReply) -> None:
        print(
            _json_line(reply) if args.format == "json" else _render(reply, state["numbered"]),
            file=stdout,
        )

    def converse() -> int:
        if args.message:
            failed = False
            for text in args.message:
                if args.format == "text":
                    print(f"{PROMPT}{text}", file=stdout)
                reply = turn(text)
                show(reply)
                failed = failed or reply.failed
            return EXIT_FAILURE if failed else EXIT_OK  # a scripted run can tell
        if args.format == "text":
            print("Plan a bike route by chatting. Type 'help', or 'quit' to leave.", file=stdout)
            print(f"bot> {_show_files(turn('help').reply)}".replace("\n", "\n     "), file=stdout)
        # In JSON mode stdout holds JSON lines only: no prompt text in front of them.
        prompt = PROMPT if args.format == "text" else ""
        while True:
            try:
                text = reader(prompt)
            except (EOFError, KeyboardInterrupt):
                if args.format == "text":  # the shell prompt starts on a new line; JSON stays JSON
                    print("", file=stdout)
                return EXIT_OK
            if not text.strip():
                continue
            if text.strip().lower() in QUIT_WORDS:
                return EXIT_OK
            show(turn(text))

    try:
        return converse()
    finally:
        loop.close()
