"""Provider discovery without coupling the UI to implementations."""

from __future__ import annotations

from .base import AgentProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AgentProvider] = {}

    def register(self, provider: AgentProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"Provider already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> AgentProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ValueError(f"Unknown AgentCAD provider: {provider_id}") from exc

    def providers(self) -> list[AgentProvider]:
        return [self._providers[key] for key in sorted(self._providers)]

    def close(self) -> None:
        for provider in self._providers.values():
            provider.close()
