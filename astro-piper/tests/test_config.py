"""
tests/test_config.py — Unit tests for configuration loading and validation

Run with: python -m pytest tests/test_config.py -v
"""

import json
import tempfile
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import load_config, normalize_paths, ConfigError


# ── Fixtures ──────────────────────────────────────────────────────────────────


MINIMAL_VALID_CONFIG = {
    "target": {"name": "NGC1499"},
    "directories": {
        "raw_nb":  "D:/Astro/NB",
        "raw_rgb": "D:/Astro/RGB",
        "working": "E:/work",
        "output":  "E:/out",
    },
    "acquisition": {
        "nb": {
            "gain": 100, "offset": 50, "exposure": 300,
            "temperature": -20, "filters": ["Ha", "OIII", "SII"],
        },
        "rgb": {
            "gain": -25, "offset": 50, "exposure": 10,
            "temperature": -20, "filters": ["R", "G", "B"],
        },
    },
    "preprocessing": {
        "pedestal": 150,
        "drizzle_scale": 2,
        "rejection_algorithm": "ESD",
    },
    "processing": {
        "bxt_sharpen_stars": 0.25,
        "bxt_sharpen_nonstellar": 0.40,
        "graxpert_denoise_strength": 0.50,
        "stretch_target_median": 0.22,
    },
}


def _write_config(cfg: dict) -> Path:
    """Write cfg as JSON to a temp file, return the path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(cfg, f)
    f.close()
    return Path(f.name)


# ── load_config tests ─────────────────────────────────────────────────────────


def test_load_valid_config():
    path = _write_config(MINIMAL_VALID_CONFIG)
    cfg = load_config(path)
    assert cfg["target"]["name"] == "NGC1499"
    assert cfg["acquisition"]["nb"]["gain"] == 100
    path.unlink()


def test_load_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path/config.json")


def test_load_invalid_json():
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    f.write("{bad json}")
    f.close()
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(f.name)
    Path(f.name).unlink()


def test_missing_top_level_key():
    cfg = {k: v for k, v in MINIMAL_VALID_CONFIG.items() if k != "processing"}
    path = _write_config(cfg)
    with pytest.raises(ConfigError, match="processing"):
        load_config(path)
    path.unlink()


def test_missing_directory_key():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    del cfg["directories"]["working"]
    path = _write_config(cfg)
    with pytest.raises(ConfigError, match="working"):
        load_config(path)
    path.unlink()


def test_missing_acquisition_track():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    del cfg["acquisition"]["rgb"]
    path = _write_config(cfg)
    with pytest.raises(ConfigError, match="acquisition.rgb"):
        load_config(path)
    path.unlink()


def test_missing_acquisition_field():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    del cfg["acquisition"]["nb"]["gain"]
    path = _write_config(cfg)
    with pytest.raises(ConfigError, match="gain"):
        load_config(path)
    path.unlink()


def test_missing_preprocessing_key():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    del cfg["preprocessing"]["pedestal"]
    path = _write_config(cfg)
    with pytest.raises(ConfigError, match="pedestal"):
        load_config(path)
    path.unlink()


def test_missing_processing_key():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    del cfg["processing"]["bxt_sharpen_stars"]
    path = _write_config(cfg)
    with pytest.raises(ConfigError, match="bxt_sharpen_stars"):
        load_config(path)
    path.unlink()


# ── normalize_paths tests ─────────────────────────────────────────────────────


def test_normalize_paths_pjsr_forward_slashes():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    cfg["directories"]["working"] = "E:\\AstroPipeline\\NGC1499\\working"
    normalize_paths(cfg)
    assert "\\" not in cfg["_paths"]["pjsr"]["working"]
    assert "/" in cfg["_paths"]["pjsr"]["working"]


def test_normalize_paths_native_preserved():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    normalize_paths(cfg)
    assert "native" in cfg["_paths"]
    assert "pjsr" in cfg["_paths"]
    assert set(cfg["_paths"]["native"].keys()) == set(cfg["directories"].keys())


def test_normalize_paths_posix():
    import copy
    cfg = copy.deepcopy(MINIMAL_VALID_CONFIG)
    cfg["directories"]["working"] = "D:/Astro/work"
    normalize_paths(cfg)
    # Forward slashes should be preserved
    assert cfg["_paths"]["pjsr"]["working"] == "D:/Astro/work"
