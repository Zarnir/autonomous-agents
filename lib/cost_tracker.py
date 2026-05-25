"""Token usage parsing and USD cost computation for agent runners.

Each runner emits token usage differently. This module provides:
- A best-effort `parse_usage(output, runner)` that recognizes common formats.
- A pricing table for current Claude models.
- `compute_cost(usage, model)` that returns USD.

If usage cannot be parsed, returns None — never crashes. The orchestrator
should treat None as "untracked" and proceed (degrades gracefully).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# Pricing in USD per million tokens. Approximate as of Claude 4.x.
# Override per-project via .opencode/config.json `pricing` field.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "_default": (3.0, 15.0),
}


def compute_cost(usage: Usage, model: Optional[str], pricing: Optional[dict] = None) -> float:
    table = pricing or DEFAULT_PRICING
    key = model if model and model in table else "_default"
    in_price, out_price = table.get(key, table["_default"])
    return (usage.input_tokens / 1_000_000.0) * in_price + (usage.output_tokens / 1_000_000.0) * out_price


_CLAUDE_JSON_RE = re.compile(r'\{[^{}]*"usage"\s*:\s*\{[^{}]*\}[^{}]*\}', re.DOTALL)
_TOKENS_LINE_RE = re.compile(
    r'(?:input\s+tokens?|tokens?\s+in|prompt\s+tokens?)\s*[:=]\s*(\d[\d,]*)',
    re.IGNORECASE,
)
_OUTPUT_TOKENS_LINE_RE = re.compile(
    r'(?:output\s+tokens?|tokens?\s+out|completion\s+tokens?)\s*[:=]\s*(\d[\d,]*)',
    re.IGNORECASE,
)


def _parse_claude_json(text: str) -> Optional[Usage]:
    candidates: list[str] = []
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        candidates.append(text)
    for m in _CLAUDE_JSON_RE.finditer(text):
        candidates.append(m.group(0))
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            continue
        try:
            return Usage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            )
        except (TypeError, ValueError):
            continue
    return None


def _parse_generic_tokens(text: str) -> Optional[Usage]:
    in_match = _TOKENS_LINE_RE.search(text)
    out_match = _OUTPUT_TOKENS_LINE_RE.search(text)
    if not in_match or not out_match:
        return None
    try:
        return Usage(
            input_tokens=int(in_match.group(1).replace(",", "")),
            output_tokens=int(out_match.group(1).replace(",", "")),
        )
    except ValueError:  # pragma: no cover — regex captures only \d and commas
        return None


def parse_usage(output: str, runner: str) -> Optional[Usage]:
    if not output:
        return None
    if runner == "claude":
        usage = _parse_claude_json(output)
        if usage:
            return usage
    return _parse_generic_tokens(output)


def accumulate(state: dict, agent: str, usage: Usage, cost_usd: float) -> None:
    state.setdefault("total_input_tokens", 0)
    state.setdefault("total_output_tokens", 0)
    state.setdefault("total_usd", 0.0)
    state.setdefault("by_agent", {})
    state.setdefault("calls", 0)
    state["total_input_tokens"] += usage.input_tokens
    state["total_output_tokens"] += usage.output_tokens
    state["total_usd"] += cost_usd
    state["by_agent"][agent] = state["by_agent"].get(agent, 0.0) + cost_usd
    state["calls"] += 1


def format_summary(state: dict) -> str:
    if not state or state.get("calls", 0) == 0:
        return "no agent calls tracked"
    return (
        f"${state.get('total_usd', 0):.4f} "
        f"({state.get('total_input_tokens', 0):,} in / "
        f"{state.get('total_output_tokens', 0):,} out tokens, "
        f"{state.get('calls', 0)} calls)"
    )
