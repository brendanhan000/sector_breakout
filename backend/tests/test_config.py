"""config.yaml binds to the engine, and the engine hides no thresholds.

"Every threshold is in config.yaml; no magic numbers in engine code" is an
acceptance criterion, so it gets a test rather than a code review convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.engine.params import EngineParams, UniverseSpec
from backend.settings import AppConfig, Settings, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"
STATE_MODULE = REPO_ROOT / "backend" / "engine" / "state.py"

# Structural constants that are not thresholds: array/loop bookkeeping and the
# zero that defines "the signal changed sign".
ALLOWED_NUMERIC_LITERALS = {0, 1, -1, 0.0}


def test_shipped_config_parses_and_binds():
    settings = Settings(config_path=CONFIG_PATH)
    assert isinstance(settings.config, AppConfig)
    assert isinstance(settings.engine_params, EngineParams)
    assert isinstance(settings.universe, UniverseSpec)


def test_universe_is_the_eleven_spdr_sectors_plus_soxx_plus_benchmark():
    universe = Settings(config_path=CONFIG_PATH).universe
    assert universe.benchmark == "SPY"
    assert len(universe.sectors) == 12
    assert set(universe.sector_symbols) == {
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SOXX",
    }
    assert set(universe.equal_weight_symbols) == {
        "RSPM", "RSPC", "RSPG", "RSPF", "RSPN", "RSPT", "RSPS", "RSPR", "RSPU", "RSPH", "RSPD",
    }
    assert len(set(universe.all_symbols)) == 24


def test_every_sector_has_a_distinct_equal_weight_twin_where_present():
    universe = Settings(config_path=CONFIG_PATH).universe
    twins = [s.equal_weight for s in universe.sectors if s.equal_weight]
    assert len(set(twins)) == len(twins)
    assert not set(twins) & set(universe.sector_symbols)


def test_state_machine_thresholds_are_asymmetric():
    """Hysteresis is mandatory. Symmetric thresholds cause boundary chatter."""
    p = Settings(config_path=CONFIG_PATH).engine_params.state
    assert p.pending_entry > p.pending_exit > p.confirmed_exit > 0


def test_state_horizon_and_breadth_horizon_are_real_horizons():
    p = Settings(config_path=CONFIG_PATH).engine_params
    assert p.channel.state_horizon in p.channel.horizons
    assert p.breadth.horizon in p.channel.horizons


def test_config_rejects_symmetric_thresholds(tmp_path):
    import yaml

    raw = yaml.safe_load(CONFIG_PATH.read_text())
    raw["state_machine"]["confirmed_exit"] = raw["state_machine"]["pending_entry"]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw))

    with pytest.raises(Exception, match="pending_entry"):
        load_config(bad)


def test_config_rejects_a_state_horizon_outside_the_horizon_set(tmp_path):
    import yaml

    raw = yaml.safe_load(CONFIG_PATH.read_text())
    raw["engine"]["channel"]["state_horizon"] = 999
    bad = tmp_path / "bad2.yaml"
    bad.write_text(yaml.safe_dump(raw))

    with pytest.raises(Exception, match="state_horizon"):
        load_config(bad)


def test_state_machine_code_contains_no_magic_numbers():
    """Scan the transition logic's AST for numeric literals.

    Every threshold the machine uses must arrive through StateParams. A literal
    appearing here means a number exists in two places — config.yaml and the
    code — and the two will drift.
    """
    tree = ast.parse(STATE_MODULE.read_text())

    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_next_state"
    )

    offenders = [
        node.value
        for node in ast.walk(target)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value not in ALLOWED_NUMERIC_LITERALS
    ]
    assert offenders == [], (
        f"magic numbers in the state machine transition logic: {offenders}. "
        "Every threshold belongs in config.yaml and must arrive via StateParams."
    )


def test_engine_params_have_no_defaults():
    """A default in the params dataclasses is a threshold living outside
    config.yaml, where it can silently diverge from the shipped value."""
    import dataclasses

    from backend.engine import params as params_module

    for name in dir(params_module):
        obj = getattr(params_module, name)
        if not dataclasses.is_dataclass(obj) or not isinstance(obj, type):
            continue
        for field in dataclasses.fields(obj):
            if name == "UniverseSpec" or (name, field.name) == ("SectorSpec", "equal_weight"):
                continue  # optional twin is structure, not a threshold
            assert field.default is dataclasses.MISSING, (
                f"{name}.{field.name} has a default of {field.default!r}"
            )
            assert field.default_factory is dataclasses.MISSING


def test_engine_package_does_not_import_the_application_layer():
    """The engine must stay pure. If it can read config or hit the database,
    the no-look-ahead truncation test stops being a real experiment."""
    engine_dir = REPO_ROOT / "backend" / "engine"
    forbidden = ("backend.settings", "backend.db", "backend.api", "backend.data", "sqlalchemy",
                 "fastapi", "yaml", "requests", "httpx", "yfinance")

    for path in engine_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for n in names:
                assert not any(n == f or n.startswith(f + ".") for f in forbidden), (
                    f"{path.name} imports {n}; the engine must not depend on the "
                    "application layer or on any I/O library"
                )


def test_engine_does_not_read_the_clock():
    """A signal that depends on 'now' is not reproducible and cannot be tested
    by truncation.

    Scans the AST for actual attribute access rather than the raw text, so that
    prose in a docstring does not trip the check.
    """
    banned = {"now", "today", "utcnow", "time"}
    engine_dir = REPO_ROOT / "backend" / "engine"

    for path in engine_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in banned:
                    owner = getattr(node.func.value, "id", None) or getattr(
                        node.func.value, "attr", ""
                    )
                    assert False, (
                        f"{path.name} reads the clock via {owner}.{node.func.attr}()"
                    )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in {"time", "datetime"}, (
                        f"{path.name} imports {alias.name}; the engine must be a pure "
                        "function of its arguments"
                    )
