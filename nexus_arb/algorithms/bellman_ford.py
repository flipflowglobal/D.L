"""
nexus_arb.algorithms.bellman_ford
==================================

Negative-cycle detection for multi-hop DEX arbitrage.

Theory
------
Represent the token exchange graph as a directed weighted graph where:
  - Nodes  = token addresses / symbols
  - Edges  = (token_a, token_b, dex_name) with weight = -log(exchange_rate)

A negative-weight cycle in this graph corresponds to a profitable
arbitrage route: trading around the cycle returns more tokens than started.

Bellman-Ford finds shortest paths from a source node, relaxing all edges
V-1 times.  Any edge that can still be relaxed in the V-th pass lies on a
negative cycle.

Complexity
----------
  Time  : O(V · E)   where V = #tokens, E = #(token_pair, DEX) combinations
  Space : O(V)       distance + predecessor arrays only

Formal Specification
---------------------
  Preconditions:
    - edges: list[tuple[str, str, float]]  (from_token, to_token, rate>0)
    - source token must appear in the graph

  Postconditions:
    - Returns ArbitrageResult with has_cycle, cycle, profit_ratio
    - profit_ratio > 1.0  ⟺  has_cycle is True
    - cycle is a closed path (first == last) when has_cycle is True

  Invariants:
    - Weights stored as -log(rate); negative sum ⟺ positive product > 1
    - Cycle traversal is deterministic given consistent edge ordering
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class ArbitrageResult:
    """Result of a Bellman-Ford negative-cycle search."""
    has_cycle:    bool
    cycle:        List[str]        # token path; first == last when has_cycle
    profit_ratio: float            # product of exchange rates along cycle (>1 = profit)
    cycle_edges:  List[Tuple[str, str, float]]  # (from, to, rate) tuples


class BellmanFordArb:
    """
    Multi-hop DEX arbitrage finder via Bellman-Ford negative-cycle detection.

    Usage
    -----
    >>> arb = BellmanFordArb()
    >>> arb.add_edge("WETH", "USDC", 2000.0, "uniswap_v3")
    >>> arb.add_edge("USDC", "WETH", 0.0005005, "sushiswap")   # slightly better
    >>> result = arb.find_arbitrage("WETH")
    >>> result.has_cycle
    True
    >>> result.profit_ratio > 1.0
    True
    """

    def __init__(self) -> None:
        # edges: list of (from_node, to_node, log_weight, rate, dex)
        self._edges: List[Tuple[str, str, float, float, str]] = []
        self._nodes: set = set()

    # ── graph construction ────────────────────────────────────────────────────

    def add_edge(
        self,
        from_token: str,
        to_token: str,
        rate: float,
        dex: str = "unknown",
    ) -> None:
        """
        Add a directed exchange edge.

        Parameters
        ----------
        from_token : token being sold
        to_token   : token being received
        rate       : units of to_token per one from_token  (must be > 0)
        dex        : exchange name (for result annotation)
        """
        if rate <= 0:
            raise ValueError(f"Exchange rate must be positive, got {rate}")
        log_w = -math.log(rate)    # negative because Bellman-Ford finds min-cost paths
        self._edges.append((from_token, to_token, log_w, rate, dex))
        self._nodes.add(from_token)
        self._nodes.add(to_token)

    def add_price_matrix(self, prices: Dict[str, Dict[str, float]], dex: str = "matrix") -> None:
        """
        Convenience method: add all pairs from a {from: {to: rate}} matrix.

        Example
        -------
        prices = {
            "WETH": {"USDC": 2000.0, "DAI": 1999.5},
            "USDC": {"WETH": 0.0005, "DAI": 0.999},
            "DAI":  {"USDC": 1.001,  "WETH": 0.0005002},
        }
        """
        for from_t, tos in prices.items():
            for to_t, rate in tos.items():
                if from_t != to_t and rate > 0:
                    self.add_edge(from_t, to_t, rate, dex)

    def clear(self) -> None:
        """Remove all edges and nodes."""
        self._edges.clear()
        self._nodes.clear()

    # ── core algorithm ────────────────────────────────────────────────────────

    def find_arbitrage(self, source: Optional[str] = None) -> ArbitrageResult:
        """
        Run Bellman-Ford from `source` and detect negative-weight cycles.

        If source is None, uses an arbitrary node from the graph.

        Returns
        -------
        ArbitrageResult with has_cycle, cycle path, profit_ratio.
        """
        if not self._nodes:
            return ArbitrageResult(False, [], 1.0, [])

        nodes = list(self._nodes)
        src   = source if source in self._nodes else nodes[0]

        # Add a virtual source with zero-weight edges to every node so that
        # all negative cycles are reachable regardless of connectivity.
        INF = float("inf")
        dist: Dict[str, float] = {n: INF for n in nodes}
        pred: Dict[str, Optional[str]] = {n: None for n in nodes}
        pred_edge: Dict[str, Optional[Tuple]] = {n: None for n in nodes}

        dist[src] = 0.0

        # Virtual zero-weight edges from src to every node
        virtual_edges = [(src, n, 0.0, 1.0, "_virtual") for n in nodes if n != src]
        all_edges = self._edges + virtual_edges

        # V-1 relaxation passes
        V = len(nodes)
        for _ in range(V - 1):
            updated = False
            for (u, v, w, rate, dex) in all_edges:
                if dist[u] < INF and dist[u] + w < dist[v] - 1e-12:
                    dist[v] = dist[u] + w
                    pred[v] = u
                    pred_edge[v] = (u, v, rate, dex)
                    updated = True
            if not updated:
                break   # early termination

        # V-th pass: if any edge can still be relaxed, it is on a negative cycle
        cycle_node: Optional[str] = None
        for (u, v, w, rate, dex) in self._edges:   # exclude virtual
            if dist[u] < INF and dist[u] + w < dist[v] - 1e-12:
                cycle_node = v
                break

        if cycle_node is None:
            return ArbitrageResult(False, [], 1.0, [])

        # Trace back to find the cycle (walk V steps to ensure we're inside).
        # Guard against None predecessors — can occur on nodes only reachable via
        # virtual zero-weight edges that never got a real predecessor recorded.
        visited = {cycle_node}
        node = cycle_node
        for _ in range(V):
            nxt = pred.get(node)
            if nxt is None:
                # Cannot trace further; report no exploitable cycle
                return ArbitrageResult(False, [], 1.0, [])
            node = nxt
            if node in visited:
                cycle_start = node
                break
            visited.add(node)
        else:
            cycle_start = node

        # Extract cycle path from cycle_start back to itself
        cycle_path: List[str] = []
        cycle_edge_list: List[Tuple[str, str, float]] = []
        node = cycle_start
        while True:
            cycle_path.append(node)
            e = pred_edge[node]
            if e is not None:
                cycle_edge_list.append((e[0], e[1], e[2]))
            node = pred[node]
            if node == cycle_start or node is None:
                break
        cycle_path.append(cycle_start)   # close the cycle

        cycle_path.reverse()
        cycle_edge_list.reverse()

        # Compute true profit ratio (product of rates along cycle)
        profit_ratio = 1.0
        for (_, _, r) in cycle_edge_list:
            profit_ratio *= r

        return ArbitrageResult(
            has_cycle=profit_ratio > 1.0,
            cycle=cycle_path,
            profit_ratio=round(profit_ratio, 8),
            cycle_edges=cycle_edge_list,
        )

    # ── multi-source search ───────────────────────────────────────────────────

    def find_best_arbitrage(self) -> ArbitrageResult:
        """
        Run find_arbitrage from every node and return the highest-profit cycle.
        Useful when the graph has multiple disconnected components.

        Complexity: O(V² · E)  — use only on small graphs (<20 tokens).
        """
        best = ArbitrageResult(False, [], 1.0, [])
        for node in list(self._nodes):
            result = self.find_arbitrage(source=node)
            if result.has_cycle and result.profit_ratio > best.profit_ratio:
                best = result
        return best


# ── Legacy compatibility classes ──────────────────────────────────────────────

@dataclass
class PoolPrice:
    """Legacy PoolPrice descriptor used by FlashLoanExecutor.execute() and tests."""
    token_in:       str
    token_out:      str
    price:          float
    price_after_fee: float
    fee_bps:        int
    liquidity:      float
    dex:            str


@dataclass
class ArbitrageOpportunity:
    """Legacy ArbitrageOpportunity used by FlashLoanExecutor.execute() and tests."""
    cycle:               List[str]
    pools:               List[PoolPrice]
    gross_rate:          float
    net_rate:            float
    expected_profit_pct: float
    max_input_eth:       float
    score:               float
