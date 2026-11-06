"""The AI-mode replay asks the app's real AIEngine, sees only the past, and never reveals tickers or dates."""
import json
import random
from datetime import date

import pytest

from dkapp.ai.engine import AIEngine
from dkapp.backtest.ai_replay import compare_modes
from dkapp.backtest.engine import BTConfig, replay_portfolio, run_day
from dkapp.backtest.runner import synthetic_days
from dkapp.clock import MarketCalendar
from dkapp.config import AppSettings
from dkapp.data.demo_data import DemoMarket
from dkapp.storage.db import Database
from dkapp.strategies import ALL_STRATEGIES
from tests.harness import ScriptedAI

SYMS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META"]


@pytest.fixture(scope="module")
def history():
    days = synthetic_days(DemoMarket(SYMS, MarketCalendar(), 4), SYMS, date(2026, 11, 4), 6, warmup=5)
    cfg = BTConfig(min_signal_score=0.3)
    rng = random.Random(cfg.seed)
    trades = [t for d in days for t in run_day(d, list(ALL_STRATEGIES), cfg, rng)]
    return days, trades, cfg


def engine(tmp_path, provider):
    s = AppSettings()
    s.ai.decision_mode = "ai_confirm"
    return AIEngine(s, Database(tmp_path / "ai.db", "paper"), provider)


def test_ai_that_always_skips_takes_no_trades_and_rules_are_unchanged(history, tmp_path):
    days, trades, cfg = history
    ai = ScriptedAI(action="skip", select="none")
    out = compare_modes(days, trades, cfg, engine(tmp_path, ai), sample_for_filter=10)
    spy = {d.day: d.spy_trend for d in days}
    assert out["modes"]["rules_ranker"]["trades"] == len(replay_portfolio([t for t in trades if not t.random_baseline],
                                                                          cfg, True, spy))
    assert out["modes"]["ai_confirm"]["trades"] == 0
    assert out["modes"]["ai_select"]["trades"] == 0
    assert out["filter"]["approved"] == 0
    assert out["ai"]["costUsd"] == 0.0


def test_prompts_are_anonymised(history, tmp_path):
    days, trades, cfg = history
    ai = ScriptedAI(action="buy", confidence=0.9, quality="strong", select="first")
    out = compare_modes(days, trades, cfg, engine(tmp_path, ai), sample_for_filter=15)
    assert ai.prompts, "the AI was asked"
    for p in ai.prompts:
        for sym in SYMS:
            assert f'"{sym}"' not in p, f"ticker {sym} leaked into the prompt"
        for d in days:
            assert d.day.isoformat() not in p, "real date leaked into the prompt"
        assert "STOCK_" in p
    # an AI that approves everything confirms every ranked pick
    assert out["modes"]["ai_confirm"]["trades"] == out["modes"]["rules_ranker"]["trades"]
    assert out["filter"]["approved"] == out["filter"]["asked"]


def test_prompt_shows_only_bars_up_to_the_signal(history, tmp_path):
    from dkapp.backtest.ai_replay import PromptBuilder
    days, trades, cfg = history
    t = next(t for t in trades if not t.random_baseline and t.entry_i > 20)
    sig, ctx = PromptBuilder(days, cfg).build(t)
    assert len(ctx["recent_bars"]) == 15
    assert ctx["recent_bars"][-1]["c"] == pytest.approx(100.0, rel=1e-6), "last bar shown is the signal bar"
    assert sig.price == 100.0 and sig.symbol.startswith("STOCK_")
    json.dumps(ctx)


def test_prices_inside_explanations_are_rescaled_too(history):
    from dkapp.backtest.ai_replay import PromptBuilder, _rescale_text
    assert _rescale_text("broke above $60.33 on 2x volume", 100 / 50.0) == "broke above $120.66 on 2x volume"
    days, trades, cfg = history
    pb = PromptBuilder(days, cfg)
    for t in [t for t in trades if not t.random_baseline][:40]:
        sig, _ = pb.build(t)
        raw_prices = [float(x) for r in sig.reasons for x in __import__("re").findall(r"\$(\d+(?:\.\d+)?)", r)]
        assert all(40 <= x <= 250 for x in raw_prices), (sig.reasons, "dollar amounts must be on the rescaled ~100 scale")
