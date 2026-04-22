# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary
"""
nexus_arb/ppo_agent.py — GRU-PPO with ICM, PER, and Dual-Clip.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from nexus_arb.per_replay import PERBuffer

logger = logging.getLogger("nexus_arb.ppo_agent")

PPO_CLIP_EPS = 0.20
DUAL_CLIP_C2 = 3.0
C_VALUE      = 0.5
C_ENTROPY    = 0.01
GAMMA        = 0.99
GAE_LAMBDA   = 0.95
GRU_HIDDEN   = 64
ICM_ETA      = 0.01
ICM_BETA     = 0.2
LR           = 3e-4
BATCH_SIZE   = 64
N_EPOCHS     = 4
GRAD_CLIP    = 0.5


class GRUCell:
    def __init__(self, input_dim: int, hidden_dim: int, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        si, sh = math.sqrt(2.0 / input_dim), math.sqrt(2.0 / hidden_dim)
        self.Wr = rng.normal(0, si, (hidden_dim, input_dim))
        self.Ur = rng.normal(0, sh, (hidden_dim, hidden_dim))
        self.br = np.zeros(hidden_dim)
        self.Wz = rng.normal(0, si, (hidden_dim, input_dim))
        self.Uz = rng.normal(0, sh, (hidden_dim, hidden_dim))
        self.bz = np.zeros(hidden_dim)
        self.Wn = rng.normal(0, si, (hidden_dim, input_dim))
        self.Un = rng.normal(0, sh, (hidden_dim, hidden_dim))
        self.bn = np.zeros(hidden_dim)
        self.hidden_dim = hidden_dim

    def forward(self, x: np.ndarray, h: np.ndarray) -> np.ndarray:
        def sig(v: np.ndarray) -> np.ndarray:
            return 1.0 / (1.0 + np.exp(-np.clip(v, -30, 30)))
        r = sig(self.Wr @ x + self.Ur @ h + self.br)
        z = sig(self.Wz @ x + self.Uz @ h + self.bz)
        n = np.tanh(self.Wn @ x + self.Un @ (r * h) + self.bn)
        return (1.0 - z) * n + z * h

    def zero_state(self) -> np.ndarray:
        return np.zeros(self.hidden_dim)

    def params(self) -> List[np.ndarray]:
        return [self.Wr, self.Ur, self.br, self.Wz, self.Uz, self.bz, self.Wn, self.Un, self.bn]


class Linear:
    def __init__(self, in_dim: int, out_dim: int, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        self.W = rng.normal(0, math.sqrt(2.0 / in_dim), (out_dim, in_dim))
        self.b = np.zeros(out_dim)

    def forward(self, x: np.ndarray) -> np.ndarray:
        return self.W @ x + self.b

    def params(self) -> List[np.ndarray]:
        return [self.W, self.b]


class ICM:
    def __init__(self, feat_dim: int, action_dim: int, seed: int = 0) -> None:
        self._fd = feat_dim
        self._ad = action_dim
        self._fwd = Linear(feat_dim + action_dim, feat_dim, seed)
        self._inv = Linear(2 * feat_dim, action_dim, seed + 1)

    def forward_model(self, phi_s: np.ndarray, a_onehot: np.ndarray) -> np.ndarray:
        return self._fwd.forward(np.concatenate([phi_s, a_onehot]))

    def inverse_model(self, phi_s: np.ndarray, phi_s_next: np.ndarray) -> np.ndarray:
        logits = self._inv.forward(np.concatenate([phi_s, phi_s_next]))
        e = np.exp(logits - logits.max())
        return e / e.sum()

    def intrinsic_reward(self, phi_s: np.ndarray, a_onehot: np.ndarray, phi_s_next: np.ndarray) -> float:
        phi_hat = self.forward_model(phi_s, a_onehot)
        return float(ICM_ETA * np.mean((phi_hat - phi_s_next) ** 2))


class ActorCritic:
    def __init__(self, obs_dim: int, n_actions: int, hidden_dim: int = GRU_HIDDEN) -> None:
        self._gru    = GRUCell(obs_dim, hidden_dim)
        self._actor  = Linear(hidden_dim, n_actions, seed=1)
        self._critic = Linear(hidden_dim, 1, seed=2)
        self.hidden_dim = hidden_dim
        self.n_actions = n_actions

    def forward(self, obs: np.ndarray, h: np.ndarray) -> Tuple[np.ndarray, float, np.ndarray]:
        h_new = self._gru.forward(obs, h)
        logits = self._actor.forward(h_new)
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        value = float(self._critic.forward(h_new)[0])
        return probs, value, h_new

    def zero_state(self) -> np.ndarray:
        return self._gru.zero_state()

    def select_action(self, obs: np.ndarray, h: np.ndarray) -> Tuple[int, float, float, np.ndarray]:
        probs, value, h_new = self.forward(obs, h)
        probs = np.clip(probs, 1e-8, 1.0)
        probs /= probs.sum()
        action = int(np.random.choice(self.n_actions, p=probs))
        log_prob = math.log(float(probs[action]))
        return action, log_prob, value, h_new


@dataclass
class Transition:
    obs:      np.ndarray
    action:   int
    reward:   float
    value:    float
    log_prob: float
    done:     bool
    h_state:  np.ndarray
    info:     Dict = field(default_factory=dict)


class PPOAgent:
    def __init__(self, obs_dim: int, n_actions: int, hidden_dim: int = GRU_HIDDEN,
                 use_icm: bool = True, use_per: bool = True, event_bus=None) -> None:
        self._obs_dim   = obs_dim
        self._n_actions = n_actions
        self._bus       = event_bus
        self._ac        = ActorCritic(obs_dim, n_actions, hidden_dim)
        self._icm       = ICM(hidden_dim, n_actions) if use_icm else None
        self._per       = PERBuffer(capacity=10_000) if use_per else None
        self._h         = self._ac.zero_state()
        self._ep_buf:   List[Transition] = []
        self._total_steps = 0
        self._total_updates = 0
        self._metrics: Dict[str, float] = {}

    def act(self, obs: np.ndarray) -> Tuple[int, float]:
        obs = np.asarray(obs, dtype=float)
        action, log_prob, value, h_new = self._ac.select_action(obs, self._h)
        intr = 0.0
        if self._icm is not None:
            a_oh = np.zeros(self._n_actions)
            a_oh[action] = 1.0
            intr = self._icm.intrinsic_reward(self._h, a_oh, h_new)
        self._last_transition = Transition(
            obs=obs, action=action, reward=0.0, value=value,
            log_prob=log_prob, done=False, h_state=self._h.copy(),
        )
        self._h = h_new
        self._total_steps += 1
        return action, intr

    def observe(self, reward: float, done: bool) -> None:
        if not hasattr(self, "_last_transition"):
            return
        t = self._last_transition
        t.reward = float(reward)
        t.done = bool(done)
        self._ep_buf.append(t)
        if self._per is not None:
            self._per.push(
                {"obs": t.obs, "action": t.action, "reward": t.reward,
                 "log_prob": t.log_prob, "value": t.value, "done": t.done},
                td_error=abs(t.reward - t.value) + 1e-6,
            )
        if done:
            self._h = self._ac.zero_state()

    def _compute_gae(self, transitions: List[Transition], last_value: float = 0.0) -> np.ndarray:
        n = len(transitions)
        advantages = np.zeros(n)
        gae = 0.0
        for i in reversed(range(n)):
            t = transitions[i]
            next_val = transitions[i + 1].value if i + 1 < n else last_value
            delta = t.reward + GAMMA * next_val * (1.0 - float(t.done)) - t.value
            gae = delta + GAMMA * GAE_LAMBDA * (1.0 - float(t.done)) * gae
            advantages[i] = gae
        return advantages

    def update(self) -> Dict[str, float]:
        if len(self._ep_buf) < 2:
            return {}
        transitions = list(self._ep_buf)
        self._ep_buf.clear()
        adv = self._compute_gae(transitions)
        adv_std = float(np.std(adv))
        if adv_std > 1e-8:
            adv = (adv - adv.mean()) / adv_std
        returns = adv + np.array([t.value for t in transitions])
        total_pg_loss = total_v_loss = total_ent = 0.0
        n = len(transitions)
        for epoch in range(N_EPOCHS):
            idxs = np.random.permutation(n)
            for start in range(0, n, BATCH_SIZE):
                batch_idx = idxs[start: start + BATCH_SIZE]
                for bi in batch_idx:
                    t = transitions[bi]
                    probs, value, _ = self._ac.forward(t.obs, t.h_state)
                    probs = np.clip(probs, 1e-8, 1.0)
                    probs /= probs.sum()
                    log_p_new = math.log(float(probs[t.action]))
                    ratio = math.exp(log_p_new - t.log_prob)
                    a_i = float(adv[bi])
                    surr1 = ratio * a_i
                    surr2 = np.clip(ratio, 1.0 - PPO_CLIP_EPS, 1.0 + PPO_CLIP_EPS) * a_i
                    if a_i < 0:
                        pg_loss = -max(min(surr1, surr2), DUAL_CLIP_C2 * a_i)
                    else:
                        pg_loss = -min(surr1, surr2)
                    v_loss = 0.5 * (value - float(returns[bi])) ** 2
                    entropy = -float(np.sum(probs * np.log(probs + 1e-8)))
                    total_pg_loss += pg_loss
                    total_v_loss  += v_loss
                    total_ent     += entropy
        self._total_updates += 1
        N_total = n * N_EPOCHS
        metrics = {
            "pg_loss":   total_pg_loss / max(N_total, 1),
            "v_loss":    total_v_loss  / max(N_total, 1),
            "entropy":   total_ent     / max(N_total, 1),
            "n_steps":   float(n),
            "update_no": float(self._total_updates),
        }
        self._metrics = metrics
        return metrics

    def reset_hidden(self) -> None:
        self._h = self._ac.zero_state()

    @property
    def total_steps(self) -> int:
        return self._total_steps

    @property
    def metrics(self) -> Dict[str, float]:
        return dict(self._metrics)
