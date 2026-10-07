"""`igft scan` and `igft resume`: read a target's follower list page by page."""

from __future__ import annotations

from typing import Annotated

import typer

from igft.cli.errors import EXIT_STOPPED
from igft.cli.runtime import InstagramRun, instagram_command
from igft.config import Settings
from igft.domain import BlockSignal, FetchError
from igft.safety.pacer import Pacer
from igft.scanning.service import ScanProgress, ScanResult, ScanService


def _service(run: InstagramRun) -> ScanService:
    settings: Settings = run.settings
    pacer = Pacer(settings.delay_min_seconds, settings.delay_max_seconds)
    return ScanService(
        run.engine,
        run.fetcher,
        pacer,
        early_stop_pages=settings.early_stop_pages,
        page_cap=settings.page_cap,
        on_progress=lambda progress: typer.echo(_progress_line(progress), err=True),
    )


def _progress_line(progress: ScanProgress) -> str:
    line = (
        f"Page {progress.run_pages} of at most {progress.page_limit} this run, "
        f"{progress.followers_saved:,} followers saved"
    )
    if progress.first_seen is not None:
        line += f" ({progress.first_seen:,} new)"
    return f"{line}, next request in {round(progress.next_request_in)} s."


def _run_scan(ctx: typer.Context, command: str, action) -> None:
    with instagram_command(ctx, command) as run:
        service = _service(run)
        try:
            result = action(service)
        except (BlockSignal, FetchError):
            _report_kept(service)
            raise
        except KeyboardInterrupt:
            typer.echo("\nInterrupted.", err=True)
            _report_kept(service)
            raise typer.Exit(EXIT_STOPPED) from None
    _report(result, command)


def _report_kept(service: ScanService) -> None:
    """Say what a stopped scan kept, once a scan record exists."""
    if service.scan_id is None:
        return
    scan = service.snapshot()
    typer.echo(
        f"Scan {scan.id} of @{service.username} is unfinished and kept: {scan.pages_fetched} pages, "
        f"{scan.followers_seen} followers saved. Continue it with `igft resume {service.username}`.",
        err=True,
    )


def _report(result: ScanResult, command: str) -> None:
    scan = result.scan
    name = f"@{result.username}"
    if result.complete:
        reason = (scan.stop_reason or "").replace("_", " ")
        typer.echo(f"Scan {scan.id} of {name} is complete ({reason}).")
        typer.echo(_counts(result))
        if scan.is_baseline:
            typer.echo(
                "This was the baseline scan: its followers are recorded but never reported by `igft first-seen`."
            )
        return
    if scan.stop_reason == "page_cap":
        typer.echo(f"Scan {scan.id} of {name} stopped at the page cap of {result.pages_this_run} pages for this run.")
        typer.echo(_counts(result))
        typer.echo(
            f"Continue it with `igft resume {result.username}`, or start from page 1 with "
            f"`igft resume {result.username} --restart`."
        )
    else:
        typer.echo(result.message or f"Scan {scan.id} of {name} stopped: {scan.stop_reason}.", err=True)
    raise typer.Exit(EXIT_STOPPED)


def _counts(result: ScanResult) -> str:
    scan = result.scan
    line = f"Pages fetched: {scan.pages_fetched}"
    if scan.runs > 1:
        line += f" ({result.pages_this_run} in this run)"
    line += f". Followers seen: {scan.followers_seen}."
    if not scan.is_baseline:
        line += f" Seen for the first time: {scan.first_seen_followers}."
    return line


def scan(
    ctx: typer.Context,
    username: Annotated[str, typer.Argument(help="A target added with `igft target add`.")],
    full: Annotated[
        bool, typer.Option("--full", help="Read the whole follower list instead of stopping early.")
    ] = False,
) -> None:
    """Scan one target's follower list. The first scan of a target is its baseline and reads everything."""
    _run_scan(ctx, "scan", lambda service: service.start(username, full=full))


def resume(
    ctx: typer.Context,
    username: Annotated[str, typer.Argument(help="A target with an unfinished scan.")],
    restart: Annotated[
        bool,
        typer.Option("--restart", help="Continue from page 1 instead of the saved cursor. Saved followers are kept."),
    ] = False,
) -> None:
    """Continue the target's unfinished scan from its saved cursor."""
    _run_scan(ctx, "resume", lambda service: service.resume(username, restart=restart))
