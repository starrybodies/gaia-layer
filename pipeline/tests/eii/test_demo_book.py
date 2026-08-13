"""The demo book has two jobs and both of them are refusals.

It must refuse to be mistaken for a portfolio: every value in it is invented, and the file
has to say so loudly enough that a screenshot of it cannot be quoted as a price.

It must refuse to carry anything finer than a cell. The privacy claim the portfolio surface
makes is that a client sends H3 identifiers and never addresses, and a claim like that is
only worth what the format enforces. So the tests sweep the serialised book for anything
that parses as a coordinate, and check that a field nobody anticipated is rejected rather
than passed through.

Nothing here reaches Overture. The aggregation from footprints to cells is the only step
that ever holds a coordinate, and it is tested directly on arrays.
"""

from __future__ import annotations

import json
import re

import h3
import numpy as np
import pytest

from gaia_pipeline.eii import demo_book
from gaia_pipeline.eii.area import H3_PARENT_RES, H3_RES, STUDY_AREA
from gaia_pipeline.eii.demo_book import LeakedDetailError

#: Four clusters in the study area, each with enough buildings to be eligible.
CLUSTERS = [(49.88, -119.50), (50.27, -119.27), (49.50, -119.59), (49.16, -119.55)]


@pytest.fixture()
def footprints() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    lat: list[float] = []
    lon: list[float] = []
    area: list[float] = []
    for index, (centre_lat, centre_lon) in enumerate(CLUSTERS):
        count = 40 + index * 10
        lat.extend(centre_lat + rng.normal(0.0, 0.004, count))
        lon.extend(centre_lon + rng.normal(0.0, 0.006, count))
        area.extend(rng.uniform(80.0, 400.0, count))
    return np.array(lat), np.array(lon), np.array(area)


@pytest.fixture()
def tally(footprints) -> dict[str, dict[str, float]]:
    return demo_book.cells_from_footprints(*footprints)


@pytest.fixture()
def book(tally) -> dict:
    return demo_book.book_from_cells(tally, size=8, seed=3, minimum_buildings=1)


