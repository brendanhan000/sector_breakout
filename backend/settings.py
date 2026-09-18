"""Application settings: ``config.yaml`` + environment, validated by Pydantic v2.

Split of responsibilities:
  * config.yaml  — everything about *behaviour* (universe, windows, thresholds).
                   Checked into git, reviewable in a diff, the single place a
                   threshold may live.
  * environment  — everything about *secrets and deployment* (database URL,
                   Schwab OAuth credentials). Never checked in.

``Settings.engine_params`` is the bridge into the pure engine: it converts the
validated YAML into the frozen dataclasses the engine accepts. The engine itself
never imports this module.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.engine.params import (
    ATRParams,
    BetaParams,
    BreadthParams,
    ChannelParams,
    CorrelationParams,
    DispersionParams,
    EngineParams,
    RegimeParams,
    RRGParams,
    RVolParams,
    SectorSpec,
    StateParams,
    UniverseSpec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


# --------------------------------------------------------------------------- #
# config.yaml schema
# --------------------------------------------------------------------------- #
class SectorConfig(BaseModel):
    symbol: str
    name: str
    equal_weight: str | None = None  # None -> no breadth signal


class UniverseConfig(BaseModel):
    benchmark: str
    sectors: list[SectorConfig]

    @field_validator("sectors")
    @classmethod
    def _unique_symbols(cls, v: list[SectorConfig]) -> list[SectorConfig]:
        syms = [s.symbol for s in v]
        if len(set(syms)) != len(syms):
            raise ValueError("duplicate sector symbols in universe")
        ew = [s.equal_weight for s in v if s.equal_weight]
        if len(set(ew)) != len(ew):
            raise ValueError("duplicate equal-weight symbols in universe")
        return v


class ValidationConfig(BaseModel):
    max_gap_trading_days: int = Field(gt=0)
    allow_zero_volume: bool
    envelope_rtol: float = Field(gt=0)
    adjustment_rtol: float = Field(gt=0)
    adjustment_check_bars: int = Field(gt=0)


class DataConfig(BaseModel):
    provider: str
    backfill_years: int = Field(gt=0)
    validation: ValidationConfig


class ATRConfig(BaseModel):
    period: int = Field(gt=0)


class BetaConfig(BaseModel):
    window: int = Field(gt=1)
    residual_base: float = Field(gt=0)


class ChannelConfig(BaseModel):
    horizons: list[int]
    state_horizon: int = Field(gt=0)
    clip: float = Field(gt=0)
    ewm_span_floor: int = Field(gt=0)
    ewm_span_divisor: int = Field(gt=0)

    @model_validator(mode="after")
    def _state_horizon_present(self) -> "ChannelConfig":
        if any(h <= 0 for h in self.horizons):
            raise ValueError("channel horizons must all be positive")
        if self.state_horizon not in self.horizons:
            raise ValueError(
                f"state_horizon {self.state_horizon} must be one of horizons {self.horizons}"
            )
        return self


class RVolConfig(BaseModel):
    span: int = Field(gt=0)


class BreadthConfig(BaseModel):
    horizon: int = Field(gt=0)
    narrow_threshold: float


class EngineConfig(BaseModel):
    atr: ATRConfig
    beta: BetaConfig
    channel: ChannelConfig
    rvol: RVolConfig
    breadth: BreadthConfig

    @model_validator(mode="after")
    def _breadth_horizon_present(self) -> "EngineConfig":
        if self.breadth.horizon not in self.channel.horizons:
            raise ValueError(
                f"breadth.horizon {self.breadth.horizon} must be one of "
                f"channel.horizons {self.channel.horizons}"
            )
        return self


class StateMachineConfig(BaseModel):
    pending_entry: float
    pending_exit: float
    confirmed_exit: float
    confirm_consecutive_closes: int = Field(gt=0)
    confirm_rvol: float = Field(gt=0)
    fail_window_bars: int = Field(ge=0)
    failed_cooldown_bars: int = Field(ge=0)

    @model_validator(mode="after")
    def _asymmetric(self) -> "StateMachineConfig":
        # Hysteresis is mandatory, not stylistic: entry must sit strictly above
        # exit or the machine chatters at the boundary and the grid is unreadable.
        if not (self.pending_entry > self.pending_exit > self.confirmed_exit):
            raise ValueError(
                "state machine requires pending_entry > pending_exit > confirmed_exit "
                f"(got {self.pending_entry} / {self.pending_exit} / {self.confirmed_exit})"
            )
        if self.confirmed_exit <= 0:
            raise ValueError("confirmed_exit must be > 0 so it is distinguishable from failure")
        return self


class DispersionConfig(BaseModel):
    smoothing_window: int = Field(gt=0)
    percentile_window: int = Field(gt=0)
    low_threshold: float = Field(ge=0, le=100)


class CorrelationConfig(BaseModel):
    window: int = Field(gt=1)
    sparkline_bars: int = Field(gt=0)


class RegimeConfig(BaseModel):
    dispersion: DispersionConfig
    correlation: CorrelationConfig


class RRGConfig(BaseModel):
    window: int = Field(gt=1)
    origin: float
    offset: float
    tail_length: int = Field(gt=0)


class APIConfig(BaseModel):
    default_history_days: int = Field(gt=0)
    max_history_days: int = Field(gt=0)


class SchedulerConfig(BaseModel):
    enabled: bool
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    timezone: str


class AppConfig(BaseModel):
    """The whole of config.yaml, validated."""

    universe: UniverseConfig
    data: DataConfig
    engine: EngineConfig
    state_machine: StateMachineConfig
    regime: RegimeConfig
    rrg: RRGConfig
    api: APIConfig
    scheduler: SchedulerConfig


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Secrets and deployment knobs. Everything else lives in config.yaml."""

    model_config = SettingsConfigDict(
        env_prefix="SECTOR_", env_file=".env", extra="ignore", case_sensitive=False
    )

    database_url: str = "postgresql+psycopg2://localhost:5432/sector_breakout"
    config_path: Path = DEFAULT_CONFIG_PATH
    log_level: str = "INFO"

    # Schwab data comes from the central schwab_hub, which owns all credentials.
    schwab_hub_url: str = "http://127.0.0.1:8765"

    @property
    def config(self) -> AppConfig:
        return load_config(self.config_path)

    # ---- bridge into the pure engine ------------------------------------- #
    @property
    def universe(self) -> UniverseSpec:
        u = self.config.universe
        return UniverseSpec(
            benchmark=u.benchmark,
            sectors=tuple(
                SectorSpec(symbol=s.symbol, name=s.name, equal_weight=s.equal_weight)
                for s in u.sectors
            ),
        )

    @property
    def engine_params(self) -> EngineParams:
        c = self.config
        return EngineParams(
            atr=ATRParams(period=c.engine.atr.period),
            beta=BetaParams(
                window=c.engine.beta.window, residual_base=c.engine.beta.residual_base
            ),
            channel=ChannelParams(
                horizons=tuple(c.engine.channel.horizons),
                state_horizon=c.engine.channel.state_horizon,
                clip=c.engine.channel.clip,
                ewm_span_floor=c.engine.channel.ewm_span_floor,
                ewm_span_divisor=c.engine.channel.ewm_span_divisor,
            ),
            rvol=RVolParams(span=c.engine.rvol.span),
            breadth=BreadthParams(
                horizon=c.engine.breadth.horizon,
                narrow_threshold=c.engine.breadth.narrow_threshold,
            ),
            state=StateParams(
                pending_entry=c.state_machine.pending_entry,
                pending_exit=c.state_machine.pending_exit,
                confirmed_exit=c.state_machine.confirmed_exit,
                confirm_consecutive_closes=c.state_machine.confirm_consecutive_closes,
                confirm_rvol=c.state_machine.confirm_rvol,
                fail_window_bars=c.state_machine.fail_window_bars,
                failed_cooldown_bars=c.state_machine.failed_cooldown_bars,
            ),
            regime=RegimeParams(
                dispersion=DispersionParams(
                    smoothing_window=c.regime.dispersion.smoothing_window,
                    percentile_window=c.regime.dispersion.percentile_window,
                    low_threshold=c.regime.dispersion.low_threshold,
                ),
                correlation=CorrelationParams(
                    window=c.regime.correlation.window,
                    sparkline_bars=c.regime.correlation.sparkline_bars,
                ),
            ),
            rrg=RRGParams(
                window=c.rrg.window,
                origin=c.rrg.origin,
                offset=c.rrg.offset,
                tail_length=c.rrg.tail_length,
            ),
        )


@lru_cache(maxsize=8)
def load_config(path: Path) -> AppConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
