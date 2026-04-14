# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
Bellman-Ford Arbitrage Detector.

Theory:
  - Model token exchange rates as a directed weighted graph
  - Edge (u, v) weight = -log(price_after_fee(u -> v))
  - A negative-weight cycle corresponds to a profitable arbitrage cycle
  - Bellman-Ford detects negative cycles in O(V*E) time

Features:
  - Multi-source Bellman-Ford (start from all nodes simultaneously)
  - Path reconstruction to recover the actual trade route
  - Profit calculation including flash loan premium
  - Optimal entry size estimation via liquidity-constrained search
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class PoolPrice:
    """Represents a token-pair price on a specific DEX pool."""
    token_in: str
    token_out: str
    price: float             # tokens_out / tokens_in (before fees)
    price_after_fee: float   # tokens_out / tokens_in (after fees)
    fee_bps: int = 30        # Fee in basis points (30 = 0.30%)
    liquidity: float = 1.0   # Liquidity in ETH equivalent
    dex: str = "unknown"     # DEX identifier


class PriceGraph:
    """
    Directed weighted graph of token exchange rates.
    Nodes = tokens, edges = DEX pools.
    """

    def __init__(self) -> None:
        self._prices: dict[tuple[str, str], list[PoolPrice]] = {}

    def add_price(self, pool: PoolPrice) -> None:
        key = (pool.token_in, pool.token_out)
        if key not in self._prices:
            self._prices[key] = []
        self._prices[key].append(pool)

    def best_price(self, token_in: str, token_out: str) -> Optional[PoolPrice]:
        """Return the pool with the best (highest) price for this pair."""
        pools = self._prices.get((token_in, token_out), [])
        if not pools:
            return None
        return max(pools, key=lambda p: p.price_after_fee)

    def tokens(self) -> list[str]:
        tokens: set[str] = set()
        for t_in, t_out in self._prices:
            tokens.add(t_in)
            tokens.add(t_out)
        return sorted(tokens)

    def to_weight_matrix(self) -> tuple[list[str], dict]:
        """
        Convert graph to Bellman-Ford weight matrix.
        Weight = -log(best_price_after_fee) for each edge.
        """
        tokens = self.tokens()
        weights: dict[tuple[str, str], float] = {}

        for (token_in, token_out), pools in self._prices.items():
            best = max(pools, key=lambda p: p.price_after_fee)
            if best.price_after_fee > 0:
                weights[(token_in, token_out)] = -math.log(best.price_after_fee)

        return tokens, weights


@dataclass
class ArbitrageOpportunity:
    """A discovered profitable arbitrage cycle."""
    cycle: list[str]
    pools: list[PoolPrice]
    gross_rate: float
    net_rate: float
    expected_profit_pct: float
    max_input_eth: float
    score: float = 0.0

    @property
    def is_profitable(self) -> bool:
        return self.net_rate > 1.0

    def __repr__(self) -> str:
        path = " -> ".join(self.cycle)
        return (f"ArbitrageOpportunity({path}, "
                f"profit={self.expected_profit_pct:.4f}%, "
                f"max_input={self.max_input_eth:.3f} ETH)")


