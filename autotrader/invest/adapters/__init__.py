"""Offline adapters for the investing pilot: pure parsers and normalizers over saved raw responses.

Nothing in this package opens a network connection. Collection goes through an injectable `Transport`
(`raw.OfflineTransport` reads a verified raw store; `raw.RefusingTransport` is the default for anything live and
refuses). A later, separately approved collector may supply a live transport; it would write raw files and their
provenance into the same store format, and these parsers would read them unchanged.

* `raw`     the raw store: every file with its source URL, retrieval time and SHA-256, verified before use.
* `edgar`   submissions index, filing headers, reconciliation, XBRL company facts and trailing-four-quarter values,
            deterministic passage selection.
* `nport`   N-PORT-P holdings, the registered universe rule, and the CUSIP/CIK/ticker review table.
* `market`  unadjusted daily opens and closes, dividends, splits, symbol changes; unsupported actions flagged.
"""
