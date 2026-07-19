from __future__ import annotations

from types import SimpleNamespace

from freecad.AgentCAD.core.context import ContextCollector
from freecad.AgentCAD.core.transactions import FreeCADTransaction, Verification

from .fakes import FakeBoundBox, FakeDocument, FakeGui, FakeObject, FakeShape, fake_app


def _transaction(document: FakeDocument, name: str) -> FreeCADTransaction:
    return FreeCADTransaction(document, name, application=fake_app(document))


def test_transaction_commits_only_after_recompute_and_postcondition() -> None:
    document = FakeDocument()

    def action() -> dict[str, str]:
        document.Objects.append(FakeObject("Box", shape=FakeShape()))
        return {"object": "Box"}

    result = _transaction(document, "AgentCAD: create box").run(
        action,
        lambda payload: Verification(
            payload["object"] == "Box",
            "Shape is valid",
            {"solids": 1},
        ),
    )
    assert result["ok"] is True
    assert result["committed"] is True
    assert result["rolled_back"] is False
    assert result["document_delta"] == {"created": ["Box"], "deleted": [], "changed": []}
    assert result["verification"] == {
        "ok": True,
        "message": "Shape is valid",
        "details": {"solids": 1},
    }
    assert document.commits == 1
    assert document.aborts == 0


def test_transaction_aborts_action_exception_and_restores_snapshot() -> None:
    document = FakeDocument()

    def action() -> dict[str, str]:
        document.Objects.append(FakeObject("Broken", shape=FakeShape(valid=False)))
        raise ValueError("modeling failed")

    result = _transaction(document, "AgentCAD: broken action").run(
        action,
        lambda _payload: Verification(True),
    )
    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert result["error"] == "modeling failed"
    assert result["document_delta"] == {"created": [], "deleted": [], "changed": []}
    assert document.Objects == []
    assert document.aborts == 1


def test_transaction_closes_reservation_when_action_fails_before_first_change() -> None:
    document = FakeDocument()

    def action() -> dict[str, str]:
        raise ValueError("geometry kernel failed")

    result = _transaction(document, "AgentCAD: early failure").run(
        action,
        lambda _payload: Verification(True),
    )

    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert document.active_transaction_id == 0
    assert document.active_transaction_closes == [(True, 1)]


def test_transaction_rolls_back_failed_postcondition() -> None:
    document = FakeDocument()

    def action() -> dict[str, str]:
        document.Objects.append(FakeObject("Invalid", shape=FakeShape(valid=False)))
        return {"object": "Invalid"}

    result = _transaction(document, "AgentCAD: verify").run(
        action,
        lambda _payload: Verification(False, "Created shape is invalid"),
    )
    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert result["error"] == "Created shape is invalid"
    assert document.Objects == []


def test_transaction_rolls_back_recompute_failure() -> None:
    document = FakeDocument()
    document.recompute_result = False

    def action() -> dict[str, str]:
        document.Objects.append(FakeObject("Recompute", shape=FakeShape()))
        return {"object": "Recompute"}

    result = _transaction(document, "AgentCAD: recompute").run(
        action,
        lambda _payload: Verification(True),
    )
    assert result["ok"] is False
    assert result["rolled_back"] is False
    assert "recompute reported failure" in result["error"]


def test_transaction_refuses_to_commit_an_existing_pending_transaction() -> None:
    document = FakeDocument()
    document.pending_transaction = True
    action_called = False

    def action() -> dict[str, str]:
        nonlocal action_called
        action_called = True
        return {"object": "none"}

    result = _transaction(document, "AgentCAD: guarded").run(
        action,
        lambda _payload: Verification(True),
    )
    assert result["ok"] is False
    assert result["rolled_back"] is False
    assert "pending transaction" in result["error"]
    assert action_called is False
    assert document.opened == []
    assert document.commits == 0
    assert document.aborts == 0


def test_transaction_rejects_undo_disabled_document_before_action() -> None:
    document = FakeDocument()
    document.UndoMode = 0
    action_called = False

    def action() -> dict[str, str]:
        nonlocal action_called
        action_called = True
        document.Objects.append(FakeObject("Unsafe", shape=FakeShape()))
        return {"object": "Unsafe"}

    result = _transaction(document, "AgentCAD: guarded").run(
        action,
        lambda _payload: Verification(True),
    )

    assert result["ok"] is False
    assert "undo mode must be enabled" in result["error"]
    assert action_called is False
    assert document.Objects == []
    assert document.opened == []


