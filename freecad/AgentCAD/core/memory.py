"""Structured, provenance-preserving Agent Workbench intent memory."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from .. import SHORT_NAME
from .project_store import AgentCADProject, now_iso

MEMORY_SCHEMA = "agentcad-intent-memory-v1"
CATEGORIES = {"requirement", "constraint", "preference", "decision", "open_question"}
AUTHORITIES = {"user", "agent", "document"}


class IntentMemory:
    def __init__(self, project: AgentCADProject) -> None:
        self.project = project

    def load(self) -> dict[str, Any]:
        payload = self.project.read_memory_payload()
        if payload is None:
            return self._empty()
        self._validate(payload)
        return payload

    def active_entries(self) -> list[dict[str, Any]]:
        return [item for item in self.load()["entries"] if item["status"] == "active"]

    def remember(
        self,
        category: str,
        statement: str,
        *,
        source_turn_id: str,
        authority: str = "user",
    ) -> dict[str, Any]:
        clean_category = str(category).strip()
        clean_statement = re.sub(r"\s+", " ", str(statement)).strip()
        if clean_category not in CATEGORIES:
            raise ValueError(f"Memory category must be one of {sorted(CATEGORIES)}")
        if authority not in AUTHORITIES:
            raise ValueError(f"Memory authority must be one of {sorted(AUTHORITIES)}")
        if not clean_statement:
            raise ValueError("Memory statements cannot be empty")
        if len(clean_statement) > 1000:
            raise ValueError("Memory statements cannot exceed 1000 characters")

        with self.project.locked():
            memory = self.load()
            normalized = clean_statement.casefold()
            for item in memory["entries"]:
                if (
                    item["status"] == "active"
                    and item["category"] == clean_category
                    and item["statement"].casefold() == normalized
                    and item["authority"] == authority
                ):
                    if source_turn_id not in item["source_turn_ids"]:
                        item["source_turn_ids"].append(source_turn_id)
                        item["updated_at"] = now_iso()
                    self._save(memory)
                    return dict(item)

            timestamp = now_iso()
            item = {
                "id": uuid.uuid4().hex,
                "category": clean_category,
                "statement": clean_statement,
                "authority": authority,
                "source_turn_ids": [source_turn_id],
                "status": "active",
                "superseded_by": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            memory["entries"].append(item)
            self._save(memory)
            return dict(item)

    def supersede(self, entry_id: str, replacement_id: str) -> None:
        with self.project.locked():
            memory = self.load()
            by_id = {item["id"]: item for item in memory["entries"]}
            if entry_id not in by_id or replacement_id not in by_id:
                raise ValueError("Unknown memory entry")
            if entry_id == replacement_id:
                raise ValueError("A memory entry cannot supersede itself")
            if by_id[entry_id]["status"] != "active":
                raise ValueError("Only an active memory entry can be superseded")
            if by_id[replacement_id]["status"] != "active":
                raise ValueError("A memory replacement must be active")
            by_id[entry_id]["status"] = "superseded"
            by_id[entry_id]["superseded_by"] = replacement_id
            by_id[entry_id]["updated_at"] = now_iso()
            self._save(memory)

    def provider_context(self) -> dict[str, Any]:
        memory = self.load()
        return {
            "revision": memory["revision"],
            "entries": [
                {
                    "id": item["id"],
                    "category": item["category"],
                    "statement": item["statement"],
                    "authority": item["authority"],
                }
                for item in memory["entries"]
                if item["status"] == "active"
            ],
        }

    def _empty(self) -> dict[str, Any]:
        value = {
            "schema": MEMORY_SCHEMA,
            "version": 1,
            "project_id": self.project.manifest()["project_id"],
            "entries": [],
            "updated_at": None,
        }
        value["revision"] = _revision(value)
        return value

    def _save(self, memory: dict[str, Any]) -> None:
        memory["updated_at"] = now_iso()
        memory["revision"] = _revision(memory)
        self._validate(memory)
        self.project.write_memory_payload(memory)

    def _validate(self, memory: dict[str, Any]) -> None:
        if memory.get("schema") != MEMORY_SCHEMA or memory.get("version") != 1:
            raise RuntimeError(f"Unsupported {SHORT_NAME} memory schema")
        if memory.get("project_id") != self.project.manifest().get("project_id"):
            raise RuntimeError(f"{SHORT_NAME} memory belongs to another project")
        if not isinstance(memory.get("entries"), list):
            raise RuntimeError(f"{SHORT_NAME} memory entries must be a list")
        ids = set()
        for item in memory["entries"]:
            if not isinstance(item, dict) or not re.fullmatch(
                r"[a-f0-9]{32}", item.get("id", "")
            ):
                raise RuntimeError(f"{SHORT_NAME} memory contains an invalid entry")
            if item["id"] in ids:
                raise RuntimeError(f"{SHORT_NAME} memory contains duplicate ids")
            ids.add(item["id"])
            if (
                item.get("category") not in CATEGORIES
                or item.get("authority") not in AUTHORITIES
            ):
                raise RuntimeError(
                    f"{SHORT_NAME} memory contains an unsupported classification"
                )
            if item.get("status") not in {"active", "superseded"}:
                raise RuntimeError(
                    f"{SHORT_NAME} memory contains an unsupported status"
                )
        by_id = {item["id"]: item for item in memory["entries"]}
        for item in memory["entries"]:
            replacement_id = item.get("superseded_by")
            if item["status"] == "active" and replacement_id is not None:
                raise RuntimeError(
                    f"Active {SHORT_NAME} memory cannot name a replacement"
                )
            if item["status"] == "superseded" and (
                replacement_id not in by_id or replacement_id == item["id"]
            ):
                raise RuntimeError(
                    f"{SHORT_NAME} memory has an invalid supersession reference"
                )
        for entry_id in by_id:
            visited = set()
            current_id = entry_id
            while by_id[current_id]["status"] == "superseded":
                if current_id in visited:
                    raise RuntimeError(
                        f"{SHORT_NAME} memory contains a supersession cycle"
                    )
                visited.add(current_id)
                current_id = str(by_id[current_id]["superseded_by"])
        if memory.get("revision") != _revision(memory):
            raise RuntimeError(
                f"{SHORT_NAME} memory revision does not match its content"
            )


def _revision(memory: dict[str, Any]) -> str:
    canonical = {
        "schema": memory.get("schema"),
        "version": memory.get("version"),
        "project_id": memory.get("project_id"),
        "entries": memory.get("entries", []),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
