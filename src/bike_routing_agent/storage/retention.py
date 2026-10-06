"""Retention: expire old plans and the artifacts they own (issue #27).

One policy over both stores. A plan older than ``max_age`` is deleted from
the history *first*, then the artifacts only it referenced; a crash in
between leaves unreferenced artifacts (harmless, swept next run), never a
plan pointing at a missing file. An artifact still referenced by a retained
plan is never deleted. Artifacts that no plan references at all (a failed
history write, an interrupted run) are orphans and are swept once they are
older than a grace period, so an export whose plan record is about to be
saved is not caught in the middle.

Without a history (no database) there are no references to honour, so
artifacts are simply expired by age.

Everything is a function of ``(history, store, policy, now)``: a dry run
reports exactly what a real run would delete, using the same code path.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bike_routing_agent.storage.artifacts import ArtifactInfo, ArtifactStore
from bike_routing_agent.storage.history import RetentionCursor, RouteHistory

logger = logging.getLogger(__name__)


class RetentionBusy(RuntimeError):
    """Another prune holds the maintenance lock."""


@dataclass(frozen=True)
class RetentionPolicy:
    max_age: timedelta
    # Unreferenced artifacts younger than this are left alone: the plan record
    # that will reference them may not have been written yet.
    orphan_grace: timedelta = timedelta(hours=24)
    batch_size: int = 500


@dataclass
class PruneReport:
    dry_run: bool
    cutoff: datetime
    plans: int = 0
    plan_artifacts: int = 0
    orphans: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def artifacts(self) -> int:
        return self.plan_artifacts + self.orphans

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cutoff"] = self.cutoff.isoformat()
        data["artifacts"] = self.artifacts
        data["errors"] = list(self.errors)
        return data


def prune(
    history: RouteHistory | None,
    store: ArtifactStore,
    policy: RetentionPolicy,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> PruneReport:
    """Apply ``policy``; ``dry_run`` (the default) deletes nothing."""
    now = now or datetime.now(UTC)
    report = PruneReport(dry_run=dry_run, cutoff=now - policy.max_age)
    if history is None:
        _expire_by_age(store, report, dry_run)
        return report

    with history.maintenance_lock() as acquired:
        if not acquired:
            raise RetentionBusy("another retention run is in progress")
        _prune_with_history(history, store, policy, report, now)
    return report


def _delete_artifacts(
    store: ArtifactStore,
    names: list[str],
    sizes: dict[str, ArtifactInfo],
    report: PruneReport,
    *,
    dry_run: bool,
    orphans: bool,
) -> None:
    for name in names:
        size = sizes[name].size if name in sizes else 0
        if not dry_run:
            try:
                store.delete(name)
            except Exception as exc:  # one bad object must not stop the run
                logger.warning("could not delete artifact %s", name, exc_info=True)
                report.errors.append(f"{name}: {type(exc).__name__}")
                continue
        report.bytes_freed += size
        if orphans:
            report.orphans += 1
        else:
            report.plan_artifacts += 1


def _expire_by_age(store: ArtifactStore, report: PruneReport, dry_run: bool) -> None:
    infos = {i.name: i for i in store.list_artifacts()}
    old = sorted(n for n, i in infos.items() if i.created_at < report.cutoff)
    _delete_artifacts(store, old, infos, report, dry_run=dry_run, orphans=True)


def _prune_with_history(
    history: RouteHistory,
    store: ArtifactStore,
    policy: RetentionPolicy,
    report: PruneReport,
    now: datetime,
) -> None:
    infos = {i.name: i for i in store.list_artifacts()}
    handled: set[str] = set()
    cursor: RetentionCursor | None = None
    while True:
        batch = history.find_older_than(report.cutoff, limit=policy.batch_size, after=cursor)
        if not batch:
            break
        cursor = (batch[-1].created_at, batch[-1].plan_id)
        names = {name for ref in batch for name in ref.artifacts}
        if not report.dry_run:
            report.plans += history.delete_plans([ref.plan_id for ref in batch])
        else:
            report.plans += len(batch)
        # Only plans created after the cutoff survive this prune, so those are
        # the references that protect an artifact (identical in dry-run mode).
        keep = history.referenced_artifacts(names, newer_than=report.cutoff)
        doomed = sorted(names - keep - handled)
        handled.update(doomed)
        _delete_artifacts(store, doomed, infos, report, dry_run=report.dry_run, orphans=False)

    # Orphan sweep: referenced by no plan at all (old plans in a dry run still
    # count as referencing, which is right -- they were handled above).
    referenced = history.referenced_artifacts()
    grace_cutoff = now - policy.orphan_grace
    orphans = sorted(
        name
        for name, info in infos.items()
        if name not in referenced and name not in handled and info.created_at < grace_cutoff
    )
    _delete_artifacts(store, orphans, infos, report, dry_run=report.dry_run, orphans=True)
