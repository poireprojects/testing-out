"""A read-only view of a stored budget proof (budget_proof.json), for rebuilding a report offline."""
from __future__ import annotations


class ProofView:
    def __init__(self, rec: dict):
        self.proven, self.reasons, self.totals = rec["proven"], rec["reasons"], rec["totals"]
