"""Autonomous research-and-decision prototype (phase 5). Research only: it simulates, it never places an order.

    observation -> investigation (evidence pack) -> thesis + decision (AI or baseline) -> deterministic risk
    controller -> simulated execution -> outcome tracking -> daily report

Everything is written to an append-only, hash-chained ledger before any outcome exists. See
research/PHASE5-AUTONOMOUS.md for the design, what is proven and what is not.

This package is not part of the desktop app (the app never imports it and the installer does not bundle it).
It reuses the app's pieces: the risk engine and sizing (stockpilot.risk), fee schedule (stockpilot.risk.fees), market
calendar (stockpilot.clock), AI providers (stockpilot.ai.providers) and the SEC EDGAR client (stockpilot.research.events).
"""
