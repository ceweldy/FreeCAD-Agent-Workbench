from __future__ import annotations

import json
import multiprocessing
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from freecad.AgentCAD.core.memory import CATEGORIES, IntentMemory
from freecad.AgentCAD.core.project_store import (
    CONVERSATION_SCHEMA,
    PROJECT_SCHEMA,
    AgentCADProject,
    ProjectStore,
    project_id_for_path,
    slugify,
)


def _append_turns_in_process(
    data_root: str, document_path: str, prefix: str, count: int
) -> None:
    project = ProjectStore(data_root).for_document(document_path)
    for index in range(count):
        project.append_turn("user", f"{prefix} turn {index}")


def test_project_store_initializes_stable_document_scoped_data(tmp_path: Path) -> None:
    document = tmp_path / "Fixture Plate.FCStd"
    store = ProjectStore(tmp_path / "agent-data")
    first = store.for_document(document)
    second = store.for_document(document)
    manifest = first.manifest()
    assert first.root == second.root
    assert manifest["schema"] == PROJECT_SCHEMA
    assert manifest["project_id"] == project_id_for_path(document)
    assert manifest["document_path"] == str(document.resolve())
    assert first.active_conversation()["schema"] == CONVERSATION_SCHEMA
    assert first.conversations_dir.is_dir()
    assert first.runtime_dir.is_dir()
    assert slugify("Fixture Plate") == "fixture-plate"


@pytest.mark.parametrize("document_path", ["", "   "])
def test_project_store_rejects_empty_document_path(
    tmp_path: Path, document_path: str
) -> None:
    with pytest.raises(ValueError, match="saved document path"):
        ProjectStore(tmp_path / "data").for_document(document_path)


