"""The `gaia` command line: run the pipeline, inspect what it produced."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .config import PILOT_AOI, AreaOfInterest, default_history_range, ensure_dirs, settings
from .grid import grid_for
from .store import lake

app = typer.Typer(help="Gaia ecological intelligence layer — pipeline.", no_args_is_help=True)
ingest_app = typer.Typer(help="Ingest data for an area of interest.", no_args_is_help=True)
aoi_app = typer.Typer(help="Manage areas of interest.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")
app.add_typer(aoi_app, name="aoi")

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _resolve_aoi(aoi_id: str | None) -> AreaOfInterest:
    if aoi_id is None or aoi_id == PILOT_AOI.aoi_id:
        return PILOT_AOI
    conn = lake.connect()
    try:
        found = lake.load_aoi(conn, aoi_id)
    finally:
        conn.close()
    if found is None:
        raise typer.BadParameter(
            f"no area of interest with id {aoi_id!r}. Register one with `gaia aoi add`."
        )
    return found


def _parse_date(value: str | None, fallback: date) -> date:
    if value is None:
        return fallback
    return datetime.strptime(value, "%Y-%m-%d").date()


# ------------------------------------------------------------------------- ingestion


@ingest_app.command("sentinel2")
def ingest_sentinel2(
    aoi_id: str | None = typer.Option(None, "--aoi", help="Area of interest id."),
    start: str | None = typer.Option(None, "--start", help="Start date, YYYY-MM-DD."),
    end: str | None = typer.Option(None, "--end", help="End date, YYYY-MM-DD."),
    force: bool = typer.Option(False, "--force", help="Re-ingest periods already present."),
    max_scenes: int = typer.Option(3, "--max-scenes", help="Scenes per MGRS tile per month."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Sentinel-2 spectral indicators: NDVI, NDMI, NBR as monthly composites."""
    _setup_logging(verbose)
    ensure_dirs()
    from .ingest.sentinel2 import ingest as run_ingest

    aoi = _resolve_aoi(aoi_id)
    default_start, default_end = default_history_range()
    summary = run_ingest(
        aoi,
        _parse_date(start, default_start),
        _parse_date(end, default_end),
        force=force,
        max_scenes_per_tile=max_scenes,
    )
    console.print(
        f"[green]done[/green] {summary.values_written} values written, "
        f"{summary.values_rejected} rejected, {summary.months_processed} months processed, "
        f"{summary.months_skipped} skipped, {summary.scenes_used} scene reads"
    )


