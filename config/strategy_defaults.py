"""Shared strategy defaults loaded from the cross-runtime JSON contract."""
import json
from pathlib import Path


DEFAULT_STRATEGY = json.loads(
    (Path(__file__).with_name('strategy_defaults.json')).read_text(encoding='utf-8')
)


def strategy_defaults() -> dict:
    return dict(DEFAULT_STRATEGY)
