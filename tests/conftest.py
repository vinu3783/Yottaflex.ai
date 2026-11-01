from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from plantsim import config
from plantsim.generate import build


@pytest.fixture(scope="session")
def full_data(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("full") / "data"
    build(out, config.DEFAULT_SEED, config.PROFILES["full"].telemetry_points, "full")
    return out


@pytest.fixture(scope="session")
def starter_data(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("starter") / "data"
    build(out, config.DEFAULT_SEED, config.PROFILES["starter"].telemetry_points, "starter")
    return out


@pytest.fixture()
def starter_copy(starter_data, tmp_path) -> Path:
    dst = tmp_path / "copy"
    shutil.copytree(starter_data, dst)
    return dst
