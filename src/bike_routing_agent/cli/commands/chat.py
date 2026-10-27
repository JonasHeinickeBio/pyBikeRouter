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
from pathlib import Path
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

    evaluate = sub.add_parser(
        "eval",
        help="score how the chat reads messages against benchmarks/chat-v1.json (free, offline)",
    )
    evaluate.add_argument("--benchmark", type=Path, default=None, metavar="FILE")
    evaluate.add_argument(
        "--rules-only",
        action="store_true",
        help="as without a language-model parser: a message no rule claims goes to the dialogue",
    )
    evaluate.add_argument(
        "--intent-model",
        action="store_true",
        help="also ask the configured language model (LLM_* settings) about each message the rules "
        "give up on, as CHAT_INTENT_MODEL does, and show it next to the rules' own score. This "
        "sends those benchmark messages (about 20, made-up examples) to that model",
    )
    evaluate.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        metavar="ACCURACY",
        help="exit 1 below this accuracy (0-1; default: never)",
    )
    evaluate.add_argument("--format", choices=("text", "json"), default="text")

    misses = sub.add_parser(
        "misses", help="what the chat could not read (the opt-in log, CHAT_MISS_LOG_PATH)"
    )
    misses.add_argument(
        "--path", type=Path, default=None, metavar="FILE", help="default: CHAT_MISS_LOG_PATH"
    )
    misses.add_argument("--top", type=int, default=15, metavar="N", help="phrases to list")
    misses.add_argument(
        "--export-cases",
        type=Path,
        default=None,
        metavar="FILE",
        help="write the distinct misses as unlabelled benchmark cases to fill in",
    )
    misses.add_argument("--clear", action="store_true", help="delete the log")
    misses.add_argument("--format", choices=("text", "json"), default="text")
    return chat


def _eval(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent.chat.evaluation import DEFAULT_PATH, load_benchmark, run_benchmark

    path = args.benchmark or DEFAULT_PATH
    try:
        benchmark = load_benchmark(path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_USAGE
    baseline = run_benchmark(benchmark, text_enabled=not args.rules_only)
    report = baseline
    if args.intent_model:
        from bike_routing_agent.api import build_intent_model
        from bike_routing_agent.config import settings

        model = build_intent_model(settings.model_copy(update={"chat_intent_model": True}))
        if model is None:
            print(
                "error: --intent-model needs a language model: set LLM_PARSER_ENABLED, LLM_MODEL "
                "and the provider's settings (docs/llm-parser.md)",
                file=stderr,
            )
            return EXIT_USAGE
        report = run_benchmark(benchmark, text_enabled=not args.rules_only, intent_model=model)
    if args.format == "json":
        data = report.as_dict()
        if report is not baseline:
            data["rules_accuracy"] = round(baseline.accuracy, 4)
        print(json.dumps(data, indent=2, ensure_ascii=False), file=stdout)
    else:
        print(_eval_text(report, rules_only=args.rules_only, baseline=baseline), file=stdout)
    return EXIT_FAILURE if report.accuracy < args.fail_under else EXIT_OK


def _eval_text(report: Any, *, rules_only: bool, baseline: Any = None) -> str:
    mode = "rules only" if rules_only else "with a free-text parser"
    lines = [
        f"Chat benchmark ({mode}): {report.passed}/{len(report.results)} read as expected "
        f"({report.accuracy:.1%})",
    ]
    if baseline is not None and baseline is not report:
        lines[0] = lines[0].replace("Chat benchmark (", "Chat benchmark (with an intent model, ")
        decided = report.decided_by_model
        lines += [
            f"  the same without the intent model: {baseline.passed}/{len(baseline.results)} "
            f"({baseline.accuracy:.1%})",
            f"  the model placed {len(decided)} message(s), {sum(r.passed for r in decided)} "
            "of them as expected",
        ]
    lines += [
        "",
        f"  {'intent':<13}{'cases':>6}{'recall':>8}{'chosen':>8}{'precision':>10}",
    ]
    for intent, row in report.by_intent().items():
        lines.append(
            f"  {intent:<13}{row['cases']:>6}{row['recall']:>8.0%}{row['chosen']:>8}"
            f"{row['precision']:>10.0%}"
        )
    if report.failed:
        lines += ["", "Read differently than expected:"]
        for r in report.failed:
            by = " (model)" if r.by_model else ""
            lines.append(f"  [{r.state}] {r.text!r}: expected {r.expected}, got {r.got}{by}")
    return "\n".join(lines)


def _misses(args: argparse.Namespace, stdout: IO[str], stderr: IO[str]) -> int:
    from bike_routing_agent.chat import misslog
    from bike_routing_agent.config import settings

    path = args.path or settings.chat_miss_log_path
    if path is None:
        print(
            "error: no miss log: set CHAT_MISS_LOG_PATH (it is off by default) or give --path",
            file=stderr,
        )
        return EXIT_USAGE
    if args.clear:
        removed = misslog.MissLog(path).clear()
        print(f"removed {removed} file(s) of the miss log {path}", file=stdout)
        return EXIT_OK
    records = misslog.load(path)
    summary = misslog.summarise(records, top=args.top)
    if args.export_cases is not None:
        cases = misslog.candidate_cases(records)
        args.export_cases.parent.mkdir(parents=True, exist_ok=True)
        args.export_cases.write_text(
            json.dumps({"schema_version": 1, "cases": cases}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {len(cases)} unlabelled case(s) to {args.export_cases}", file=stderr)
    if args.format == "json":
        print(json.dumps(summary, indent=2, ensure_ascii=False), file=stdout)
        return EXIT_OK
    if not summary["total"]:
        print(f"No misses recorded in {path}.", file=stdout)
        return EXIT_OK
    kinds = ", ".join(f"{kind} {n}" for kind, n in summary["by_kind"].items())
    lines = [f"{summary['total']} message(s) the chat could not read ({kinds}).", ""]
    lines += [f"  {item['count']:>3}x  {item['text']}" for item in summary["top"]]
    print("\n".join(lines), file=stdout)
    return EXIT_OK


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
    if args.command == "eval":
        return _eval(args, stdout, stderr)
    if args.command == "misses":
        return _misses(args, stdout, stderr)
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