def test_transaction_refuses_to_replace_an_existing_active_transaction() -> None:
    document = FakeDocument()
    document.active_transaction_id = 42
    document.active_transaction_name = "User edit"
    action_called = False

    def action() -> dict[str, str]:
        nonlocal action_called
        action_called = True
        return {"object": "none"}

    result = _transaction(document, "AgentCAD: guarded").run(
        action,
        lambda _payload: Verification(True),
    )

    assert result["ok"] is False
    assert result["rolled_back"] is False
    assert "reserved transaction" in result["error"]
    assert action_called is False
    assert document.opened == []


def test_transaction_does_not_run_action_when_freecad_refuses_open() -> None:
    document = FakeDocument()
    document.openTransaction = lambda _name: None  # type: ignore[method-assign]
    action_called = False

    def action() -> dict[str, str]:
        nonlocal action_called
        action_called = True
        return {"object": "none"}

    result = _transaction(document, "AgentCAD: refused").run(
        action,
        lambda _payload: Verification(True),
    )
    assert result["ok"] is False
    assert result["rolled_back"] is False
    assert "refused to open" in result["error"]
    assert action_called is False


def test_transaction_verifies_reservation_after_open() -> None:
    document = FakeDocument()

    def open_without_reservation(name: str) -> None:
        document.opened.append(name)

    document.openTransaction = open_without_reservation  # type: ignore[method-assign]
    action_called = False

    def action() -> dict[str, str]:
        nonlocal action_called
        action_called = True
        return {"object": "none"}

    result = _transaction(document, "AgentCAD: no pending state").run(
        action,
        lambda _payload: Verification(True),
    )
    assert result["ok"] is False
    assert "refused to open" in result["error"]
    assert action_called is False


def test_context_collects_safe_shape_selection_and_workbench_facts() -> None:
    document = FakeDocument("/tmp/fixture.FCStd")
    document.Objects = [
        FakeObject(
            "Plate",
            label="Fixture Plate",
            type_id="Part::Box",
            shape=FakeShape(
                bounds=FakeBoundBox(XMin=-1, YMin=-2, ZMin=0, XMax=9, YMax=8, ZMax=3)
            ),
        ),
        FakeObject("Spreadsheet", type_id="Spreadsheet::Sheet", shape=None),
    ]
    gui = FakeGui([SimpleNamespace(ObjectName="Plate", SubElementNames=["Face1"])])
    state = ContextCollector(fake_app(document), gui).collect()
    assert state["document"]["file_name"] == "fixture.FCStd"
    assert state["document"]["object_count"] == 2
    assert state["document"]["objects"][0] == {
        "name": "Plate",
        "label": "Fixture Plate",
        "type": "Part::Box",
        "shape": {
            "valid": True,
            "solids": 1,
            "faces": 6,
            "edges": 12,
            "volume": 6.0,
            "bounds": {
                "XMin": -1.0,
                "YMin": -2.0,
                "ZMin": 0.0,
                "XMax": 9.0,
                "YMax": 8.0,
                "ZMax": 3.0,
            },
        },
    }
    assert state["selection"] == ["Plate"]
    assert state["workbench"] == "Part"
    assert len(state["revision"]) == 64


def test_context_is_deterministic_and_caps_provider_object_count() -> None:
    document = FakeDocument()
    document.Objects = [FakeObject(f"Object{index}", shape=FakeShape()) for index in range(160)]
    collector = ContextCollector(fake_app(document), FakeGui())
    first = collector.collect()
    second = collector.collect()
    assert first["revision"] == second["revision"]
    assert first["document"]["object_count"] == 160
    assert first["document"]["returned_object_count"] == 150
    assert first["document"]["objects_truncated"] is True
    assert first["document"]["objects"][-1]["name"] == "Object149"


def test_context_degrades_safely_without_document_or_gui() -> None:
    state = ContextCollector(fake_app(None), None).collect()
    assert state["document"] == {
        "name": None,
        "label": None,
        "file_name": None,
        "object_count": 0,
        "returned_object_count": 0,
        "objects_truncated": False,
        "objects": [],
    }
    assert state["selection"] == []
    assert state["workbench"] is None
