from __future__ import annotations

from types import SimpleNamespace

import pytest

from freecad.AgentCAD.init_gui import _require_supported_freecad


def test_supported_freecad_version_is_accepted() -> None:
    _require_supported_freecad(SimpleNamespace(Version=lambda: ["1", "1", "1", "build"]))


@pytest.mark.parametrize(
    "app",
    [
        SimpleNamespace(Version=lambda: ["1", "0", "2"]),
        SimpleNamespace(Version=lambda: ["invalid"]),
        SimpleNamespace(),
    ],
)
def test_unsupported_or_unknown_freecad_version_is_rejected(app: object) -> None:
    with pytest.raises(RuntimeError, match="FreeCAD"):
        _require_supported_freecad(app)
