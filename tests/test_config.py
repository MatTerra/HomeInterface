from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from homeinterface.config import DEFAULTS, load_config, resolve_path


def test_load_config_none_returns_defaults():
    config = load_config(None)
    assert config == DEFAULTS
    # top-level dict is a fresh copy (nested dicts are shared references -
    # see the note on load_config's shallow copy in the README/test report)
    assert config is not DEFAULTS


def test_load_config_missing_file_returns_defaults(tmp_path):
    missing = tmp_path / "nope.yaml"
    config = load_config(missing)
    assert config == DEFAULTS


def test_load_config_deep_merges_nested_display_keys(tmp_path):
    path = tmp_path / "app.yaml"
    path.write_text(
        yaml.safe_dump({"display": {"width": 3840, "fullscreen": True}}),
        encoding="utf-8",
    )
    config = load_config(path)
    # overridden keys take the new value
    assert config["display"]["width"] == 3840
    assert config["display"]["fullscreen"] is True
    # sibling keys are not wiped out by the partial override
    assert config["display"]["height"] == DEFAULTS["display"]["height"]
    assert config["display"]["fps"] == DEFAULTS["display"]["fps"]
    # top-level defaults not mentioned in the file survive untouched
    assert config["backend"] == DEFAULTS["backend"]


def test_load_config_sets_path_and_root(tmp_path):
    path = tmp_path / "app.yaml"
    path.write_text(yaml.safe_dump({}), encoding="utf-8")
    config = load_config(path)
    assert config["_path"] == str(path.resolve())


def test_load_config_non_mapping_root_raises(tmp_path):
    path = tmp_path / "app.yaml"
    path.write_text(yaml.safe_dump([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_resolve_path_absolute():
    config = {"_root": "/some/root"}
    abs_path = Path("/abs/file.yaml").resolve()
    assert resolve_path(config, str(abs_path)) == abs_path


def test_resolve_path_relative_without_root():
    config = {}
    result = resolve_path(config, "relative/file.yaml")
    assert result == Path("relative/file.yaml")


def test_resolve_path_relative_with_existing_root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "floorplan.yaml").write_text("floors: []", encoding="utf-8")
    config = {"_root": str(tmp_path)}
    result = resolve_path(config, "config/floorplan.yaml")
    assert result == tmp_path / "config" / "floorplan.yaml"


def test_resolve_path_none_value_returns_none():
    assert resolve_path({}, None) is None
    assert resolve_path({}, "") is None
