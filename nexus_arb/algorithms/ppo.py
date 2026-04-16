"""
nexus_arb.algorithms.ppo
=========================

Proximal Policy Optimization (PPO) trading policy.

Usage in AUREON
---------------
  - Direct policy control: map market state → discrete action (BUY/SELL/HOLD)
    with continuous position-size scalar.
  - Online fine-tuning: collect a small rollout buffer after each N cycles,
    run PPO update, and immediately apply to next cycle.
  - Risk-aware reward: shaped reward = PnL - λ·drawdown² ensures the policy
    learns capital preservation alongside profit maximisation.

Theory
------
PPO maintains an actor π_θ(a|s) and a critic V_φ(s) implemented as
lightweight MLP networks (pure NumPy — no deep-learning framework).

The clipped surrogate objective prevents destructively large policy updates:

  L_CLIP(θ) = E[ min(r_t·A_t,  clip(r_t, 1-ε, 1+ε)·A_t) ]

where  r_t = π_θ(a_t|s_t) / π_θ_old(a_t|s_t)  and A_t is the GAE advantage.

Generalised Advantage Estimation (GAE, Schulman et al., 2015b):
  A_t = Σ_{l≥0} (γλ)^l · δ_{t+l},   δ_t = r_t + γ·V(s_{t+1}) − V(s_t)

Complexity per update: O(T·n_epochs·batch·(d_in·d_h + d_h²))
  where T = rollout length, d_in = state dim, d_h = hidden dim.

Formal Specification
---------------------
  State  s ∈ ℝ^6: [norm_price, log_return, volatility, position, drawdown, cash_ratio]
  Action a ∈ {0=HOLD, 1=BUY, 2=SELL}
  Reward r ∈ ℝ: step PnL minus drawdown penalty

  Postconditions:
    - select_action(s) returns (action: int, log_prob: float, value: float)
    - update(rollout) returns dict of loss metrics
    - action indices map deterministically: 0→HOLD, 1→BUY, 2→SELL
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ── Constants ──────────────────────────────────────────────────────────────────

ACTION_HOLD = 0
ACTION_BUY  = 1
ACTION_SELL = 2
ACTION_NAMES = {ACTION_HOLD: "HOLD", ACTION_BUY: "BUY", ACTION_SELL: "SELL"}
STATE_DIM   = 6   # norm_price, log_return, volatility, position, drawdown, cash_ratio
N_ACTIONS   = 3


# ── Tiny MLP helpers (pure NumPy) ──────────────────────────────────────────────

def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0.0, x)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


class _MLP:
    """
    Two-layer MLP: input → hidden (ReLU) → output.

    Weights use He initialisation (Kaiming uniform) for ReLU units.
    """

    def __init__(
        self,
        in_dim:  int,
        hidden:  int,
        out_dim: int,
        rng:     np.random.Generator,
    ) -> None:
        # He (Kaiming) uniform initialisation
        lim1 = math.sqrt(6.0 / in_dim)
        lim2 = math.sqrt(6.0 / hidden)

        self.W1 = rng.uniform(-lim1, lim1, (hidden, in_dim))
        self.b1 = np.zeros(hidden)
        self.W2 = rng.uniform(-lim2, lim2, (out_dim, hidden))
        self.b2 = np.zeros(out_dim)

    def forward(self, x: np.ndarray) -> np.ndarray:
        h = _relu(self.W1 @ x + self.b1)
        return self.W2 @ h + self.b2

    def params(self) -> List[np.ndarray]:
        return [self.W1, self.b1, self.W2, self.b2]

    def set_params(self, params: List[np.ndarray]) -> None:
        self.W1, self.b1, self.W2, self.b2 = (p.copy() for p in params)


# ── Rollout buffer ─────────────────────────────────────────────────────────────

@dataclass
class Transition:
    """Single environment step."""
    state:     np.ndarray
    action:    int
    reward:    float
    log_prob:  float
    value:     float
    done:      bool


@dataclass
class PPOResult:
    """Result of a PPO update step."""
    policy_loss:  float
    value_loss:   float
    entropy:      float
    n_updates:    int
    mean_return:  float
    mean_advantage: float


# ── TradingPolicy ─────────────────────────────────────────────────────────────

class TradingPolicy:
    """
    PPO-based trading policy for AUREON agents.

    Parameters
    ----------
    hidden_dim   : neurons per hidden layer (default 64)
    gamma        : discount factor for returns (default 0.99)
    gae_lambda   : GAE smoothing coefficient λ (default 0.95)
    clip_eps     : PPO clipping radius ε (default 0.2)
    lr           : SGD learning rate for both actor and critic (default 3e-4)
    n_epochs     : gradient update epochs per rollout (default 4)
    batch_size   : mini-batch size (default 32)
    vf_coef      : value function loss coefficient (default 0.5)
    ent_coef     : entropy bonus coefficient (default 0.01)
    max_grad_norm: gradient clip norm (default 0.5)
    seed         : reproducibility seed

    Example
    -------
    >>> policy = TradingPolicy(seed=42)
    >>> state = np.array([0.5, 0.001, 0.02, 0.0, 0.0, 1.0])
    >>> action, log_prob, value = policy.select_action(state)
    >>> action in (0, 1, 2)
    True
    >>> transitions = [Transition(state, action, 0.01, log_prob, value, False)]
    >>> result = policy.update(transitions, last_value=0.0)
    >>> isinstance(result.policy_loss, float)
    True
    """

    def __init__(
        self,
        hidden_dim:    int   = 64,
        gamma:         float = 0.99,
        gae_lambda:    float = 0.95,
        clip_eps:      float = 0.2,
        lr:            float = 3e-4,
        n_epochs:      int   = 4,
        batch_size:    int   = 32,
        vf_coef:       float = 0.5,
        ent_coef:      float = 0.01,
        max_grad_norm: float = 0.5,
        seed:          Optional[int] = None,
    ) -> None:
        self.gamma        = gamma
        self.gae_lambda   = gae_lambda
        self.clip_eps     = clip_eps
        self.lr           = lr
        self.n_epochs     = n_epochs
        self.batch_size   = batch_size
        self.vf_coef      = vf_coef
        self.ent_coef     = ent_coef
        self.max_grad_norm = max_grad_norm

        self._rng = np.random.default_rng(seed)

        # Actor: state → logits over N_ACTIONS
        self._actor  = _MLP(STATE_DIM, hidden_dim, N_ACTIONS, self._rng)
        # Critic: state → scalar value estimate
        self._critic = _MLP(STATE_DIM, hidden_dim, 1, self._rng)

        self._total_updates = 0

    # ── inference ─────────────────────────────────────────────────────────────

    def select_action(
        self, state: np.ndarray
    ) -> Tuple[int, float, float]:
        """
        Sample an action from the current policy.

        Parameters
        ----------
        state : 1-D array of shape (STATE_DIM,) — normalised market features.
                [norm_price, log_return, volatility, position, drawdown, cash_ratio]

        Returns
        -------
        (action, log_prob, value)
          action   : int in {0=HOLD, 1=BUY, 2=SELL}
          log_prob : log π_θ(action | state)
          value    : V_φ(state) — critic's estimate
        """
        state = np.asarray(state, dtype=np.float64)
        logits = self._actor.forward(state)
        probs  = _softmax(logits)
        action = int(self._rng.choice(N_ACTIONS, p=probs))
        log_prob = math.log(max(float(probs[action]), 1e-10))
        value = float(self._critic.forward(state)[0])
        return action, log_prob, value

    def action_name(self, action: int) -> str:
        """Return human-readable name for an action integer."""
        return ACTION_NAMES.get(action, "HOLD")

    def value(self, state: np.ndarray) -> float:
        """Return critic value estimate for a state."""
        return float(self._critic.forward(np.asarray(state, dtype=np.float64))[0])

    def policy_probs(self, state: np.ndarray) -> np.ndarray:
        """Return softmax action probabilities for inspection/logging."""
        logits = self._actor.forward(np.asarray(state, dtype=np.float64))
        return _softmax(logits)

    # ── update ────────────────────────────────────────────────────────────────

    def update(
        self,
        transitions: List[Transition],
        last_value:  float = 0.0,
    ) -> PPOResult:
        """
        Run PPO update over a collected rollout.

        Computes GAE advantages, then performs n_epochs × mini-batch SGD
        steps using the clipped surrogate objective and value loss.

        Parameters
        ----------
        transitions : list of Transition (in time order)
        last_value  : V(s_{T+1}) — bootstrap value for open episode

        Returns
        -------
        PPOResult with scalar loss metrics.
        """
        if not transitions:
            return PPOResult(0.0, 0.0, 0.0, 0, 0.0, 0.0)

        T = len(transitions)

        # ── Extract arrays ────────────────────────────────────────────────────
        states   = np.array([t.state    for t in transitions], dtype=np.float64)
        actions  = np.array([t.action   for t in transitions], dtype=np.int32)
        rewards  = np.array([t.reward   for t in transitions], dtype=np.float64)
        old_lps  = np.array([t.log_prob for t in transitions], dtype=np.float64)
        values   = np.array([t.value    for t in transitions], dtype=np.float64)
        dones    = np.array([t.done     for t in transitions], dtype=np.float64)

        # ── GAE advantage computation ─────────────────────────────────────────
        advantages = np.zeros(T, dtype=np.float64)
        returns    = np.zeros(T, dtype=np.float64)
        gae        = 0.0
        next_val   = last_value

        for t in reversed(range(T)):
            mask    = 1.0 - dones[t]
            delta   = rewards[t] + self.gamma * next_val * mask - values[t]
            gae     = delta + self.gamma * self.gae_lambda * mask * gae
            advantages[t] = gae
            next_val = values[t]

        returns = advantages + values

        # Normalise advantages
        adv_mean = advantages.mean()
        adv_std  = advantages.std() + 1e-8
        advantages = (advantages - adv_mean) / adv_std

        # ── PPO update epochs ─────────────────────────────────────────────────
        total_pl = total_vl = total_ent = 0.0
        n_updates = 0

        for _ in range(self.n_epochs):
            idx = self._rng.permutation(T)

            for start in range(0, T, self.batch_size):
                batch = idx[start : start + self.batch_size]

                b_states = states[batch]
                b_actions = actions[batch]
                b_old_lps = old_lps[batch]
                b_adv     = advantages[batch]
                b_returns = returns[batch]

                pl, vl, ent = self._ppo_step(
                    b_states, b_actions, b_old_lps, b_adv, b_returns
                )
                total_pl  += pl
                total_vl  += vl
                total_ent += ent
                n_updates += 1

        self._total_updates += n_updates

        n = max(n_updates, 1)
        return PPOResult(
            policy_loss    = total_pl / n,
            value_loss     = total_vl / n,
            entropy        = total_ent / n,
            n_updates      = n_updates,
            mean_return    = float(returns.mean()),
            mean_advantage = float(adv_mean),
        )

    # ── numerical gradient step ───────────────────────────────────────────────

    def _ppo_step(
        self,
        states:   np.ndarray,
        actions:  np.ndarray,
        old_lps:  np.ndarray,
        adv:      np.ndarray,
        returns:  np.ndarray,
    ) -> Tuple[float, float, float]:
        """
        Estimate PPO clipped policy loss + value loss via numerical gradient
        and apply a vanilla SGD step.  Pure NumPy — no autograd.

        Returns
        -------
        (policy_loss, value_loss, entropy)  — scalar floats for logging.
        """
        B = len(states)
        pl_total = vl_total = ent_total = 0.0

        # Collect parameter gradients via finite differences (2-point)
        eps_fd = 1e-5

        def _actor_loss(param_vec: np.ndarray) -> float:
            """Clipped surrogate actor loss for one mini-batch."""
            self._set_actor_params(param_vec)
            loss = 0.0
            for i in range(B):
                logits = self._actor.forward(states[i])
                probs  = _softmax(logits)
                lp     = math.log(max(float(probs[actions[i]]), 1e-10))
                ratio  = math.exp(lp - old_lps[i])
                cl     = min(ratio * adv[i],
                             np.clip(ratio, 1 - self.clip_eps, 1 + self.clip_eps) * adv[i])
                ent    = -float(np.sum(probs * np.log(probs + 1e-8)))
                loss  += -(cl + self.ent_coef * ent)
            return loss / B

        def _critic_loss(param_vec: np.ndarray) -> float:
            """MSE value loss for one mini-batch."""
            self._set_critic_params(param_vec)
            loss = 0.0
            for i in range(B):
                v_pred = float(self._critic.forward(states[i])[0])
                loss  += (v_pred - returns[i]) ** 2
            return loss / B * self.vf_coef

        # Compute metrics with current params
        a_params = self._get_actor_params()
        c_params = self._get_critic_params()
        pl = _actor_loss(a_params)
        vl = _critic_loss(c_params)

        # Finite-difference gradient for actor
        grad_a = np.zeros_like(a_params)
        for k in range(len(a_params)):
            p_plus  = a_params.copy(); p_plus[k]  += eps_fd
            p_minus = a_params.copy(); p_minus[k] -= eps_fd
            grad_a[k] = (_actor_loss(p_plus) - _actor_loss(p_minus)) / (2 * eps_fd)
        self._set_actor_params(a_params)   # restore

        # Clip gradient
        norm_a = np.linalg.norm(grad_a)
        if norm_a > self.max_grad_norm:
            grad_a = grad_a * self.max_grad_norm / norm_a

        # SGD step for actor
        self._set_actor_params(a_params - self.lr * grad_a)

        # Finite-difference gradient for critic
        grad_c = np.zeros_like(c_params)
        for k in range(len(c_params)):
            p_plus  = c_params.copy(); p_plus[k]  += eps_fd
            p_minus = c_params.copy(); p_minus[k] -= eps_fd
            grad_c[k] = (_critic_loss(p_plus) - _critic_loss(p_minus)) / (2 * eps_fd)
        self._set_critic_params(c_params)  # restore

        norm_c = np.linalg.norm(grad_c)
        if norm_c > self.max_grad_norm:
            grad_c = grad_c * self.max_grad_norm / norm_c

        # SGD step for critic
        self._set_critic_params(c_params - self.lr * grad_c)

        # Entropy for logging
        ent_sum = 0.0
        for i in range(B):
            logits = self._actor.forward(states[i])
            probs  = _softmax(logits)
            ent_sum += -float(np.sum(probs * np.log(probs + 1e-8)))

        pl_total  += pl
        vl_total  += vl
        ent_total += ent_sum / B

        return pl_total, vl_total, ent_total

    # ── param vector helpers ──────────────────────────────────────────────────

    def _get_actor_params(self) -> np.ndarray:
        return np.concatenate([p.ravel() for p in self._actor.params()])

    def _set_actor_params(self, vec: np.ndarray) -> None:
        offset = 0
        for p in self._actor.params():
            n = p.size
            p[:] = vec[offset:offset + n].reshape(p.shape)
            offset += n

    def _get_critic_params(self) -> np.ndarray:
        return np.concatenate([p.ravel() for p in self._critic.params()])

    def _set_critic_params(self, vec: np.ndarray) -> None:
        offset = 0
        for p in self._critic.params():
            n = p.size
            p[:] = vec[offset:offset + n].reshape(p.shape)
            offset += n

    # ── state encoding helpers ────────────────────────────────────────────────

    @staticmethod
    def encode_state(
        price:       float,
        prev_price:  float,
        volatility:  float,
        position:    float,
        drawdown:    float,
        cash_ratio:  float,
        price_scale: float = 3000.0,
    ) -> np.ndarray:
        """
        Encode raw market observations into a normalised state vector.

        Parameters
        ----------
        price       : current asset price (USD)
        prev_price  : price at previous step
        volatility  : rolling σ of returns (dimensionless)
        position    : current ETH held (0.0 = flat)
        drawdown    : current drawdown fraction (0–1)
        cash_ratio  : cash / total_equity (0–1)
        price_scale : normalisation divisor for price

        Returns
        -------
        np.ndarray of shape (STATE_DIM,) with all values roughly in [−1, 1].
        """
        norm_price  = price / price_scale - 1.0
        log_return  = max(-1.0, min(1.0, math.log(price / prev_price + 1e-9))) if prev_price > 0 else 0.0
        vol_norm    = min(volatility * 10.0, 2.0)     # scale 2 % vol → 0.2 after ×10
        pos_norm    = min(max(position / 10.0, -1.0), 1.0)
        dd_norm     = -min(drawdown, 1.0)             # negative reward signal
        cash_norm   = cash_ratio * 2.0 - 1.0          # [0,1] → [−1, 1]
        return np.array(
            [norm_price, log_return, vol_norm, pos_norm, dd_norm, cash_norm],
            dtype=np.float64,
        )

    # ── stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, object]:
        """Return diagnostic summary of policy state."""
        return {
            "total_updates": self._total_updates,
            "n_actions":     N_ACTIONS,
            "state_dim":     STATE_DIM,
            "clip_eps":      self.clip_eps,
            "lr":            self.lr,
            "gamma":         self.gamma,
            "gae_lambda":    self.gae_lambda,
        }
