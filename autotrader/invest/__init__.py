"""Forward, paper-only investing pilot (proposed registration INVEST-PILOT-1). Research only.

Question: do monthly AI stock selection and position sizing leave more money than buying VOO with the same dollars
on the same dates?

Two simulated $100 portfolios: `voo` buys and holds VOO; `ai` holds 70-100% VOO and up to three stocks from a
frozen candidate universe, each set to at most 10%. Once a month the AI reads dated public filings, valuations and
its current holdings and chooses; code enforces the evidence requirements, the limits and the accounting, and
records everything in an append-only, keyed, hash-chained ledger before the next session opens.

Nothing here places an order, calls a model or reads a credential. The genuine (model) path renders its exact
request offline and refuses to send it until the approvals in the registration exist. See
research/runs/2026-11-11-invest-pilot/REGISTRATION.md.

Not part of the desktop app: the app never imports it and the installer does not bundle it (`autotrader/*` is
non-app in packaging/app_changed.py).
"""