@ingest_app.command("all")
def ingest_all(
    aoi_id: str | None = typer.Option(None, "--aoi"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    force: bool = typer.Option(False, "--force"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Every source: Sentinel-2, climate and soil moisture, terrain."""
    _setup_logging(verbose)
    ensure_dirs()
    aoi = _resolve_aoi(aoi_id)
    default_start, default_end = default_history_range()
    window = (_parse_date(start, default_start), _parse_date(end, default_end))

    from .ingest.sentinel2 import ingest as ingest_s2

    console.rule("[bold]Sentinel-2 spectral indicators")
    ingest_s2(aoi, *window, force=force)

    from .ingest.climate import ingest as ingest_climate
    from .ingest.substrate import ingest as ingest_substrate
    from .ingest.terrain import ingest as ingest_terrain

    console.rule("[bold]Terrain")
    ingest_terrain(aoi, force=force)

    console.rule("[bold]Land cover")
    from .ingest.landcover import ingest as ingest_landcover

    ingest_landcover(aoi, force=force)

    console.rule("[bold]Climate and soil moisture")
    ingest_climate(aoi, *window, force=force)

    # Last, because the score is composed from the indicators the earlier steps landed.
    console.rule("[bold]Wildfire substrate score")
    scored = ingest_substrate(aoi, *window, force=force)
    console.print(f"[green]done[/green] {scored} monthly substrate scores")

    console.rule("[bold]Map cells")
    from .cells import rebuild as rebuild_cells

    console.print(f"[green]done[/green] {len(rebuild_cells(aoi))} cell layers")


@app.command("seed")
def seed(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Cold start: ingest the default history window for the pilot area, end to end."""
    start, end = default_history_range()
    console.print(
        f"Seeding [bold]{PILOT_AOI.name}[/bold] from {start} to {end} "
        f"({settings().history_months} months)."
    )
    ingest_all(aoi_id=None, start=str(start), end=str(end), force=False, verbose=verbose)


# ------------------------------------------------------------------ areas of interest


@aoi_app.command("add")
def aoi_add(
    geojson: Path = typer.Option(..., "--geojson", exists=True, help="Path to a GeoJSON file."),
    aoi_id: str = typer.Option(..., "--id", help="Short id, lowercase with hyphens."),
    name: str = typer.Option(..., "--name"),
    description: str = typer.Option("", "--description"),
) -> None:
    """Register an area of interest from a GeoJSON geometry, Feature or FeatureCollection."""
    ensure_dirs()
    aoi = AreaOfInterest.from_geojson(geojson, aoi_id, name, description)
    grid = grid_for(aoi)
    conn = lake.connect()
    try:
        ghash = lake.register_aoi(conn, aoi, grid.pixel_count * grid.pixel_area_km2())
    finally:
        conn.close()
    console.print(
        f"[green]registered[/green] {aoi_id} — {grid.width}x{grid.height} at "
        f"{grid.resolution_m:g} m in {grid.crs}, geometry hash {ghash}"
    )
    console.print(f"Now run: gaia ingest all --aoi {aoi_id}")


@aoi_app.command("list")
def aoi_list() -> None:
    """Areas of interest registered in the lake."""
    conn = lake.connect(read_only=True)
    try:
        rows = conn.execute(
            "SELECT aoi_id, name, area_km2, analysis_crs, grid_resolution_m, geometry_hash "
            "FROM aoi ORDER BY aoi_id"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]no areas registered — run `gaia seed`[/yellow]")
        return

    table = Table(title="Areas of interest")
    for column in ("id", "name", "area km2", "crs", "res m", "geometry hash"):
        table.add_column(column)
    for row in rows:
        table.add_row(row[0], row[1], f"{row[2]:,.0f}", row[3], f"{row[4]:g}", row[5])
    console.print(table)


# -------------------------------------------------------------------------- coverage


@app.command("cells")
def cells(
    aoi_id: str | None = typer.Option(None, "--aoi"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Rebuild every map cell layer from the rasters on disk. No network."""
    _setup_logging(verbose)
    from .cells import rebuild

    aoi = _resolve_aoi(aoi_id)
    written = rebuild(aoi)
    table = Table(title="Cell layers")
    table.add_column("layer")
    table.add_column("cells")
    for layer, count in sorted(written.items()):
        table.add_row(layer, f"{count:,}")
    console.print(table)


@app.command("coverage")
def coverage(aoi_id: str | None = typer.Option(None, "--aoi")) -> None:
    """What the layer can currently answer for."""
    conn = lake.connect(read_only=True)
    try:
        where = "WHERE aoi_id = ?" if aoi_id else ""
        params = [aoi_id] if aoi_id else []
        rows = conn.execute(
            f"""
            SELECT aoi_id, indicator, unit,
                   min(period_start) AS first_start,
                   max(period_end) AS last_end,
                   count(*) AS periods,
                   avg(confidence) AS mean_confidence,
                   sum(CASE WHEN validation_status = 'validated' THEN 1 ELSE 0 END),
                   sum(CASE WHEN validation_status = 'flagged' THEN 1 ELSE 0 END),
                   sum(CASE WHEN validation_status = 'rejected' THEN 1 ELSE 0 END)
            FROM indicator_value
            {where}
            GROUP BY aoi_id, indicator, unit
            ORDER BY aoi_id, indicator
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]the lake is empty — run `gaia seed`[/yellow]")
        raise typer.Exit(code=1)

    table = Table(title="Coverage")
    for column in (
        "area",
        "indicator",
        "unit",
        "from",
        "to",
        "periods",
        "mean conf",
        "validated",
        "flagged",
        "rejected",
    ):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row[0],
            row[1],
            row[2],
            str(row[3]),
            str(row[4]),
            str(row[5]),
            f"{row[6]:.2f}",
            str(row[7]),
            str(row[8]),
            str(row[9]),
        )
    console.print(table)


@app.command("runs")
def runs(limit: int = typer.Option(10, "--limit")) -> None:
    """Recent pipeline runs, with their input and output digests."""
    conn = lake.connect(read_only=True)
    try:
        rows = conn.execute(
            "SELECT run_id, command, status, started_at, finished_at, observation_count, "
            "value_count, inputs_digest, outputs_digest, error "
            "FROM run_manifest ORDER BY started_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    finally:
        conn.close()

    table = Table(title="Runs")
    for column in (
        "run",
        "command",
        "status",
        "started",
        "obs",
        "values",
        "in digest",
        "out digest",
    ):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row[0][-8:],
            row[1],
            f"[red]{row[2]}[/red]" if row[2] == "failed" else row[2],
            str(row[3])[:19],
            str(row[5]),
            str(row[6]),
            (row[7] or "")[:12],
            (row[8] or "")[:12],
        )
    console.print(table)


if __name__ == "__main__":
    app()
