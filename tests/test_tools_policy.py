from __future__ import annotations

import pytest

from freecad.AgentCAD.core.policy import ToolPolicy
from freecad.AgentCAD.core.tools import (
    SafetyLevel,
    ToolContext,
    ToolContractError,
    ToolExecutionError,
    ToolRegistry,
    ToolSpec,
    validate_schema,
)
from freecad.AgentCAD.tools import core_tools, memory_tools, part_tools

from .fakes import FakeDocument, FakeObject, FakeShape, fake_app


def _spec(
    name: str = "test.inspect",
    *,
    safety: SafetyLevel = SafetyLevel.READ,
    requires_document: bool = True,
    handler=lambda _context, arguments: {"ok": True, "value": arguments.get("value")},
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Inspect a deterministic test value without side effects.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer", "minimum": 1}},
            "required": ["value"],
            "additionalProperties": False,
        },
        safety=safety,
        handler=handler,
        requires_document=requires_document,
    )


@pytest.mark.parametrize(
    ("schema", "value", "message"),
    [
        ({"type": "integer"}, True, "must be integer"),
        ({"type": "string", "minLength": 3}, "x", "too short"),
        ({"type": "number", "exclusiveMinimum": 0}, 0, "greater than"),
        ({"type": "number"}, float("inf"), "finite number"),
        ({"type": "number"}, float("nan"), "finite number"),
        ({"type": "array", "minItems": 2}, [1], "at least 2"),
        ({"enum": ["left", "right"]}, "top", "must be one of"),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            {},
            "missing required fields",
        ),
        (
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            {"extra": 1},
            "unknown fields",
        ),
    ],
)
def test_validate_schema_rejects_invalid_values(
    schema: dict[str, object], value: object, message: str
) -> None:
    with pytest.raises(ToolContractError, match=message):
        validate_schema(schema, value)


def test_validate_schema_accepts_nested_supported_subset() -> None:
    schema = {
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {"x": {"type": "number", "maximum": 10}},
                    "required": ["x"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["points"],
        "additionalProperties": False,
    }
    validate_schema(schema, {"points": [{"x": 2.5}]})


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "invalid"}, "namespace"),
        ({"description": "short"}, "concrete description"),
        ({"version": 0}, "invalid version"),
        ({"input_schema": {"type": "array"}}, "JSON object"),
    ],
)
def test_tool_spec_rejects_invalid_contracts(kwargs: dict[str, object], message: str) -> None:
    defaults: dict[str, object] = {
        "name": "test.inspect",
        "description": "Inspect a deterministic test value without side effects.",
        "input_schema": {"type": "object", "properties": {}},
        "safety": SafetyLevel.READ,
        "handler": lambda _context, _arguments: {"ok": True},
    }
    defaults.update(kwargs)
    with pytest.raises(ToolContractError, match=message):
        ToolSpec(**defaults)


def test_registry_is_sorted_versioned_and_validates_before_call() -> None:
    registry = ToolRegistry()
    registry.register(_spec("zeta.inspect"))
    registry.register(_spec("alpha.inspect"))
    assert [item.name for item in registry.specs()] == ["alpha.inspect", "zeta.inspect"]
    assert len(registry.schema_revision()) == 64
    assert registry.schema_revision() == registry.schema_revision()
    context = ToolContext(app=None, gui=None, project=None, memory=None)
    assert registry.call("alpha.inspect", {"value": 2}, context) == {
        "tool": "alpha.inspect",
        "tool_version": 1,
        "ok": True,
        "value": 2,
    }
    with pytest.raises(ToolContractError, match="must be at least"):
        registry.call("alpha.inspect", {"value": 0}, context)
    with pytest.raises(ToolContractError, match="Tool already registered"):
        registry.register(_spec("alpha.inspect"))


def test_registry_wraps_handler_failures_and_rejects_non_object_result() -> None:
    context = ToolContext(app=None, gui=None, project=None, memory=None)
    failing = ToolRegistry()
    failing.register(_spec(handler=lambda _context, _arguments: 1 / 0))
    with pytest.raises(ToolExecutionError, match=r"test\.inspect failed"):
        failing.call("test.inspect", {"value": 1}, context)

    malformed = ToolRegistry()
    malformed.register(_spec(handler=lambda _context, _arguments: []))
    with pytest.raises(ToolExecutionError, match="non-object"):
        malformed.call("test.inspect", {"value": 1}, context)


def test_dynamic_tool_matches_app_server_shape() -> None:
    dynamic = _spec().dynamic_tool()
    assert dynamic == {
        "type": "function",
        "name": "agentcad_test_dinspect",
        "description": "Inspect a deterministic test value without side effects.",
        "inputSchema": _spec().input_schema,
    }
    assert _spec().transport_name == "agentcad_test_dinspect"


