# Copyright (c) 2026 Darcel King. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""
PPO — Proximal Policy Optimization for Execution Timing.

The agent observes market state and chooses to EXECUTE, WAIT, or SKIP.
Trained via clipped surrogate PPO with GAE-lambda advantage estimation.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions import Categorical

log = logging.getLogger(__name__)

EXECUTE   = 0
WAIT      = 1
SKIP      = 2
N_ACTIONS = 3
STATE_DIM = 8


class ActorCritic(nn.Module):
    """Shared-backbone Actor-Critic network with GELU activations."""

    def __init__(self, state_dim: int = STATE_DIM, hidden_dim: int = 256) -> None:
        super().__init__()
        self.backbone = nn.Sequential(
            nn.LayerNorm(state_dim),
            nn.Linear(state_dim, 512),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(512, hidden_dim),
            nn.GELU()
        )
        self.actor_head = nn.Sequential(
            nn.Linear(hidden_dim, 128),
            nn.GELU(),
            nn.Linear(128, N_ACTIONS)
        )
        self.critic_head = nn.Sequential(
            nn.Linear(hidden_dim, 128),
            nn.GELU(),
            nn.Linear(128, 1)
        )
        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.orthogonal_(m.weight, gain=np.sqrt(2))
                nn.init.constant_(m.bias, 0.0)
        nn.init.orthogonal_(self.actor_head[-1].weight, gain=0.01)

    def forward(self, x: torch.Tensor):
        feat   = self.backbone(x)
        logits = self.actor_head(feat)
        value  = self.critic_head(feat).squeeze(-1)
        return logits, value

    def act(self, state: np.ndarray) -> tuple[int, float, float]:
        x = torch.FloatTensor(state).unsqueeze(0)
        with torch.no_grad():
            logits, value = self.forward(x)
        dist     = Categorical(logits=logits)
        action   = dist.sample()
        log_prob = dist.log_prob(action)
        return int(action.item()), float(log_prob.item()), float(value.item())

    def evaluate(
        self,
        states: torch.Tensor,
        actions: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        logits, values = self.forward(states)
        dist      = Categorical(logits=logits)
        log_probs = dist.log_prob(actions)
        entropy   = dist.entropy()
        return log_probs, values, entropy


@dataclass
class Transition:
    state:    np.ndarray
    action:   int
    reward:   float
    done:     bool
    log_prob: float
    value:    float


class RolloutBuffer:
    def __init__(self, maxlen: int = 2048) -> None:
        self.buffer: deque[Transition] = deque(maxlen=maxlen)

    def add(self, t: Transition) -> None:
        self.buffer.append(t)

    def clear(self) -> None:
        self.buffer.clear()

    def __len__(self) -> int:
        return len(self.buffer)

    def compute_gae(
        self,
        last_value: float,
        gamma: float = 0.99,
        lam: float = 0.95
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        transitions = list(self.buffer)
        n = len(transitions)
        states    = np.array([t.state    for t in transitions], dtype=np.float32)
        actions   = np.array([t.action   for t in transitions], dtype=np.int64)
        rewards   = np.array([t.reward   for t in transitions], dtype=np.float32)
        log_probs = np.array([t.log_prob for t in transitions], dtype=np.float32)
        values    = np.array([t.value    for t in transitions], dtype=np.float32)
        dones     = np.array([t.done     for t in transitions], dtype=np.float32)

        advantages = np.zeros(n, dtype=np.float32)
        gae = 0.0
        for t in reversed(range(n)):
            next_val = values[t + 1] if t < n - 1 else last_value
            delta    = rewards[t] + gamma * next_val * (1 - dones[t]) - values[t]
            gae      = delta + gamma * lam * (1 - dones[t]) * gae
            advantages[t] = gae

        returns     = advantages + values
        advantages  = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        return states, actions, returns, log_probs, advantages


class PPOAgent:
    """PPO agent for flash loan execution timing."""

    def __init__(self, config: dict) -> None:
        ppo_cfg = config.get("algorithms", {}).get("ppo", {})
        self.hidden_dim    = ppo_cfg.get("hidden_dim", 256)
        self.lr_actor      = ppo_cfg.get("lr_actor", 3e-4)
        self.lr_critic     = ppo_cfg.get("lr_critic", 1e-3)
        self.gamma         = ppo_cfg.get("gamma", 0.99)
        self.gae_lambda    = ppo_cfg.get("gae_lambda", 0.95)
        self.clip_eps      = ppo_cfg.get("clip_epsilon", 0.2)
        self.entropy_coef  = ppo_cfg.get("entropy_coef", 0.01)
        self.update_epochs = ppo_cfg.get("update_epochs", 10)
        self.batch_size    = ppo_cfg.get("batch_size", 64)
        self.checkpoint    = ppo_cfg.get("checkpoint_path", "data/ppo_checkpoint.pt")

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.net    = ActorCritic(STATE_DIM, self.hidden_dim).to(self.device)
        self.optimizer = optim.Adam([
            {"params": self.net.actor_head.parameters(),  "lr": self.lr_actor},
            {"params": self.net.critic_head.parameters(), "lr": self.lr_critic},
            {"params": self.net.backbone.parameters(),    "lr": self.lr_actor}
        ])
        self.buffer   = RolloutBuffer(maxlen=4096)
        self._steps   = 0
        self._updates = 0
        self._load_checkpoint()

    def _load_checkpoint(self) -> None:
        if os.path.exists(self.checkpoint):
            try:
                ckpt = torch.load(self.checkpoint, map_location=self.device, weights_only=True)
                self.net.load_state_dict(ckpt["model"])
                self.optimizer.load_state_dict(ckpt["optimizer"])
                self._updates = ckpt.get("updates", 0)
                log.info(f"PPO checkpoint loaded ({self._updates} updates)")
            except Exception as e:
                log.warning(f"PPO checkpoint load failed: {e}")

    def _save_checkpoint(self) -> None:
        os.makedirs(os.path.dirname(self.checkpoint), exist_ok=True)
        torch.save({
            "model":     self.net.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "updates":   self._updates
        }, self.checkpoint)

    def encode_state(
        self,
        spread_mean: float,
        spread_std: float,
        gas_price_gwei: float,
        block_utilization: float,
        time_since_opp_ms: float,
        wallet_balance_eth: float,
        ukf_velocity: float,
        recent_success_rate: float
    ) -> np.ndarray:
        return np.array([
            np.clip(spread_mean / 0.02, 0, 1),
            np.clip(spread_std  / 0.01, 0, 1),
            np.clip(gas_price_gwei / 2.0, 0, 1),
            np.clip(block_utilization, 0, 1),
            np.clip(time_since_opp_ms / 500.0, 0, 1),
            np.clip(wallet_balance_eth / 10.0, 0, 1),
            np.tanh(ukf_velocity * 100),
            np.clip(recent_success_rate, 0, 1)
        ], dtype=np.float32)

    def select_action(self, state: np.ndarray) -> tuple[int, float, float]:
        return self.net.act(state)

    def store_transition(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        done: bool,
        log_prob: float,
        value: float
    ) -> None:
        self.buffer.add(Transition(state, action, reward, done, log_prob, value))
        self._steps += 1

    def update(self) -> Optional[dict]:
        if len(self.buffer) < self.batch_size:
            return None

        with torch.no_grad():
            last_state = torch.FloatTensor(
                self.buffer.buffer[-1].state
            ).unsqueeze(0).to(self.device)
            _, last_val = self.net.forward(last_state)
            last_value = float(last_val.item())

        states, actions, returns, old_log_probs, advantages = self.buffer.compute_gae(
            last_value, self.gamma, self.gae_lambda
        )

        states_t     = torch.FloatTensor(states).to(self.device)
        actions_t    = torch.LongTensor(actions).to(self.device)
        returns_t    = torch.FloatTensor(returns).to(self.device)
        old_lp_t     = torch.FloatTensor(old_log_probs).to(self.device)
        advantages_t = torch.FloatTensor(advantages).to(self.device)

        total_loss_p = total_loss_v = total_entropy = 0.0
        n_batches = 0

        for _ in range(self.update_epochs):
            idx = torch.randperm(len(states_t))
            for start in range(0, len(states_t), self.batch_size):
                b_idx = idx[start:start + self.batch_size]
                new_lp, values, entropy = self.net.evaluate(states_t[b_idx], actions_t[b_idx])
                ratio  = torch.exp(new_lp - old_lp_t[b_idx])
                surr1  = ratio * advantages_t[b_idx]
                surr2  = torch.clamp(ratio, 1 - self.clip_eps, 1 + self.clip_eps) * advantages_t[b_idx]
                loss_p = -torch.min(surr1, surr2).mean()
                loss_v = 0.5 * (values - returns_t[b_idx]).pow(2).mean()
                loss_e = -entropy.mean()
                loss   = loss_p + 0.5 * loss_v + self.entropy_coef * loss_e
                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.net.parameters(), 0.5)
                self.optimizer.step()
                total_loss_p  += loss_p.item()
                total_loss_v  += loss_v.item()
                total_entropy += (-loss_e).item()
                n_batches += 1

        self.buffer.clear()
        self._updates += 1
        if self._updates % 10 == 0:
            self._save_checkpoint()

        return {
            "policy_loss": total_loss_p / max(n_batches, 1),
            "value_loss":  total_loss_v / max(n_batches, 1),
            "entropy":     total_entropy / max(n_batches, 1),
            "updates":     self._updates
        }
