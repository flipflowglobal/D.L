# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/bellman_ford.py — SPFA-accelerated Bellman-Ford arbitrage scanner.

Edge weight:  w(u,v) = -log(p_eff_uv * sqrt(L_uv))
CLMM slippage: sqrt_P_new = sqrt_P -/+ delta/(2L), p_eff = sqrt_P_new^2
Net profit:   pi = (exp(-sum_w) - 1)*V_loan - AAVE_fee - gas_usd
MC:           500 samples eps~N(0,sigma^2), P_viable = fraction(profit>min)
Kelly:        f* = p/l - (1-p)/b, position = bankroll * max(f*,0) * 0.25
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("nexus_arb.bellman_ford")

TOKENS: Dict[str, str] = {
    "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    "USDC": "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC",
    "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
    "DAI":  "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    "WBTC": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
    "ARB":  "0x912CE59144191C1204E64559FE8253a0e49E6548",
    "LINK": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
    "UNI":  "0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0",
    "FRAX": "0x17FC002b466eEc40DaE837Fc4bE5c67993ddBd6F",
}

UNISWAP_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
FEE_TIERS = [100, 500, 3000, 10000]
AAVE_FEE_BPS = 5
GAS_COST_USD = 1.5
CACHE_TTL_S = 12.0
Q96 = 2 ** 96

FACTORY_ABI = [{"inputs": [{"internalType": "address","name": "tokenA","type": "address"},{"internalType": "address","name": "tokenB","type": "address"},{"internalType": "uint24","name": "fee","type": "uint24"}],"name": "getPool","outputs": [{"internalType": "address","name": "pool","type": "address"}],"stateMutability": "view","type": "function"}]
POOL_SLOT0_ABI = [{"inputs": [],"name": "slot0","outputs": [{"internalType": "uint160","name": "sqrtPriceX96","type": "uint160"},{"internalType": "int24","name": "tick","type": "int24"},{"internalType": "uint16","name": "observationIndex","type": "uint16"},{"internalType": "uint16","name": "observationCardinality","type": "uint16"},{"internalType": "uint16","name": "observationCardinalityNext","type": "uint16"},{"internalType": "uint8","name": "feeProtocol","type": "uint8"},{"internalType": "bool","name": "unlocked","type": "bool"}],"stateMutability": "view","type": "function"}]
POOL_LIQ_ABI = [{"inputs": [],"name": "liquidity","outputs": [{"internalType": "uint128","name": "","type": "uint128"}],"stateMutability": "view","type": "function"}]


@dataclass
class ArbRoute:
    path: List[str]
    addresses: List[str]
    fees: List[int]
    log_weight_sum: float
    gross_profit_pct: float
    net_profit_usd: float
    mc_viable_prob: float
    kelly_fraction: float
    flash_asset: str
    flash_amount_wei: int
    slippage_adjusted: bool = True
    block_number: int = 0
    ts: float = field(default_factory=time.time)


class EdgeCache:
    """12-second TTL cache (1 Arbitrum block)."""

    def __init__(self, ttl: float = CACHE_TTL_S) -> None:
        self._ttl = ttl
        self._store: Dict[tuple, Tuple[Any, float]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, key: tuple) -> Optional[tuple]:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            self._misses += 1
            return None
        self._hits += 1
        return value

    def set(self, key: tuple, value: tuple) -> None:
        self._store[key] = (value, time.monotonic())

    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def evict_stale(self) -> int:
        now = time.monotonic()
        stale = [k for k, (_, ts) in self._store.items() if now - ts > self._ttl]
        for k in stale:
            del self._store[k]
        return len(stale)