def test_transport_names_encode_dots_and_underscores_without_collisions() -> None:
    first = _spec("foo.bar__baz")
    second = _spec("foo__bar.baz")
    assert first.transport_name == "agentcad_foo_dbar_u_ubaz"
    assert second.transport_name == "agentcad_foo_u_ubar_dbaz"
    assert first.transport_name != second.transport_name


def test_policy_exposes_only_allowed_tools_for_current_document_state() -> None:
    specs = [
        _spec("test.read", safety=SafetyLevel.READ),
        _spec("test.view", safety=SafetyLevel.VIEW, requires_document=False),
        _spec("test.write", safety=SafetyLevel.SAFE_WRITE),
        _spec("test.delete", safety=SafetyLevel.DESTRUCTIVE),
        _spec("test.external", safety=SafetyLevel.EXTERNAL),
        _spec("test.developer", safety=SafetyLevel.DEVELOPER),
    ]
    policy = ToolPolicy()
    assert [item.name for item in policy.exposed(specs, has_document=True)] == [
        "test.read",
        "test.view",
        "test.write",
    ]
    assert [item.name for item in policy.exposed(specs, has_document=False)] == ["test.view"]
    assert policy.decide(specs[3]).reason == "destructive_requires_human_approval"
    assert not ToolPolicy(allow_safe_writes=False).decide(specs[2]).allowed


def test_builtin_registry_contract_and_policy_surface() -> None:
    registry = ToolRegistry()
    core_tools.register(registry)
    memory_tools.register(registry)
    part_tools.register(registry)
    names = [item.name for item in registry.specs()]
    assert names == [
        "core.get_document_state",
        "core.get_selection",
        "core.set_view",
        "memory.list",
        "memory.remember",
        "part.boolean",
        "part.create_box",
        "part.create_cylinder",
        "part.create_sphere",
        "part.measure",
    ]
    assert all(item.name.count(".") == 1 for item in registry.specs())
    assert all(
        item.dynamic_tool()["inputSchema"]["type"] == "object" for item in registry.specs()
    )
    assert "authority" not in registry.get("memory.remember").input_schema["properties"]
    assert set(names) == {
        item.name for item in ToolPolicy().exposed(registry.specs(), has_document=True)
    }
    without_document = {
        item.name for item in ToolPolicy().exposed(registry.specs(), has_document=False)
    }
    assert without_document == {"core.get_document_state", "core.get_selection"}


def test_boolean_recomputes_operands_before_reading_their_shapes() -> None:
    document = FakeDocument()
    stale_left = FakeShape(volume=2.0)
    current_left = FakeShape(volume=9.0)
    right = FakeShape(volume=3.0)
    left_object = FakeObject("Left", shape=stale_left)
    right_object = FakeObject("Right", shape=right)
    document.Objects = [left_object, right_object]
    document.recompute_hook = lambda: setattr(left_object, "Shape", current_left)
    context = ToolContext(
        app=fake_app(document),
        gui=None,
        project=None,
        memory=None,
        document=document,
    )

    result = part_tools._boolean(
        context,
        {
            "operation": "union",
            "left": "Left",
            "right": "Right",
            "label": "Joined",
        },
    )

    assert result["ok"] is True
    assert document.getObject("Joined").Shape.Volume == 10.0
    assert document.recomputes == 2


def test_boolean_rejects_failed_operand_recompute_before_opening_transaction() -> None:
    document = FakeDocument()
    document.Objects = [
        FakeObject("Left", shape=FakeShape()),
        FakeObject("Right", shape=FakeShape()),
    ]
    document.recompute_result = False
    context = ToolContext(
        app=fake_app(document),
        gui=None,
        project=None,
        memory=None,
        document=document,
    )

    with pytest.raises(RuntimeError, match="Boolean operands could not be recomputed"):
        part_tools._boolean(
            context,
            {
                "operation": "union",
                "left": "Left",
                "right": "Right",
                "label": "Joined",
            },
        )

    assert document.opened == []


def test_measure_recomputes_before_reading_shape_facts() -> None:
    document = FakeDocument()
    measured = FakeObject("Measured", shape=FakeShape(volume=2.0))
    document.Objects = [measured]
    document.recompute_hook = lambda: setattr(
        measured, "Shape", FakeShape(volume=12.5, area=30.0)
    )
    context = ToolContext(
        app=fake_app(document),
        gui=None,
        project=None,
        memory=None,
        document=document,
    )

    result = part_tools._measure(context, {"object": "Measured"})

    assert result["volume"] == 12.5
    assert result["area"] == 30.0
    assert document.recomputes == 1


def test_measure_rejects_failed_recompute() -> None:
    document = FakeDocument()
    document.Objects = [FakeObject("Measured", shape=FakeShape())]
    document.recompute_result = False
    context = ToolContext(
        app=fake_app(document),
        gui=None,
        project=None,
        memory=None,
        document=document,
    )

    with pytest.raises(RuntimeError, match="could not be recomputed before measurement"):
        part_tools._measure(context, {"object": "Measured"})