def test_project_store_rejects_directory_as_document_path(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-document"
    directory.mkdir()
    with pytest.raises(ValueError, match="not a directory"):
        ProjectStore(tmp_path / "data").for_document(directory)


def test_project_store_rejects_manifest_from_another_document(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    first = store.for_document(tmp_path / "first.FCStd")
    with pytest.raises(RuntimeError, match="belongs to another saved document"):
        AgentCADProject(first.root, (tmp_path / "second.FCStd").resolve())


def test_project_store_resolves_relative_data_root_once(
    tmp_path: Path, monkeypatch
) -> None:
    first_cwd = tmp_path / "first-cwd"
    second_cwd = tmp_path / "second-cwd"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.chdir(first_cwd)
    store = ProjectStore("agent-data")
    monkeypatch.chdir(second_cwd)
    project = store.for_document(tmp_path / "part.FCStd")
    assert store.data_root == (first_cwd / "agent-data").resolve()
    assert project.root.is_relative_to(store.data_root)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_project_store_enforces_owner_only_permissions(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    project = ProjectStore(data_root).for_document(tmp_path / "private.FCStd")
    memory = IntentMemory(project)
    memory.remember("constraint", "Keep this private", source_turn_id="turn-1")
    project.log_event("test", {"ok": True})

    directories = [
        data_root,
        project.root.parent,
        project.root,
        project.conversations_dir,
        project.runtime_dir,
    ]
    files = [
        project.root / ".agentcad.lock",
        project.manifest_path,
        project.memory_path,
        project.events_path,
        project.conversation_path(project.active_conversation()["conversation_id"]),
    ]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in directories)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)

    os.chmod(project.root, 0o755)
    os.chmod(project.manifest_path, 0o644)
    repaired = ProjectStore(data_root).for_document(tmp_path / "private.FCStd")
    assert stat.S_IMODE(repaired.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(repaired.manifest_path.stat().st_mode) == 0o600


def test_conversation_lifecycle_titles_catalog_and_thread_binding(tmp_path: Path) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    initial_id = project.active_conversation()["conversation_id"]
    user = project.append_turn("user", "Create a 20 mm mounting plate")
    token_key = "api" + "_token"
    password_key = "pass" + "word"
    project.append_turn(
        "assistant",
        "Created it",
        metadata={token_key: "fixture-a", "nested": {password_key: "fixture-b"}},
    )
    active = project.active_conversation()
    assert active["title"] == "Create a 20 mm mounting plate"
    assert active["turns"][0]["id"] == user["id"]
    assert active["turns"][1]["metadata"] == {
        token_key: "[redacted]",
        "nested": {password_key: "[redacted]"},
    }
    project.set_provider_thread("codex", "thread-1", "schema-1")
    assert project.provider_thread("codex") == {
        "thread_id": "thread-1",
        "schema_revision": "schema-1",
        "updated_at": project.provider_thread("codex")["updated_at"],
    }

    created = project.create_conversation("Alternative")
    assert created["conversation_id"] != initial_id
    assert project.active_conversation()["title"] == "Alternative"
    project.activate_conversation(initial_id)
    catalog = project.catalog()
    assert len(catalog) == 2
    assert sum(bool(item["active"]) for item in catalog) == 1
    assert project.active_conversation()["conversation_id"] == initial_id


def test_new_conversation_sorts_ahead_of_rapidly_created_initial_entry(
    tmp_path: Path,
) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    created = project.create_conversation("Immediate follow-up")

    catalog = project.catalog()

    assert catalog[0]["id"] == created["conversation_id"]
    assert catalog[0]["updated_at"] > catalog[1]["updated_at"]
    assert "." in catalog[0]["updated_at"]


def test_project_instances_share_lock_for_concurrent_conversation_writes(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path / "data")
    document = tmp_path / "part.FCStd"
    first = store.for_document(document)
    second = store.for_document(document)

    def append(index: int) -> None:
        project = first if index % 2 == 0 else second
        project.append_turn("user", f"concurrent turn {index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(40)))

    turns = first.active_conversation()["turns"]
    assert len(turns) == 40
    assert {turn["content"] for turn in turns} == {
        f"concurrent turn {index}" for index in range(40)
    }


def test_project_lock_preserves_writes_across_processes(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    document = tmp_path / "part.FCStd"
    project = ProjectStore(data_root).for_document(document)
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_append_turns_in_process,
            args=(str(data_root), str(document), prefix, 20),
        )
        for prefix in ("first", "second")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    turns = project.active_conversation()["turns"]
    assert len(turns) == 40
    assert {turn["content"] for turn in turns} == {
        f"{prefix} turn {index}"
        for prefix in ("first", "second")
        for index in range(20)
    }


@pytest.mark.parametrize("role", ["operator", "tool", ""])
def test_conversation_rejects_unknown_roles(tmp_path: Path, role: str) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    with pytest.raises(ValueError, match="Unsupported conversation role"):
        project.append_turn(role, "content")


def test_conversation_rejects_empty_content_and_unsafe_ids(tmp_path: Path) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    with pytest.raises(ValueError, match="cannot be empty"):
        project.append_turn("user", "   ")
    with pytest.raises(ValueError, match="Invalid AgentCAD conversation id"):
        project.conversation_path("../../outside")


def test_recent_turns_honors_limit_floor(tmp_path: Path) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    for index in range(4):
        project.append_turn("user", f"turn {index}")
    assert [item["content"] for item in project.recent_turns(2)] == ["turn 2", "turn 3"]
    assert [item["content"] for item in project.recent_turns(0)] == ["turn 3"]


def test_event_log_redacts_secret_like_fields(tmp_path: Path) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    token_key = "access" + "_token"
    secret_key = "client" + "_secret"
    header_key = "Author" + "ization"
    provider_key = "api" + "Key"
    project.log_event(
        "provider",
        {
            token_key: "fixture-a",
            "details": [
                {
                    secret_key: "fixture-b",
                    header_key: "fixture-c",
                    provider_key: "fixture-d",
                }
            ],
            "tuple_details": ({header_key: "fixture-e"},),
            "ok": True,
        },
    )
    event = json.loads(project.events_path.read_text(encoding="utf-8"))
    assert event["kind"] == "provider"
    assert event["data"] == {
        token_key: "[redacted]",
        "details": [
            {
                secret_key: "[redacted]",
                header_key: "[redacted]",
                provider_key: "[redacted]",
            }
        ],
        "tuple_details": [{header_key: "[redacted]"}],
        "ok": True,
    }


def test_memory_records_provenance_deduplicates_and_supersedes(tmp_path: Path) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    memory = IntentMemory(project)
    empty = memory.load()
    assert empty["entries"] == []
    assert len(empty["revision"]) == 64

    first = memory.remember(
        "constraint",
        "  Wall thickness must be 3 mm.  ",
        source_turn_id="turn-a",
        authority="user",
    )
    duplicate = memory.remember(
        "constraint",
        "wall thickness must be 3 mm.",
        source_turn_id="turn-b",
        authority="user",
    )
    replacement = memory.remember(
        "decision",
        "Wall thickness is now 4 mm.",
        source_turn_id="turn-c",
        authority="user",
    )
    assert duplicate["id"] == first["id"]
    assert duplicate["source_turn_ids"] == ["turn-a", "turn-b"]
    assert len(memory.active_entries()) == 2
    memory.supersede(first["id"], replacement["id"])
    assert [item["id"] for item in memory.active_entries()] == [replacement["id"]]
    old = next(item for item in memory.load()["entries"] if item["id"] == first["id"])
    assert old["status"] == "superseded"
    assert old["superseded_by"] == replacement["id"]


def test_memory_keeps_distinct_authorities_for_identical_statements(tmp_path: Path) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    memory = IntentMemory(project)
    agent_entry = memory.remember(
        "requirement",
        "The bracket needs two mounting holes",
        source_turn_id="turn-agent",
        authority="agent",
    )
    user_entry = memory.remember(
        "requirement",
        "The bracket needs two mounting holes",
        source_turn_id="turn-user",
        authority="user",
    )
    assert agent_entry["id"] != user_entry["id"]
    assert [
        (item["authority"], item["source_turn_ids"]) for item in memory.active_entries()
    ] == [
        ("agent", ["turn-agent"]),
        ("user", ["turn-user"]),
    ]


def test_memory_rejects_invalid_supersession_relationships(tmp_path: Path) -> None:
    project = ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd")
    memory = IntentMemory(project)
    first = memory.remember(
        "constraint",
        "Wall thickness is 3 mm",
        source_turn_id="turn-1",
    )
    second = memory.remember(
        "constraint",
        "Wall thickness is 4 mm",
        source_turn_id="turn-2",
    )
    third = memory.remember(
        "constraint",
        "Wall thickness is 5 mm",
        source_turn_id="turn-3",
    )
    with pytest.raises(ValueError, match="cannot supersede itself"):
        memory.supersede(first["id"], first["id"])
    memory.supersede(first["id"], second["id"])
    with pytest.raises(ValueError, match="must be active"):
        memory.supersede(third["id"], first["id"])
    with pytest.raises(ValueError, match="Only an active"):
        memory.supersede(first["id"], third["id"])


@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_memory_provider_context_supports_every_category(tmp_path: Path, category: str) -> None:
    project = ProjectStore(tmp_path / category).for_document(tmp_path / f"{category}.FCStd")
    memory = IntentMemory(project)
    entry = memory.remember(
        category,
        f"A durable {category} statement",
        source_turn_id="turn-1",
        authority="agent",
    )
    context = memory.provider_context()
    assert context["entries"] == [
        {
            "id": entry["id"],
            "category": category,
            "statement": f"A durable {category} statement",
            "authority": "agent",
        }
    ]
    assert context["revision"] == memory.load()["revision"]


@pytest.mark.parametrize(
    ("category", "statement", "authority", "message"),
    [
        ("progress", "Keep this", "user", "Memory category"),
        ("constraint", "", "user", "cannot be empty"),
        ("constraint", "x" * 1001, "user", "cannot exceed"),
        ("constraint", "Keep this", "unknown", "Memory authority"),
    ],
)
def test_memory_rejects_invalid_entries(
    tmp_path: Path,
    category: str,
    statement: str,
    authority: str,
    message: str,
) -> None:
    memory = IntentMemory(ProjectStore(tmp_path / "data").for_document(tmp_path / "part.FCStd"))
    with pytest.raises(ValueError, match=message):
        memory.remember(
            category,
            statement,
            source_turn_id="turn-1",
            authority=authority,
        )


def test_memory_detects_tampering_and_cross_project_payload(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    source = store.for_document(tmp_path / "source.FCStd")
    memory = IntentMemory(source)
    memory.remember("requirement", "Add a center bore", source_turn_id="turn-1")
    payload = source.read_memory_payload()
    assert payload is not None
    payload["entries"][0]["statement"] = "Tampered"
    source.write_memory_payload(payload)
    with pytest.raises(RuntimeError, match="revision does not match"):
        memory.load()

    target = store.for_document(tmp_path / "target.FCStd")
    target.write_memory_payload(IntentMemory(source)._empty())
    with pytest.raises(RuntimeError, match="belongs to another project"):
        IntentMemory(target).load()
