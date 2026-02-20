#!/usr/bin/env python3
"""Track estimated LLM spend and enforce budget caps.

Persists call records to a JSON file.  Enforces two limits:
  - Rolling 24-hour spend cap
  - Lifetime total spend cap

Raises CostLimitExceeded when either cap would be breached.
"""

import fcntl
import json
import os
import time

# Estimated cost per 1M tokens by (source, model_prefix).
# Falls back to a conservative default for unknown models.
PRICING = {
    # Google Gemini (per 1M tokens)
    ("google", "gemini-2.5-flash"):  {"input": 0.15, "output": 0.60},
    ("google", "gemini-2.5-pro"):    {"input": 1.25, "output": 10.00},
    ("google", "gemini-2.0-flash"):  {"input": 0.10, "output": 0.40},
    # Ollama / local — free
    ("ollama", ""):                   {"input": 0.0,  "output": 0.0},
    # OpenAI (conservative estimates)
    ("openai", "gpt-4o"):            {"input": 2.50, "output": 10.00},
    ("openai", "gpt-4o-mini"):       {"input": 0.15, "output": 0.60},
}

# Rough chars-per-token ratio for estimating token counts from char counts.
CHARS_PER_TOKEN = 4

DEFAULT_DAILY_CAP = 2.00   # USD, rolling 24h
DEFAULT_TOTAL_CAP = 10.00  # USD, lifetime


class CostLimitExceeded(Exception):
    """Raised when a spend cap would be exceeded."""


class CostTracker:
    """File-backed LLM cost tracker with daily and total caps."""

    def __init__(self, path, daily_cap=None, total_cap=None, logger=None):
        self.path = path
        self.daily_cap = daily_cap if daily_cap is not None else DEFAULT_DAILY_CAP
        self.total_cap = total_cap if total_cap is not None else DEFAULT_TOTAL_CAP
        self.logger = logger
        self.records = []
        self._load()

    # ── Persistence ──

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    self.records = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                if self.logger:
                    self.logger.warning(
                        f"Cost ledger corrupted or unreadable ({e}), starting fresh"
                    )
                self.records = []
        else:
            self.records = []

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(self.records, f)
        os.replace(tmp, self.path)

    # ── Pricing lookup ──

    @staticmethod
    def _lookup_pricing(source, model):
        """Find the best matching pricing entry."""
        if source == "ollama":
            return {"input": 0.0, "output": 0.0}
        # Try exact (source, model) then prefix matches
        key = (source, model)
        if key in PRICING:
            return PRICING[key]
        for (s, prefix), price in PRICING.items():
            if s == source and model.startswith(prefix):
                return price
        # Conservative fallback: assume expensive
        return {"input": 5.00, "output": 15.00}

    @staticmethod
    def estimate_cost(source, model, prompt_chars, response_chars):
        """Estimate USD cost for a single call."""
        pricing = CostTracker._lookup_pricing(source, model)
        input_tokens = prompt_chars / CHARS_PER_TOKEN
        output_tokens = response_chars / CHARS_PER_TOKEN
        cost = (input_tokens * pricing["input"] / 1_000_000
                + output_tokens * pricing["output"] / 1_000_000)
        return cost

    # ── Budget queries ──

    def spend_last_24h(self):
        cutoff = time.time() - 86400
        return sum(r["cost"] for r in self.records if r["ts"] >= cutoff)

    def spend_total(self):
        return sum(r["cost"] for r in self.records)

    # ── Enforcement ──

    def check_budget(self, estimated_cost=0.0):
        """Raise CostLimitExceeded if adding estimated_cost would breach a cap."""
        daily = self.spend_last_24h()
        total = self.spend_total()
        if daily + estimated_cost > self.daily_cap:
            raise CostLimitExceeded(
                f"24h spend ${daily:.4f} + ${estimated_cost:.4f} "
                f"would exceed daily cap ${self.daily_cap:.2f}"
            )
        if total + estimated_cost > self.total_cap:
            raise CostLimitExceeded(
                f"Total spend ${total:.4f} + ${estimated_cost:.4f} "
                f"would exceed lifetime cap ${self.total_cap:.2f}"
            )

    def record(self, source, model, prompt_chars, response_chars, duration):
        """Record a completed call and save to disk."""
        cost = self.estimate_cost(source, model, prompt_chars, response_chars)
        entry = {
            "ts": time.time(),
            "source": source,
            "model": model,
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
            "duration": round(duration, 2),
            "cost": round(cost, 6),
        }
        self.records.append(entry)
        self._save()
        if self.logger:
            daily = self.spend_last_24h()
            total = self.spend_total()
            self.logger.info(
                f"Cost | call=${cost:.6f} | 24h=${daily:.4f}/{self.daily_cap:.2f} | "
                f"total=${total:.4f}/{self.total_cap:.2f}"
            )
        return cost

    def summary(self):
        """Return a dict with current spend status."""
        return {
            "daily_spend": round(self.spend_last_24h(), 4),
            "daily_cap": self.daily_cap,
            "total_spend": round(self.spend_total(), 4),
            "total_cap": self.total_cap,
            "call_count": len(self.records),
        }