class TestAggregation:
    def test_every_footprint_lands_in_exactly_one_cell(self, tally, footprints) -> None:
        assert sum(entry["buildings"] for entry in tally.values()) == footprints[0].size

    def test_the_cells_are_the_resolution_the_archive_is_built_on(self, tally) -> None:
        assert {h3.get_resolution(cell) for cell in tally} == {H3_RES}

    def test_the_area_conversion_lands_at_a_building_scale(self) -> None:
        """A square degree is 1.2e10 m2. Getting this factor wrong is invisible in a ratio."""
        sql = demo_book.overture_query(STUDY_AREA)
        assert str(demo_book.DEGREE_M) in sql
        assert "cos(radians(" in sql
        assert "ST_Area_Spheroid" not in sql

    def test_footprint_area_is_carried_not_recomputed(self, tally, footprints) -> None:
        total = sum(entry["footprint_m2"] for entry in tally.values())
        assert total == pytest.approx(float(footprints[2].sum()))

    def test_an_unlocatable_footprint_is_dropped_rather_than_placed(self) -> None:
        """A NaN coordinate placed at 0,0 would put a building in the Atlantic."""
        result = demo_book.cells_from_footprints(
            np.array([49.5, np.nan]), np.array([-119.5, -119.5]), np.array([100.0, 100.0])
        )
        assert sum(entry["buildings"] for entry in result.values()) == 1

    def test_mismatched_inputs_are_refused(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            demo_book.cells_from_footprints(
                np.array([49.5]), np.array([-119.5, -119.6]), np.array([100.0, 100.0])
            )


class TestItCannotBeMistakenForAPortfolio:
    def test_it_is_labelled_synthetic_at_the_top(self, book) -> None:
        assert book["synthetic"] is True
        assert "SYNTHETIC" in book["label"]

    def test_the_warning_says_the_values_are_invented(self, book) -> None:
        assert "invented" in book["warning"]
        assert "not prices" in book["warning"]

    def test_the_value_field_carries_the_word_in_its_name(self, book) -> None:
        """A column called `insured_value` gets quoted. One called `synthetic_...` does not."""
        for entry in book["cells"]:
            assert "synthetic_insured_value" in entry
            assert "insured_value" not in entry

    def test_it_names_the_footprint_release_it_was_built_from(self, book) -> None:
        assert book["footprint_source"]["release"] == demo_book.OVERTURE_RELEASE
        assert book["footprint_source"]["access_route"] == "anonymous S3"


class TestItCarriesNothingFinerThanACell:
    def test_a_cell_entry_has_no_coordinate_field(self, book) -> None:
        assert all(
            not ({"lat", "lon", "latitude", "longitude", "address"} & set(entry))
            for entry in book["cells"]
        )

    def test_the_serialised_book_contains_nothing_that_parses_as_a_coordinate(self, book) -> None:
        """The sweep, not the schema: a field added later is the failure this catches."""
        rendered = json.dumps(book["cells"])
        # Study-area latitudes are 49-50 and longitudes -118 to -121. Any bare decimal in
        # those ranges in the cell list is a coordinate that escaped.
        suspicious = re.findall(r"-?1[12]\d\.\d{3,}|\b[45]\d\.\d{3,}\b", rendered)
        assert suspicious == []

    def test_an_unexpected_field_is_refused_rather_than_written(self, book) -> None:
        book["cells"][0]["street_address"] = "1 Example Road"
        with pytest.raises(LeakedDetailError, match="street_address"):
            demo_book.assert_cells_only(book)

    def test_a_finer_cell_is_refused(self, tally) -> None:
        finer = {
            h3.latlng_to_cell(49.88, -119.5, H3_RES + 2): {"buildings": 9.0, "footprint_m2": 900.0}
        }
        with pytest.raises(LeakedDetailError, match="finer disclosure"):
            demo_book.book_from_cells(finer, size=1, minimum_buildings=1)

    def test_writing_goes_through_the_check(self, book, tmp_path) -> None:
        book["cells"][0]["geometry"] = "POLYGON((...))"
        with pytest.raises(LeakedDetailError):
            demo_book.write_book(book, tmp_path / "book.json")
        assert not (tmp_path / "book.json").exists()


class TestTheBookItself:
    def test_each_cell_carries_its_res_7_parent(self, book) -> None:
        """C3 aggregates res-8 up to res-7, and the parent is a fact rather than a lookup."""
        for entry in book["cells"]:
            assert entry["h3_parent"] == h3.cell_to_parent(entry["h3"], H3_PARENT_RES)

    def test_it_is_deterministic_for_a_seed(self, tally) -> None:
        first = demo_book.book_from_cells(tally, size=5, seed=11, minimum_buildings=1)
        second = demo_book.book_from_cells(tally, size=5, seed=11, minimum_buildings=1)
        assert [entry["h3"] for entry in first["cells"]] == [
            entry["h3"] for entry in second["cells"]
        ]

    def test_a_different_seed_gives_a_different_book(self, tally) -> None:
        first = demo_book.book_from_cells(tally, size=4, seed=1, minimum_buildings=1)
        second = demo_book.book_from_cells(tally, size=4, seed=2, minimum_buildings=1)
        assert first["cells"] != second["cells"]

    def test_every_cell_carries_a_positive_value(self, book) -> None:
        """The bug this catches shipped once: a NaN area became 0 and the whole book was 0."""
        assert book["cells"]
        for entry in book["cells"]:
            assert entry["footprint_m2"] > 0.0, entry
            assert entry["synthetic_insured_value"] > 0, entry
        assert book["totals"]["synthetic_insured_value"] > 0

    def test_an_unmeasurable_footprint_is_not_counted_as_no_footprint(self) -> None:
        tally = demo_book.cells_from_footprints(
            np.array([49.88, 49.88]), np.array([-119.5, -119.5]), np.array([200.0, np.nan])
        )
        entry = next(iter(tally.values()))

        assert entry["buildings"] == 2.0
        assert entry["measured"] == 1.0
        assert entry["footprint_m2"] == pytest.approx(200.0)

    def test_a_cell_with_no_measurable_footprint_cannot_enter_the_book(self) -> None:
        tally = demo_book.cells_from_footprints(
            np.array([49.88] * 6), np.array([-119.5] * 6), np.full(6, np.nan)
        )
        with pytest.raises(ValueError, match="measurable footprint"):
            demo_book.book_from_cells(tally, size=1, minimum_buildings=1)

    def test_the_totals_are_the_sum_of_the_rows(self, book) -> None:
        assert book["totals"]["exposures"] == sum(entry["exposures"] for entry in book["cells"])
        assert book["totals"]["synthetic_insured_value"] == sum(
            entry["synthetic_insured_value"] for entry in book["cells"]
        )

    def test_it_never_asks_for_more_cells_than_exist(self, tally) -> None:
        book = demo_book.book_from_cells(tally, size=10_000, seed=0, minimum_buildings=1)
        assert 0 < book["totals"]["cells"] <= len(tally)

    def test_a_tally_with_nothing_eligible_is_a_loud_error(self, tally) -> None:
        with pytest.raises(ValueError, match="enough buildings"):
            demo_book.book_from_cells(tally, size=4, minimum_buildings=10_000)


class TestTheOvertureQuery:
    def test_it_filters_to_the_study_area(self) -> None:
        sql = demo_book.overture_query(STUDY_AREA)
        bbox = STUDY_AREA.bbox()

        assert str(bbox.west) in sql and str(bbox.north) in sql
        assert "bbox.xmin" in sql and "bbox.ymax" in sql

    def test_it_reads_the_pinned_release_anonymously(self) -> None:
        """A moving release is not a demo, and a requester-pays bucket is not a demo either."""
        sql = demo_book.overture_query(STUDY_AREA)

        assert demo_book.OVERTURE_RELEASE in sql
        assert sql.count("s3://overturemaps-us-west-2") == 1

    def test_it_selects_no_identifier_that_would_survive_into_the_book(self) -> None:
        sql = demo_book.overture_query(STUDY_AREA)
        assert " id" not in sql and "names" not in sql and "addresses" not in sql