class BellmanFord:
    """
    Multi-source Bellman-Ford arbitrage detector.

    Algorithm:
      1. Build weight matrix: w(u,v) = -log(rate_after_fee)
      2. Run V-1 relaxation passes
      3. On V-th pass, any further relaxation = negative cycle
      4. Reconstruct cycles using predecessor pointers
      5. Verify cycles and calculate exact profit
    """

    def __init__(self, config: dict) -> None:
        self.cfg = config
        self.flash_loan_fee = config.get("trading", {}).get("flash_loan_fee_bps", 9) / 10_000

    def detect(
        self,
        graph: PriceGraph,
        min_profit_pct: float = 0.05
    ) -> list[ArbitrageOpportunity]:
        tokens, weights = graph.to_weight_matrix()
        n = len(tokens)
        if n < 2:
            return []

        tok_idx = {t: i for i, t in enumerate(tokens)}
        dist: list[float] = [0.0] * n
        pred: list[int]   = [-1] * n

        for iteration in range(n - 1):
            updated = False
            for (u, v), w in weights.items():
                if u not in tok_idx or v not in tok_idx:
                    continue
                ui, vi = tok_idx[u], tok_idx[v]
                if dist[ui] + w < dist[vi] - 1e-12:
                    dist[vi] = dist[ui] + w
                    pred[vi] = ui
                    updated = True
            if not updated:
                break

        neg_cycle_nodes: set[int] = set()
        for (u, v), w in weights.items():
            if u not in tok_idx or v not in tok_idx:
                continue
            ui, vi = tok_idx[u], tok_idx[v]
            if dist[ui] + w < dist[vi] - 1e-12:
                neg_cycle_nodes.add(vi)

        if not neg_cycle_nodes:
            return []

        opportunities: list[ArbitrageOpportunity] = []
        seen_cycles: set[tuple] = set()

        for node_idx in neg_cycle_nodes:
            cycle_tokens = self._reconstruct_cycle(node_idx, pred, tokens, n)
            if not cycle_tokens:
                continue

            # Canonical key: rotate the cycle (excluding repeated last token)
            # to start at the lexicographically smallest token, then tuple.
            # This deduplicates cycles that start at different points but
            # follow the same path, while preserving direction and pool identity.
            inner = cycle_tokens[:-1]  # drop the repeated last token
            min_idx = inner.index(min(inner))
            canonical = tuple(inner[min_idx:] + inner[:min_idx])
            if canonical in seen_cycles:
                continue
            seen_cycles.add(canonical)

            opp = self._evaluate_cycle(cycle_tokens, graph)
            if opp and opp.expected_profit_pct >= min_profit_pct:
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.score, reverse=True)
        log.debug(f"Bellman-Ford: {len(opportunities)} opportunities found")
        return opportunities

    def _reconstruct_cycle(
        self,
        start: int,
        pred: list[int],
        tokens: list[str],
        n: int
    ) -> list[str]:
        v = start
        for _ in range(n):
            v = pred[v]
            if v == -1:
                return []

        cycle = []
        visited = set()
        u = v
        while u not in visited:
            visited.add(u)
            cycle.append(tokens[u])
            u = pred[u]
            if u == -1:
                return []

        start_tok = tokens[u]
        if start_tok not in cycle:
            return []
        idx = cycle.index(start_tok)
        cycle = cycle[idx:]
        cycle.append(start_tok)
        cycle.reverse()
        return cycle

    def _evaluate_cycle(
        self,
        cycle: list[str],
        graph: PriceGraph
    ) -> Optional[ArbitrageOpportunity]:
        if len(cycle) < 3:
            return None

        hops = list(zip(cycle[:-1], cycle[1:]))
        pools_used: list[PoolPrice] = []
        gross_rate = 1.0
        net_rate   = 1.0
        min_liquidity = float("inf")

        for token_in, token_out in hops:
            best = graph.best_price(token_in, token_out)
            if best is None or best.price <= 0:
                return None
            pools_used.append(best)
            gross_rate *= best.price
            net_rate   *= best.price_after_fee
            min_liquidity = min(min_liquidity, best.liquidity)

        net_rate_after_loan = net_rate / (1 + self.flash_loan_fee)
        profit_pct = (net_rate_after_loan - 1.0) * 100

        if net_rate_after_loan <= 1.0:
            return None

        max_input_eth = min(min_liquidity * 0.1, 100.0)
        score = profit_pct * math.sqrt(max_input_eth)

        return ArbitrageOpportunity(
            cycle=cycle,
            pools=pools_used,
            gross_rate=gross_rate,
            net_rate=net_rate_after_loan,
            expected_profit_pct=profit_pct,
            max_input_eth=max_input_eth,
            score=score
        )

    def find_best(
        self,
        graph: PriceGraph,
        min_profit_pct: float = 0.05
    ) -> Optional[ArbitrageOpportunity]:
        opps = self.detect(graph, min_profit_pct)
        return opps[0] if opps else None

# Compatibility alias used by nexus_arb/algorithms/__init__.py
BellmanFordArb = BellmanFord
