"""Least-privilege provider tool policy."""

from __future__ import annotations

from dataclasses import dataclass

from .tools import SafetyLevel, ToolSpec


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


@dataclass
class ToolPolicy:
    allow_safe_writes: bool = True
    max_tool_calls_per_turn: int = 40

    def decide(self, spec: ToolSpec) -> PolicyDecision:
        if spec.safety in {SafetyLevel.READ, SafetyLevel.VIEW}:
            return PolicyDecision(True, "read_or_view")
        if spec.safety is SafetyLevel.SAFE_WRITE and self.allow_safe_writes:
            return PolicyDecision(True, "rollback_protected_write")
        if spec.safety is SafetyLevel.SAFE_WRITE:
            return PolicyDecision(False, "safe_writes_disabled")
        return PolicyDecision(False, f"{spec.safety.value}_requires_human_approval")

    def exposed(self, specs: list[ToolSpec], *, has_document: bool) -> list[ToolSpec]:
        return [
            spec
            for spec in specs
            if self.decide(spec).allowed and (has_document or not spec.requires_document)
        ]
