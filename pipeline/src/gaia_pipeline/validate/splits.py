"""Cross-validation that does not leak.

Wildfire severity is spatially autocorrelated at the scale of hundreds of metres: adjacent
cells share weather, share terrain, and usually burned in the same fire on the same
afternoon. Split those cells at random and the model gets to memorise a fire and be tested
on the rest of it. Published wildfire-susceptibility work shows exactly that, with AUC of
around 0.99 under a random split collapsing to 0.55-0.66 once the folds are spatially
disjoint. The 0.99 is not skill. It is the same afternoon appearing on both sides.

So the folds here are blocks of ground, not cells, and a buffer is cut around every test
block so that no training cell sits within a few kilometres of a cell being predicted.
There is a cost: the buffered-out cells are discarded, and with five folds that is a real
fraction of the data. Paying it is the whole point.

The temporal hold-out asks a different and harder question — can a model fitted on the
2015-2020 fire years say anything about 2021 and 2023 — and both are reported, because a
method that passes one and fails the other is telling you something specific.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

log = logging.getLogger(__name__)

#: Side length of a spatial block, in metres. Large enough to contain a typical fire's
#: footprint so that a single event does not span the training and test sides of a split,
#: small enough that five folds still tile the study area many times over.
BLOCK_SIZE_M = 20_000.0

#: How far a training cell must stay from any test cell.
DEFAULT_BUFFER_KM = 3.0


@dataclass(frozen=True)
class Fold:
    """One split, and an honest account of what it cost."""

    train: np.ndarray
    test: np.ndarray
    excluded_by_buffer: int

    @property
    def sizes(self) -> tuple[int, int]:
        return int(self.train.size), int(self.test.size)


def spatial_blocks(
    x: np.ndarray, y: np.ndarray, *, block_size_m: float = BLOCK_SIZE_M
) -> np.ndarray:
    """Assign every cell to a square block of ground, by projected coordinate."""
    col = np.floor(x / block_size_m).astype("int64")
    row = np.floor(y / block_size_m).astype("int64")
    keys = row * (col.max() - col.min() + 1) + col
    _, blocks = np.unique(keys, return_inverse=True)
    return blocks.astype("int64")


def spatial_folds(
    x: np.ndarray,
    y: np.ndarray,
    *,
    n_folds: int = 5,
    buffer_km: float = DEFAULT_BUFFER_KM,
    block_size_m: float = BLOCK_SIZE_M,
    seed: int = 0,
) -> list[Fold]:
    """Spatially blocked folds with a buffer between training and test ground.

    `x` and `y` are projected coordinates in metres — BC Albers for this study area. Every
    cell appears in exactly one test fold. Training cells inside the buffer are dropped from
    that fold only; they are still trained on in the folds where they are far from the test
    set, so the buffer costs coverage rather than data.
    """
    if x.shape != y.shape:
        raise ValueError("coordinate arrays must be the same length")
    if n_folds < 2:
        raise ValueError("cross-validation needs at least two folds")

    blocks = spatial_blocks(x, y, block_size_m=block_size_m)
    unique_blocks = np.unique(blocks)

    rng = np.random.default_rng(seed)
    assignment = rng.permutation(unique_blocks.size) % n_folds
    fold_of_block = dict(zip(unique_blocks.tolist(), assignment.tolist(), strict=True))
    fold_of_cell = np.array([fold_of_block[block] for block in blocks])

    coordinates = np.column_stack([x, y])
    tree = cKDTree(coordinates)
    buffer_m = buffer_km * 1000.0

    folds: list[Fold] = []
    for fold in range(n_folds):
        test = np.flatnonzero(fold_of_cell == fold)
        candidate_train = np.flatnonzero(fold_of_cell != fold)

        if test.size == 0:
            raise ValueError(f"fold {fold} is empty; too few blocks for {n_folds} folds")

        # Everything within the buffer of any test cell, regardless of which side it is on.
        near = tree.query_ball_point(coordinates[test], r=buffer_m)
        contaminated = np.unique(np.concatenate([np.asarray(hits) for hits in near]))

        train = np.setdiff1d(candidate_train, contaminated, assume_unique=False)
        folds.append(
            Fold(train=train, test=test, excluded_by_buffer=int(candidate_train.size - train.size))
        )

        log.info(
            "fold %d: %d train, %d test, %d dropped to the buffer",
            fold,
            train.size,
            test.size,
            folds[-1].excluded_by_buffer,
        )

    return folds


def temporal_holdout(
    years: np.ndarray, *, train_max_year: int = 2020, test_years: tuple[int, ...] = (2021, 2023)
) -> Fold:
    """Train on everything up to a year, test on named later fire years.

    No buffer here: the separation is time, and a cell that burned in 2018 and again in 2021
    is a legitimately hard case rather than leakage. It does mean a reburn appears on both
    sides, which is worth knowing when reading the numbers.
    """
    train = np.flatnonzero(years <= train_max_year)
    test = np.flatnonzero(np.isin(years, test_years))

    if train.size == 0 or test.size == 0:
        raise ValueError("temporal hold-out produced an empty side")

    return Fold(train=train, test=test, excluded_by_buffer=0)


def leakage_report(
    x: np.ndarray, y: np.ndarray, folds: list[Fold], *, buffer_km: float = DEFAULT_BUFFER_KM
) -> dict[str, float]:
    """Measure the thing the buffer is supposed to guarantee, rather than assuming it.

    Returns the smallest distance found between any training cell and any test cell across
    all folds. If that number is below the buffer, the split is not what it claims to be and
    every metric computed from it is inflated.
    """
    coordinates = np.column_stack([x, y])
    closest = np.inf

    for fold in folds:
        if fold.train.size == 0 or fold.test.size == 0:
            continue
        tree = cKDTree(coordinates[fold.train])
        distances, _ = tree.query(coordinates[fold.test], k=1)
        closest = min(closest, float(distances.min()))

    return {
        "minimum_train_test_distance_m": closest,
        "buffer_m": buffer_km * 1000.0,
        "holds": float(closest >= buffer_km * 1000.0),
    }
