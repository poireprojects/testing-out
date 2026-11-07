"""Plain-English explanations shown in tooltips throughout the app."""

GLOSSARY: dict[str, str] = {
    "account value": "Everything in the account right now: cash plus the current value of any stocks held.",
    "today's p/l": "Profit or loss since today's market open. Includes trades already closed and the "
                   "paper gain/loss on positions still open.",
    "realized": "Profit or loss that's locked in because the position was sold.",
    "unrealized": "Profit or loss on positions still open. It changes with the price until the position is sold.",
    "buying power": "How much money the broker says you can spend right now. This app never uses borrowed "
                    "money (margin), even if the broker offers it.",
    "settled cash": "Money from sales finishes 'settling' one business day later (called T+1). The app only "
                    "spends settled money, which avoids 'good-faith violations' in small cash accounts.",
    "position": "A stock the app currently owns.",
    "stop-loss": "A pre-set price where the app sells to limit a loss. In fast markets the actual sale can "
                 "happen below this price, so it limits — but cannot guarantee — the maximum loss.",
    "target": "A pre-set price where the app sells to take profit.",
    "broker stop": "A stop order held by the broker itself, so the position stays protected even if this app "
                   "or your computer turns off. It expires at the end of each trading day.",
    "app-watched stop": "The broker wouldn't accept a stop order, so the app is watching the price itself. "
                        "If the app closes or loses internet, this position is NOT protected.",
    "vwap": "Volume-Weighted Average Price: the average price everyone paid today, weighted by how many shares "
            "traded at each price. Above VWAP usually means buyers are in control.",
    "rsi": "Relative Strength Index (0–100): how strong recent up-moves are versus down-moves. Above ~70 can "
           "mean a stock ran up too fast; below ~30, that it fell too fast.",
    "relative volume": "Today's trading volume so far compared to a normal day at this time. 2.0× means twice "
                       "as much trading as usual — something is going on.",
    "momentum": "How much the price has moved recently. Strong momentum sometimes continues for a while.",
    "spread": "The gap between the highest price a buyer offers (bid) and the lowest a seller asks (ask). "
              "You pay part of it on every trade, so tight spreads are better.",
    "bps": "Basis points. 1 bps = 0.01%. A 10 bps spread on a $20 trade costs about 2 cents.",
    "opening range": "The high and low of the first 15 minutes of trading.",
    "breakout": "When price moves above a level it couldn't get past before, often on extra volume.",
    "moving average": "The average price over the last N minutes. It smooths out noise to show the trend.",
    "volatility": "How much a price typically swings. More volatility = bigger possible gains and losses.",
    "win rate": "The share of closed trades that made money. A high win rate can still lose money overall "
                "if the losses are bigger than the wins.",
    "profit factor": "Total money won divided by total money lost. Above 1.0 means more was won than lost.",
    "max drawdown": "The biggest drop from a high point in account value to a later low point.",
    "expectancy": "The average profit or loss per trade.",
    "benchmark": "What you'd have gotten by simply buying and holding the S&P 500 (SPY) instead.",
    "slippage": "The difference between the price you expected and the price you actually got.",
    "paper trading": "Practice trading with fake money at a real broker, using real market prices.",
    "live trading": "Trading real money. Off by default and requires your explicit authorization.",
    "demo": "A built-in simulator with made-up prices so you can explore the app before connecting a broker. "
            "Nothing in demo mode reflects real markets.",
    "autopilot": "When on, the app places trades by itself within your safety rules. When off, it still scans "
                 "and explains, but never trades.",
    "emergency stop": "Immediately halts all trading, cancels pending orders and sells every position the app "
                      "opened. Stays on until you reset it.",
    "daily loss limit": "If today's losses reach this amount, the app stops opening new trades until tomorrow. "
                        "Price gaps can still push the loss slightly past the limit.",
    "reinvest": "Position sizes grow as the account grows and shrink as it shrinks.",
    "fixed allocation": "The app trades with a set amount of money; profits above it are left alone.",
    "backtest": "Replaying a strategy on past market data, with realistic costs, to see how it would have done.",
    "out-of-sample": "Data the strategy never 'saw' while being evaluated. Results here are a fairer test "
                     "than results on the data used to design it.",
    "p-value": "Roughly: the chance the results are just luck. Smaller is better; we require below 0.10.",
    "evidence": "A strategy only counts as having evidence if it made money after costs on unseen data, "
                "beat random entries, and wasn't likely to be luck.",
    "iex": "The free market data feed. It comes from one exchange (IEX) — accurate prices for liquid stocks, "
           "but only a small slice of total trading volume.",
    "sip": "The full consolidated feed from all U.S. exchanges (paid plan).",
    "fractional shares": "Owning part of a share, e.g. 0.04 of a $500 stock for $20.",
    "day trade": "Buying and selling the same stock on the same day. This app closes every position before "
                 "the market closes.",
    "pdt": "The old 'pattern day trader' rule required $25,000 to day-trade often. FINRA retired it on "
           "June 4, 2026; brokers now use intraday margin rules instead.",
    "market order": "Buy or sell immediately at the best available price.",
    "limit order": "Buy or sell only at a set price or better. The app uses slightly-above-the-ask limit orders "
                   "to enter, which avoids bad fills in fast markets.",
    "shadow trades": "For every setup the AI reviewed, the app tracks what would have happened with the same "
                     "rules — so you can see whether the AI's choices actually help.",
    "confidence": "How sure the AI says it is (0–100%). Trades need at least your minimum confidence setting.",
    "evidence quality": "The AI's own rating of how strong and consistent the data is.",
    "risk engine": "A separate set of fixed rules that checks every trade. The AI can never override it.",
}


def explain(term: str) -> str:
    return GLOSSARY.get(term.lower().strip(), "")
