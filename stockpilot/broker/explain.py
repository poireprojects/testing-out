"""Turn a brokerage's order-rejection message into one plain-English sentence.

The raw message is always kept as well (in the order record), so nothing is hidden; this only
adds a translation a beginner can act on.
"""
from __future__ import annotations

import re

# (pattern, explanation) — first match wins; patterns are matched case-insensitively
_RULES: list[tuple[str, str]] = [
    (r"insufficient (buying power|funds|qty|quantity available)|not enough (buying power|cash)",
     "the account didn't have enough settled cash (or shares) for this order. nothing was bought or sold."),
    (r"not fractionable|fractional.*not (supported|allowed|available)|fractional orders",
     "this stock can't be traded in fractions at your broker, and a whole share costs more than the position limit."),
    (r"fractional.*(stop|time in force)|stop.*fractional",
     "the broker doesn't accept that kind of protective order for a fractional position; the app will watch "
     "the stop itself and keep retrying."),
    (r"wash trade|potential wash",
     "the broker blocked it as a possible wash trade (an opposite order for the same stock was still open). "
     "it will be retried once that order is done."),
    (r"pattern day trad|day ?trad(e|ing) (limit|protection)|\bpdt\b",
     "the broker's day-trading limit blocked this order."),
    (r"market (is )?closed|outside (of )?(regular )?(trading|market) hours|not (open|tradable) (at this|now)",
     "the market was closed, so the broker wouldn't take the order."),
    (r"(asset|symbol) .*not (active|tradable|found)|not tradable|halted|trading (is )?halted",
     "this stock can't be traded right now (halted, delisted or not offered by your broker)."),
    (r"shorting|short sale|cannot be sold short|would (open|create) a short",
     "the order would have sold more shares than the account owns (short selling is never allowed here)."),
    (r"(account|trading) (is )?(blocked|restricted|suspended)",
     "the brokerage has restricted this account — check for messages from your broker."),
    (r"(limit|stop) price .*(too|must|invalid|increment)|sub-?penny|price increment",
     "the order's price wasn't allowed by the exchange's price rules (e.g. too many decimals)."),
    (r"(qty|quantity|notional).*(must|invalid|too small|minimum)|minimum (order|notional)",
     "the order was too small for the broker's minimum."),
    (r"rate limit|too many requests|\b429\b",
     "the broker asked the app to slow down; it will try again shortly."),
    (r"timed? ?out|timeout|connection|unreachable|network|unconfirmed|never reached",
     "the connection to the broker failed, so it's unclear whether the order arrived; the app checks with the "
     "broker before trying again so nothing gets doubled."),
    (r"duplicate|client_order_id.*(exists|unique)",
     "the broker already had this exact order (it was sent once before); the app adopted the existing one."),
    (r"\bcancel", "the order was canceled before it filled."),
    (r"expired", "the order expired before it filled."),
]


def explain_rejection(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return "the broker didn't fill the order and gave no reason."
    low = text.lower()
    for pattern, explanation in _RULES:
        if re.search(pattern, low):
            return explanation
    return "the broker refused the order (its message is shown below)."


def with_raw(raw: str | None) -> str:
    """Plain explanation followed by the broker's own words."""
    text = (raw or "").strip()
    plain = explain_rejection(text)
    return plain if not text else f"{plain} (broker said: {text[:200]})"
