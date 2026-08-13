"""Record the climate fixtures. Run by hand when the recorded window has to change.

    uv run --project pipeline python pipeline/tests/eii/fixtures/record_climate.py

The tests never call the network. This does, once, and writes what it read to
``climate/lattice-corners.npz`` so that every later run reads the same bytes. The window
deliberately spans 2021 into 2022, which is where the published store changes from year
files to 504-hour chunks — the seam the read plan has to stitch without a duplicate or a
gap.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np

from gaia_pipeline.eii.sources import om_archive

HERE = Path(__file__).parent / "climate"

#: The four corners of the study area, which is what the tests read.
POINTS = [(49.0, -120.6), (49.0, -119.5), (50.0, -120.6), (50.0, -119.5)]

ERA5_START, ERA5_END = date(2021, 1, 1), date(2023, 12, 31)
LAND_START, LAND_END = date(2023, 5, 31), date(2023, 9, 1)

ERA5_VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "precipitation",
    "shortwave_radiation",
    "wind_u_component_10m",
    "wind_v_component_10m",
]
LAND_VARIABLES = ["soil_moisture_7_to_28cm", "soil_moisture_28_to_100cm"]


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    recorded: dict[str, np.ndarray] = {}

    for variable in ERA5_VARIABLES:
        print(f"copernicus_era5/{variable} {ERA5_START} .. {ERA5_END}")
        recorded[f"copernicus_era5|{variable}"] = om_archive.read_hourly(
            om_archive.ERA5, variable, POINTS, ERA5_START, ERA5_END
        ).astype("float32")

    for variable in LAND_VARIABLES:
        print(f"copernicus_era5_land/{variable} {LAND_START} .. {LAND_END}")
        recorded[f"copernicus_era5_land|{variable}"] = om_archive.read_hourly(
            om_archive.ERA5_LAND, variable, POINTS, LAND_START, LAND_END
        ).astype("float32")

    recorded["elevation|copernicus_era5"] = om_archive.elevation(om_archive.ERA5, POINTS)
    recorded["elevation|copernicus_era5_land"] = om_archive.elevation(om_archive.ERA5_LAND, POINTS)
    recorded["window|copernicus_era5"] = np.array(
        [ERA5_START.toordinal(), ERA5_END.toordinal()], dtype="int64"
    )
    recorded["window|copernicus_era5_land"] = np.array(
        [LAND_START.toordinal(), LAND_END.toordinal()], dtype="int64"
    )
    recorded["points"] = np.array(POINTS, dtype="float64")

    out = HERE / "lattice-corners.npz"
    np.savez_compressed(out, **recorded)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
