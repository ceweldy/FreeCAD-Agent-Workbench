"""Versioned Agent Workbench tool contracts, validation, and registry."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .. import SHORT_NAME


class SafetyLevel(StrEnum):
    READ = "read"
    VIEW = "view"
    SAFE_WRITE = "safe_write"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"
    DEVELOPER = "developer"


class ToolContractError(ValueError):
    pass


class ToolExecutionError(RuntimeError):
    pass


ToolHandler = Callable[["ToolContext", dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolContext:
    app: Any
    gui: Any
    project: Any
    memory: Any
    source_turn_id: str | None = None
    document: Any | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    safety: SafetyLevel
    handler: ToolHandler
    version: int = 1
    requires_document: bool = True
    mutates_document: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", self.name):
            raise ToolContractError("Tool names must use a namespace, such as part.create_box")
        if len(self.transport_name) > 64:
            raise ToolContractError(f"Tool {self.name} exceeds the provider name limit")
        if self.version < 1:
            raise ToolContractError(f"Tool {self.name} has an invalid version")
        if len(self.description.strip()) < 20:
            raise ToolContractError(f"Tool {self.name} needs a concrete description")
        if self.input_schema.get("type") != "object":
            raise ToolContractError(f"Tool {self.name} must accept a JSON object")
        if not isinstance(self.input_schema.get("properties", {}), dict):
            raise ToolContractError(f"Tool {self.name} properties must be an object")
        if self.mutates_document and (
            not self.requires_document
            or self.safety not in {SafetyLevel.SAFE_WRITE, SafetyLevel.DESTRUCTIVE}
        ):
            raise ToolContractError(
                f"Tool {self.name} has an invalid document-mutation contract"
            )

    def dynamic_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.transport_name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

    @property
    def transport_name(self) -> str:
        """Return the Responses API-safe wire name for a canonical tool name."""
        encoded = "".join(
            "_d" if character == "." else "_u" if character == "_" else character
            for character in self.name
        )
        return "agentcad_" + encoded


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ToolContractError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolContractError(f"Unknown {SHORT_NAME} tool: {name}") from exc

    def specs(self) -> list[ToolSpec]:
        return [self._tools[name] for name in sorted(self._tools)]

    def schema_revision(self, specs: list[ToolSpec] | None = None) -> str:
        selected = specs if specs is not None else self.specs()
        payload = [
            {
                "name": item.name,
                "transport_name": item.transport_name,
                "version": item.version,
                "safety": item.safety.value,
                "requires_document": item.requires_document,
                "mutates_document": item.mutates_document,
                "description": item.description,
                "schema": item.input_schema,
            }
            for item in selected
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def call(
        self, name: str, arguments: Mapping[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        spec = self.get(name)
        clean_arguments = dict(arguments)
        validate_schema(spec.input_schema, clean_arguments)
        try:
            result = spec.handler(context, clean_arguments)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(f"{name} failed: {exc}") from exc
        if not isinstance(result, dict):
            raise ToolExecutionError(f"{name} returned a non-object result")
        return {"tool": name, "tool_version": spec.version, **result}


def validate_schema(schema: Mapping[str, Any], value: Any, path: str = "arguments") -> None:
    """Validate the JSON Schema subset used by workbench tools."""
    if "enum" in schema and value not in schema["enum"]:
        raise ToolContractError(f"{path} must be one of {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None and not _matches_type(expected, value):
        raise ToolContractError(f"{path} must be {expected}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ToolContractError(f"{path} is missing required fields: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ToolContractError(f"{path} has unknown fields: {', '.join(unknown)}")
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, Mapping):
                validate_schema(child, item, f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ToolContractError(f"{path} needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ToolContractError(f"{path} allows at most {schema['maxItems']} items")
        child = schema.get("items")
        if isinstance(child, Mapping):
            for index, item in enumerate(value):
                validate_schema(child, item, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ToolContractError(f"{path} is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ToolContractError(f"{path} is too long")

    if isinstance(value, int | float) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolContractError(f"{path} must be a finite number")
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolContractError(f"{path} must be at least {schema['minimum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ToolContractError(f"{path} must be greater than {schema['exclusiveMinimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolContractError(f"{path} must be at most {schema['maximum']}")


def _matches_type(expected: str | list[str], value: Any) -> bool:
    expected_types = [expected] if isinstance(expected, str) else expected
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, int | float) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return any(checks.get(item, lambda _value: False)(value) for item in expected_types)


def success(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def failure(code: str, message: str, **payload: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message, **payload}