class BellmanFordScanner:
    """SPFA-accelerated Bellman-Ford arb scanner with CLMM slippage model."""

    _DEFAULT_CONFIG = {
        "MIN_NET_PROFIT_USD": 8.0,
        "REF_LOAN_ETH": 1.0,
        "MAX_HOPS": 4,
        "MC_SAMPLES": 500,
        "MC_VIABILITY_PROB": 0.90,
        "SLIPPAGE_BPS": 30,
        "ETH_USD_REF": 3200.0,
    }

    def __init__(self, w3=None, event_bus=None, config: Optional[Dict] = None) -> None:
        self._w3 = w3
        self._bus = event_bus
        self._cfg = {**self._DEFAULT_CONFIG, **(config or {})}
        self._cache = EdgeCache()
        self._cycle_count = 0
        self._last_routes: List[ArbRoute] = []

    def _clmm_effective_price(self, sqrt_px96: int, liquidity: int, trade_wei: int, zero_for_one: bool) -> float:
        if liquidity <= 0 or sqrt_px96 <= 0:
            return 1.0
        try:
            sqrt_p = sqrt_px96 / Q96
            delta = trade_wei / Q96
            two_l = max(2.0 * liquidity, 1e-30)
            sqrt_p_new = sqrt_p - delta / two_l if zero_for_one else sqrt_p + delta / two_l
            if sqrt_p_new <= 0:
                return 1.0
            p_eff = sqrt_p_new ** 2
            return (1.0 / p_eff) if not zero_for_one else p_eff
        except (ZeroDivisionError, OverflowError, ValueError):
            return 1.0

    async def _fetch_edge(self, sym_a: str, sym_b: str, fee: int) -> Optional[Tuple[float, float]]:
        cache_key = (sym_a, sym_b, fee)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if self._w3 is None:
            return None
        try:
            addr_a = TOKENS.get(sym_a)
            addr_b = TOKENS.get(sym_b)
            if not addr_a or not addr_b:
                return None
            factory = self._w3.eth.contract(
                address=self._w3.to_checksum_address(UNISWAP_V3_FACTORY),
                abi=FACTORY_ABI,
            )
            pool_addr = factory.functions.getPool(
                self._w3.to_checksum_address(addr_a),
                self._w3.to_checksum_address(addr_b),
                fee,
            ).call()
            null = "0x0000000000000000000000000000000000000000"
            if pool_addr == null:
                return None
            pool = self._w3.eth.contract(
                address=self._w3.to_checksum_address(pool_addr),
                abi=POOL_SLOT0_ABI + POOL_LIQ_ABI,
            )
            loop = asyncio.get_event_loop()
            slot0, liquidity = await asyncio.gather(
                loop.run_in_executor(None, pool.functions.slot0().call),
                loop.run_in_executor(None, pool.functions.liquidity().call),
            )
            sqrt_px96 = slot0[0]
            eth_usd = self._cfg["ETH_USD_REF"]
            loan_wei = int(self._cfg["REF_LOAN_ETH"] * 1e18)
            zero_for_one = int(addr_a, 16) < int(addr_b, 16)
            p_eff = self._clmm_effective_price(sqrt_px96, liquidity, loan_wei, zero_for_one)
            sqrt_p = max(sqrt_px96 / Q96, 1e-30)
            liq_usd = max(liquidity * 2.0 / sqrt_p * eth_usd, 1.0)
            result = (p_eff, liq_usd)
            self._cache.set(cache_key, result)
            return result
        except Exception as exc:
            logger.debug("_fetch_edge(%s,%s,%d): %s", sym_a, sym_b, fee, exc)
            return None

    async def _build_graph(self) -> Dict[str, Dict[str, Tuple[float, int, float]]]:
        syms = list(TOKENS.keys())
        tasks = []
        pairs = []
        for i, sym_a in enumerate(syms):
            for j, sym_b in enumerate(syms):
                if i >= j:
                    continue
                for fee in FEE_TIERS:
                    tasks.append(self._fetch_edge(sym_a, sym_b, fee))
                    pairs.append((sym_a, sym_b, fee))
        if not tasks:
            return {}
        results = await asyncio.gather(*tasks, return_exceptions=True)
        graph: Dict[str, Dict[str, Tuple[float, int, float]]] = {}
        for (sym_a, sym_b, fee), res in zip(pairs, results):
            if isinstance(res, Exception) or res is None:
                continue
            p_eff, liq_usd = res
            if p_eff <= 0 or not math.isfinite(p_eff):
                continue
            graph.setdefault(sym_a, {})
            existing = graph[sym_a].get(sym_b)
            if existing is None or fee < existing[1]:
                graph[sym_a][sym_b] = (p_eff, fee, liq_usd)
            graph.setdefault(sym_b, {})
            inv_p = 1.0 / p_eff
            existing_inv = graph[sym_b].get(sym_a)
            if existing_inv is None or fee < existing_inv[1]:
                graph[sym_b][sym_a] = (inv_p, fee, liq_usd)
        return graph

    def spfa_detect_cycles(self, graph: Dict[str, Dict[str, Tuple[float, int, float]]], source: str = "WETH") -> List[Tuple[List[str], float]]:
        nodes = list(graph.keys())
        if not nodes:
            return []
        n = len(nodes)
        idx = {sym: i for i, sym in enumerate(nodes)}
        INF = float("inf")
        dist = [INF] * n
        prev = [-1] * n
        count = [0] * n
        in_q = [False] * n
        src_i = idx.get(source, 0)
        dist[src_i] = 0.0
        q = deque([src_i])
        in_q[src_i] = True
        cycles: List[Tuple[List[str], float]] = []
        while q:
            u_i = q.popleft()
            in_q[u_i] = False
            u_sym = nodes[u_i]
            for v_sym, (price, _fee, liq_usd) in graph.get(u_sym, {}).items():
                if v_sym not in idx:
                    continue
                v_i = idx[v_sym]
                w = -math.log(max(price, 1e-30) * math.sqrt(max(liq_usd, 1.0)))
                ndst = dist[u_i] + w
                if ndst < dist[v_i] - 1e-9:
                    dist[v_i] = ndst
                    prev[v_i] = u_i
                    count[v_i] += 1
                    if count[v_i] >= n:
                        result = self._trace_cycle(v_i, prev, nodes, graph)
                        if result is not None:
                            cycles.append(result)
                    elif not in_q[v_i]:
                        q.append(v_i)
                        in_q[v_i] = True
        return cycles

    def _trace_cycle(self, start: int, prev: List[int], nodes: List[str], graph: Dict) -> Optional[Tuple[List[str], float]]:
        n = len(nodes)
        node = start
        for _ in range(n):
            p = prev[node]
            if p < 0:
                return None
            node = p
        cycle_start = node
        seen: Dict[int, int] = {}
        path: List[int] = []
        cur = cycle_start
        for step in range(n + 2):
            if cur in seen:
                path = path[seen[cur]:]
                break
            seen[cur] = step
            path.append(cur)
            p = prev[cur]
            if p < 0:
                return None
            cur = p
        else:
            return None
        if len(path) < 2:
            return None
        path_syms = [nodes[i] for i in path] + [nodes[path[0]]]
        log_sum = 0.0
        for i in range(len(path_syms) - 1):
            a, b = path_syms[i], path_syms[i + 1]
            edge = graph.get(a, {}).get(b)
            if edge:
                log_sum += -math.log(max(edge[0], 1e-30) * math.sqrt(max(edge[2], 1.0)))
        return (path_syms, log_sum)

    def _dfs_enumerate(self, graph: Dict, source: str = "WETH", max_hops: int = 4) -> List[Tuple[List[str], List[int], float]]:
        results: List[Tuple[List[str], List[int], float]] = []

        def _dfs(cur: str, path: List[str], fees: List[int], log_sum: float, visited: set) -> None:
            if len(path) > max_hops + 1:
                return
            for nxt, (price, fee, liq_usd) in graph.get(cur, {}).items():
                w = -math.log(max(price, 1e-30) * math.sqrt(max(liq_usd, 1.0)))
                new_sum = log_sum + w
                if nxt == source and len(path) >= 3:
                    if new_sum < -1e-4:
                        results.append((path + [source], fees + [fee], new_sum))
                    continue
                if nxt in visited:
                    continue
                visited.add(nxt)
                _dfs(nxt, path + [nxt], fees + [fee], new_sum, visited)
                visited.discard(nxt)

        _dfs(source, [source], [], 0.0, {source})
        return results

    def monte_carlo_validate(self, rates: List[float], loan_usd: float, min_profit: float, slippage_bps: int = 30, n: int = 500) -> float:
        if not rates or loan_usd <= 0:
            return 0.0
        sigma = slippage_bps / 10000.0 / math.sqrt(3)
        eps = np.random.normal(0.0, sigma, (n, len(rates)))
        r_arr = np.array(rates, dtype=float)
        gross = np.prod(r_arr * np.exp(eps), axis=1) - 1.0
        prem = loan_usd * AAVE_FEE_BPS / 10000.0
        profit = gross * loan_usd - prem - GAS_COST_USD
        return float(np.mean(profit > min_profit))

    def kelly_position_size(self, mc_prob: float, profits: np.ndarray) -> float:
        wins = profits[profits > 0]
        losses = profits[profits <= 0]
        if len(wins) == 0 or len(losses) == 0:
            return 0.0
        p = float(np.clip(mc_prob, 0.0, 1.0))
        b = float(np.mean(wins)) / max(float(np.mean(np.abs(losses))), 1e-9)
        f_star = p - (1.0 - p) / max(b, 0.01)
        return float(max(f_star, 0.0) * 0.25)

    def _synthetic_graph(self) -> Dict[str, Dict[str, Tuple[float, int, float]]]:
        syms = ["WETH", "USDC", "USDT", "DAI", "WBTC"]
        prices = {"WETH": 3200.0, "USDC": 1.0, "USDT": 1.0, "DAI": 1.001, "WBTC": 62000.0}
        graph: Dict[str, Dict[str, Tuple[float, int, float]]] = {}
        for a in syms:
            graph[a] = {}
            for b in syms:
                if a == b:
                    continue
                p_ab = prices[b] / prices[a]
                h = int(hashlib.sha256(f"{a}{b}".encode()).hexdigest(), 16)
                spread = 1.0 + ((h & 0xF) % 5) * 0.0001
                graph[a][b] = (p_ab * spread, 500, 1_000_000.0)
        return graph

    def _path_fees(self, path: List[str], graph: Dict) -> List[int]:
        return [graph.get(path[i], {}).get(path[i + 1], (0, 3000, 0))[1] for i in range(len(path) - 1)]

    def _path_rates(self, path: List[str], graph: Dict) -> List[float]:
        return [graph.get(path[i], {}).get(path[i + 1], (1.0, 0, 0))[0] for i in range(len(path) - 1)]

    def _sim_profits(self, rates: List[float], loan_usd: float, prem: float, n: int, bps: int) -> np.ndarray:
        sigma = bps / 10000.0 / math.sqrt(3)
        if not rates:
            return np.zeros(n)
        eps = np.random.normal(0.0, sigma, (n, len(rates)))
        return np.prod(np.array(rates) * np.exp(eps), axis=1) * loan_usd - loan_usd - prem - GAS_COST_USD

    async def scan(self) -> List[ArbRoute]:
        self._cycle_count += 1

        async def _emit(name: str, data: dict) -> None:
            if self._bus is not None:
                try:
                    await self._bus.emit(name, {**data, "component": "bellman_ford"})
                except Exception:
                    pass

        await _emit("bf_scan_start", {"cycle": self._cycle_count})
        try:
            t0 = time.monotonic()
            graph = await self._build_graph()
            if not graph:
                logger.warning("BellmanFordScanner: no live RPC — using synthetic graph")
                graph = self._synthetic_graph()

            raw_spfa = self.spfa_detect_cycles(graph)
            raw_dfs = self._dfs_enumerate(graph, max_hops=self._cfg["MAX_HOPS"])

            seen: set = set()
            all_paths: List[Tuple[List[str], List[int], float]] = []
            for path_syms, log_sum in raw_spfa:
                key = frozenset(path_syms)
                if key not in seen:
                    seen.add(key)
                    all_paths.append((path_syms, self._path_fees(path_syms, graph), log_sum))
            for path_syms, fees, log_sum in raw_dfs:
                key = frozenset(path_syms)
                if key not in seen:
                    seen.add(key)
                    all_paths.append((path_syms, fees, log_sum))

            eth_usd = self._cfg["ETH_USD_REF"]
            loan_eth = self._cfg["REF_LOAN_ETH"]
            loan_usd = loan_eth * eth_usd
            min_prof = self._cfg["MIN_NET_PROFIT_USD"]
            mc_thr = self._cfg["MC_VIABILITY_PROB"]
            bps = self._cfg["SLIPPAGE_BPS"]
            n_mc = self._cfg["MC_SAMPLES"]

            results: List[ArbRoute] = []
            for path_syms, fees, log_sum in all_paths:
                if log_sum >= 0:
                    continue
                gross = math.exp(-log_sum) - 1.0
                prem = loan_usd * AAVE_FEE_BPS / 10000.0
                net = gross * loan_usd - prem - GAS_COST_USD
                rates = self._path_rates(path_syms, graph)
                mc_p = self.monte_carlo_validate(rates, loan_usd, min_prof, bps, n_mc)
                if mc_p < mc_thr:
                    await _emit("bf_mc_fail", {"path": "->".join(path_syms), "mc_prob": mc_p})
                    continue
                profits = self._sim_profits(rates, loan_usd, prem, n_mc, bps)
                kelly = self.kelly_position_size(mc_p, profits)
                addrs = [TOKENS.get(s, s) for s in path_syms]
                results.append(ArbRoute(
                    path=path_syms,
                    addresses=addrs,
                    fees=fees,
                    log_weight_sum=log_sum,
                    gross_profit_pct=gross * 100.0,
                    net_profit_usd=net,
                    mc_viable_prob=mc_p,
                    kelly_fraction=kelly,
                    flash_asset=TOKENS.get(path_syms[0], path_syms[0]),
                    flash_amount_wei=int(loan_eth * 1e18),
                ))
                await _emit("bf_new_route", {"path": "->".join(path_syms), "profit_usd": net})

            results.sort(key=lambda r: r.net_profit_usd, reverse=True)
            self._last_routes = results
            elapsed = (time.monotonic() - t0) * 1000
            await _emit("bf_scan_complete", {
                "profitable": len(results),
                "duration_ms": elapsed,
                "cache_hit_rate": self._cache.hit_rate(),
            })
            return results
        except Exception as exc:
            logger.error("BellmanFordScanner.scan(): %s", exc, exc_info=True)
            await _emit("bf_scan_error", {"error": str(exc)})
            return []

    @property
    def last_routes(self) -> List[ArbRoute]:
        return list(self._last_routes)
