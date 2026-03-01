"""
tests/test_preprocessing.py -- Sprint 3: Unit tests for preprocessing stages

Tests cover:
  - Frame discovery helpers (_find_frames, _find_calibration_master,
    _match_drizzle_pairs)
  - generate_subframe_selector() PJSR output content
  - SubframeInspectionStage, NBCalibrationStage, NBDrizzleStage,
    RGBCalibrationStage, RGBToNBRegistrationStage execute() calls
  - Idempotency (skips when output files already exist)
  - Error propagation (PipelineError on missing frames/outputs)

Execution: pytest tests/test_preprocessing.py -v
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from stages.preprocessing import (
    _find_frames,
    _find_calibration_master,
    _match_drizzle_pairs,
    _glob_dir,
    SubframeInspectionStage,
    NBCalibrationStage,
    NBDrizzleStage,
    RGBCalibrationStage,
    RGBToNBRegistrationStage,
)
from orchestrator import PipelineError
from pjsr_generator import generate_subframe_selector


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def minimal_config(tmp_path):
    """Minimal pipeline config with all required directory keys."""
    raw_nb  = tmp_path / "raw_nb"
    raw_rgb = tmp_path / "raw_rgb"
    cal_nb  = tmp_path / "calibration_nb"
    cal_rgb = tmp_path / "calibration_rgb"
    working = tmp_path / "working"

    for d in (raw_nb, raw_rgb, cal_nb, cal_rgb, working):
        d.mkdir(parents=True, exist_ok=True)

    return {
        "target": {"name": "NGC1499"},
        "directories": {
            "raw_nb":         str(raw_nb),
            "raw_rgb":        str(raw_rgb),
            "calibration_nb": str(cal_nb),
            "calibration_rgb":str(cal_rgb),
            "working":        str(working),
            "output":         str(tmp_path / "output"),
        },
        "tools": {
            "pixinsight_exe": str(tmp_path / "PixInsight.exe"),
            "graxpert_exe":   str(tmp_path / "GraXpert.exe"),
        },
        "acquisition": {
            "nb":  {"filters": ["Ha", "OIII", "SII"], "gain": 100, "exposure": 300},
            "rgb": {"filters": ["R", "G", "B"],       "gain": -25, "exposure": 10},
        },
        "preprocessing": {
            "pedestal":             150,
            "drizzle_scale":        2,
            "drizzle_drop_shrink":  0.9,
            "drizzle_kernel":       "Square",
            "rejection_algorithm":  "ESD",
            "esd_significance":     0.05,
            "esd_outliers":         0.30,
            "esd_low_relaxation":   2.0,
            "local_normalization":  True,
        },
    }


def _make_fake_frames(directory: Path, names: list[str]) -> list[Path]:
    """Create empty placeholder files and return their paths."""
    paths = []
    for name in names:
        p = directory / name
        p.touch()
        paths.append(p)
    return paths


# =============================================================================
# _find_frames
# =============================================================================


class TestFindFrames:
    def test_subdirectory_layout(self, tmp_path):
        """Finds frames in <raw_dir>/<Filter>/ subdirectory."""
        ha_dir = tmp_path / "Ha"
        ha_dir.mkdir()
        frames = _make_fake_frames(ha_dir, ["Light_Ha_001.fit", "Light_Ha_002.fit"])
        found = _find_frames(tmp_path, "Ha")
        assert sorted(found) == sorted(frames)

    def test_flat_layout_case_insensitive(self, tmp_path):
        """Finds frames in flat layout when filter name is in filename (case-insensitive)."""
        frames = _make_fake_frames(tmp_path, ["Light_OIII_001.fit", "Light_OIII_002.fits"])
        _make_fake_frames(tmp_path, ["Light_Ha_001.fit"])  # different filter, should not match
        found = _find_frames(tmp_path, "OIII")
        assert len(found) == 2
        assert all("OIII" in f.name for f in found)

    def test_subdirectory_takes_priority_over_flat(self, tmp_path):
        """Subdirectory layout is preferred when both exist."""
        sub = tmp_path / "Ha"
        sub.mkdir()
        sub_frames = _make_fake_frames(sub, ["subdir_Ha_001.fit"])
        _make_fake_frames(tmp_path, ["flat_Ha_002.fit"])  # flat layout also present
        found = _find_frames(tmp_path, "Ha")
        assert found == sub_frames  # only subdirectory frames

    def test_empty_returns_empty_list(self, tmp_path):
        """Returns empty list when no frames exist."""
        found = _find_frames(tmp_path, "SII")
        assert found == []

    def test_xisf_extension_supported(self, tmp_path):
        """Finds .xisf files in subdirectory layout."""
        ha_dir = tmp_path / "Ha"
        ha_dir.mkdir()
        frames = _make_fake_frames(ha_dir, ["frame_001.xisf", "frame_002.XISF"])
        found = _find_frames(tmp_path, "Ha")
        assert len(found) == 2

    def test_returns_sorted_list(self, tmp_path):
        """Output is sorted."""
        sub = tmp_path / "Ha"
        sub.mkdir()
        _make_fake_frames(sub, ["c.fit", "a.fit", "b.fit"])
        found = _find_frames(tmp_path, "Ha")
        assert found == sorted(found)


# =============================================================================
# _find_calibration_master
# =============================================================================


class TestFindCalibrationMaster:
    def test_finds_first_matching_pattern(self, tmp_path):
        dark = tmp_path / "master_dark_300s.xisf"
        dark.touch()
        result = _find_calibration_master(tmp_path, "master_dark*.xisf")
        assert result == dark

    def test_falls_through_to_second_pattern(self, tmp_path):
        dark = tmp_path / "master_dark.fit"
        dark.touch()
        result = _find_calibration_master(
            tmp_path, "master_dark*.xisf", "master_dark*.fit"
        )
        assert result == dark

    def test_returns_none_when_not_found(self, tmp_path):
        result = _find_calibration_master(tmp_path, "master_dark*.xisf")
        assert result is None

    def test_per_filter_flat_priority(self, tmp_path):
        generic  = tmp_path / "master_flat.xisf"
        specific = tmp_path / "master_flat_Ha.xisf"
        generic.touch()
        specific.touch()
        result = _find_calibration_master(
            tmp_path,
            "master_flat_Ha.xisf",  # per-filter specific
            "master_flat*.xisf",    # generic fallback
        )
        assert result == specific


# =============================================================================
# _match_drizzle_pairs
# =============================================================================


class TestMatchDrizzlePairs:
    def test_matches_by_stem(self, tmp_path):
        f1 = tmp_path / "frame_001_c_r.xisf"
        d1 = tmp_path / "frame_001_c_r.xdrz"
        f2 = tmp_path / "frame_002_c_r.xisf"
        d2 = tmp_path / "frame_002_c_r.xdrz"
        for p in (f1, d1, f2, d2):
            p.touch()
        pairs = _match_drizzle_pairs([f1, f2], [d1, d2])
        assert pairs == [(f1, d1), (f2, d2)]

    def test_raises_on_missing_sidecar(self, tmp_path):
        frame = tmp_path / "frame_001_c_r.xisf"
        frame.touch()
        with pytest.raises(PipelineError, match="No .xdrz sidecar"):
            _match_drizzle_pairs([frame], [])

    def test_partial_mismatch_raises(self, tmp_path):
        f1 = tmp_path / "frame_001_c_r.xisf"
        f2 = tmp_path / "frame_002_c_r.xisf"
        d1 = tmp_path / "frame_001_c_r.xdrz"
        for p in (f1, f2, d1):
            p.touch()
        with pytest.raises(PipelineError):
            _match_drizzle_pairs([f1, f2], [d1])


# =============================================================================
# generate_subframe_selector
# =============================================================================


class TestGenerateSubframeSelector:
    def test_includes_frame_paths(self, tmp_path):
        csv = str(tmp_path / "weights.csv")
        paths = ["/data/Ha/Light_001.fit", "/data/Ha/Light_002.fit"]
        script = generate_subframe_selector(frame_paths=paths, output_csv=csv)
        assert "Light_001.fit" in script
        assert "Light_002.fit" in script

    def test_includes_csv_output_path(self, tmp_path):
        csv = str(tmp_path / "output.csv")
        script = generate_subframe_selector(frame_paths=["/a.fit"], output_csv=csv)
        # Forward-slash path must appear in script
        assert "output.csv" in script

    def test_contains_pjsr_subframe_selector(self):
        script = generate_subframe_selector(
            frame_paths=["/data/frame.fit"], output_csv="/out.csv"
        )
        assert "SubframeSelector" in script
        assert "executeGlobal" in script

    def test_csv_write_logic_present(self):
        script = generate_subframe_selector(
            frame_paths=["/data/frame.fit"], output_csv="/out.csv"
        )
        assert "File" in script
        assert "outTextLn" in script
        assert "P.measures" in script

    def test_includes_header(self, tmp_path):
        script = generate_subframe_selector(
            frame_paths=["/f.fit"], output_csv=str(tmp_path / "w.csv")
        )
        assert "#include <pjsr/DataType.jsh>" in script

    def test_custom_approval_expression(self, tmp_path):
        expr = "FWHM < 3.0"
        script = generate_subframe_selector(
            frame_paths=["/f.fit"],
            output_csv=str(tmp_path / "w.csv"),
            approval_expression=expr,
        )
        assert expr in script

    def test_forward_slash_paths(self, tmp_path):
        """Windows backslash paths must be converted to forward slashes."""
        csv = str(tmp_path / "w.csv").replace("/", "\\")
        script = generate_subframe_selector(
            frame_paths=["C:\\data\\frame.fit"], output_csv=csv
        )
        assert "\\" not in script


# =============================================================================
# SubframeInspectionStage
# =============================================================================


class TestSubframeInspectionStage:
    def _make_stage(self):
        return SubframeInspectionStage(
            name="Subframe Inspection and Rejection",
            phase=1, track="nb",
            output_spec=[],
        )

    def test_calls_pjsr_inline(self, minimal_config, tmp_path):
        """execute() discovers frames and calls run_pjsr_inline once."""
        raw_nb = Path(minimal_config["directories"]["raw_nb"])
        ha_dir = raw_nb / "Ha"
        ha_dir.mkdir()
        _make_fake_frames(ha_dir, ["Light_001.fit"])
        oiii_dir = raw_nb / "OIII"
        oiii_dir.mkdir()
        _make_fake_frames(oiii_dir, ["Light_001.fit"])
        sii_dir = raw_nb / "SII"
        sii_dir.mkdir()
        _make_fake_frames(sii_dir, ["Light_001.fit"])

        stage = self._make_stage()
        with patch("stages.preprocessing.run_pjsr_inline", return_value=0) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        assert mock_pi.call_count == 1
        script_arg = mock_pi.call_args[0][0]
        assert "SubframeSelector" in script_arg

    def test_raises_on_missing_frames(self, minimal_config):
        """PipelineError when no frames found for a channel."""
        stage = self._make_stage()
        with pytest.raises(PipelineError, match="No light frames"):
            stage.execute(minimal_config)

    def test_passes_pi_exe_from_config(self, minimal_config, tmp_path):
        """pi_exe from config is forwarded to run_pjsr_inline."""
        raw_nb = Path(minimal_config["directories"]["raw_nb"])
        for ch in ["Ha", "OIII", "SII"]:
            d = raw_nb / ch
            d.mkdir(exist_ok=True)
            _make_fake_frames(d, ["Light_001.fit"])

        stage = self._make_stage()
        pi_exe_val = minimal_config["tools"]["pixinsight_exe"]
        with patch("stages.preprocessing.run_pjsr_inline", return_value=0) as mock_pi:
            stage.execute(minimal_config)

        kwargs = mock_pi.call_args[1]
        assert kwargs.get("pi_exe") == pi_exe_val or mock_pi.call_args[0][1] == pi_exe_val

    def test_failure_propagates_pipeline_error(self, minimal_config):
        """Non-zero pi exit code raises PipelineError."""
        raw_nb = Path(minimal_config["directories"]["raw_nb"])
        for ch in ["Ha", "OIII", "SII"]:
            d = raw_nb / ch
            d.mkdir(exist_ok=True)
            _make_fake_frames(d, ["Light_001.fit"])

        stage = self._make_stage()
        with patch("stages.preprocessing.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError, match="SubframeSelector"):
                stage.execute(minimal_config)


# =============================================================================
# NBCalibrationStage
# =============================================================================


class TestNBCalibrationStage:
    def _make_stage(self, working):
        return NBCalibrationStage(
            name="NB Calibration Registration Integration",
            phase=1, track="nb",
            output_spec=[str(Path(working) / f"NGC1499_{ch}_master.xisf")
                         for ch in ["Ha", "OIII", "SII"]],
        )

    def _populate_raw(self, config, channels=None):
        """Create minimal fake raw frame directories."""
        raw_nb = Path(config["directories"]["raw_nb"])
        for ch in (channels or ["Ha", "OIII", "SII"]):
            d = raw_nb / ch
            d.mkdir(exist_ok=True)
            _make_fake_frames(d, [f"Light_{ch}_001.fit", f"Light_{ch}_002.fit"])

    @staticmethod
    def _detect_channel(script: str, channels: list) -> str | None:
        """Detect which channel a PJSR script is for by scanning for channel paths."""
        for ch in channels:
            if f"/{ch}/" in script or f"NGC1499_{ch}" in script:
                return ch
        return None

    def _mock_pi_and_outputs(self, config, channels=None):
        """
        Patch run_pjsr_inline to return 0 and create expected intermediate/final
        outputs so stage logic doesn't fail on "file not found after PI run".

        Creates outputs only for the channel the current script is processing,
        so idempotency logic works correctly across calls.
        """
        channels = channels or ["Ha", "OIII", "SII"]
        working = Path(config["directories"]["working"])

        def side_effect(script, pi_exe=None, timeout=None):
            ch = self._detect_channel(script, channels)
            if ch is None:
                return 0

            # Check LocalNormalization before StarAlignment: the LN script mentions
            # "StarAlignment" in a comment, so order matters.
            if "ImageCalibration" in script:
                cal_dir = working / "calibrated" / ch
                cal_dir.mkdir(parents=True, exist_ok=True)
                (cal_dir / f"Light_{ch}_001_c.xisf").touch()
            elif "LocalNormalization" in script:
                norm_dir = working / "normalized" / ch
                norm_dir.mkdir(parents=True, exist_ok=True)
                (norm_dir / f"Light_{ch}_001_c_r_n.xisf").touch()
            elif "StarAlignment" in script:
                reg_dir = working / "registered" / ch
                reg_dir.mkdir(parents=True, exist_ok=True)
                (reg_dir / f"Light_{ch}_001_c_r.xisf").touch()
                (reg_dir / f"Light_{ch}_001_c_r.xdrz").touch()
            elif "ImageIntegration" in script:
                (working / f"NGC1499_{ch}_master.xisf").touch()

            return 0

        return patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect)

    def test_runs_four_pjsr_scripts_per_channel(self, minimal_config, tmp_path):
        """
        For each channel: ImageCalibration + StarAlignment + LocalNormalization
        + ImageIntegration = 4 calls. With 3 channels = 12 total.
        """
        self._populate_raw(minimal_config)
        stage = self._make_stage(minimal_config["directories"]["working"])

        with self._mock_pi_and_outputs(minimal_config) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        # 4 scripts per channel × 3 channels = 12
        assert mock_pi.call_count == 12

    def test_skips_channel_if_master_exists(self, minimal_config):
        """Channel is skipped when its master already exists (idempotency)."""
        self._populate_raw(minimal_config)
        working = Path(minimal_config["directories"]["working"])

        # Pre-create Ha master -- Ha should be skipped
        (working / "NGC1499_Ha_master.xisf").touch()

        stage = self._make_stage(minimal_config["directories"]["working"])
        with self._mock_pi_and_outputs(minimal_config, channels=["OIII", "SII"]) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        # Only OIII + SII ran: 2 channels × 4 scripts = 8
        assert mock_pi.call_count == 8

    def test_skips_all_channels_when_all_masters_exist(self, minimal_config):
        """Zero PJSR calls when all master files already exist."""
        self._populate_raw(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        for ch in ["Ha", "OIII", "SII"]:
            (working / f"NGC1499_{ch}_master.xisf").touch()

        stage = self._make_stage(minimal_config["directories"]["working"])
        with patch("stages.preprocessing.run_pjsr_inline", return_value=0) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        assert mock_pi.call_count == 0

    def test_raises_on_missing_raw_frames(self, minimal_config):
        """PipelineError when raw frames don't exist for a channel."""
        # Don't populate raw frames
        stage = self._make_stage(minimal_config["directories"]["working"])
        with pytest.raises(PipelineError, match="No light frames"):
            stage.execute(minimal_config)

    def test_raises_when_calibration_produces_no_output(self, minimal_config):
        """PipelineError when ImageCalibration leaves no files in output dir."""
        self._populate_raw(minimal_config)
        stage = self._make_stage(minimal_config["directories"]["working"])

        # PI returns 0 but produces no files
        with patch("stages.preprocessing.run_pjsr_inline", return_value=0):
            with pytest.raises(PipelineError, match="produced no output"):
                stage.execute(minimal_config)

    def test_scripts_contain_correct_pjsr_classes(self, minimal_config):
        """Generated PJSR scripts include the expected PI process class names."""
        self._populate_raw(minimal_config)
        stage = self._make_stage(minimal_config["directories"]["working"])
        scripts_seen: list[str] = []
        channels = ["Ha", "OIII", "SII"]

        def capture_script(script, pi_exe=None, timeout=None):
            scripts_seen.append(script)
            ch = self._detect_channel(script, channels)
            if ch:
                working = Path(minimal_config["directories"]["working"])
                # Check LocalNormalization before StarAlignment: the LN script mentions
                # "StarAlignment" in a comment, so order matters.
                if "ImageCalibration" in script:
                    sub = working / "calibrated" / ch
                    sub.mkdir(parents=True, exist_ok=True)
                    (sub / f"frame_{ch}_001.xisf").touch()
                elif "LocalNormalization" in script:
                    sub = working / "normalized" / ch
                    sub.mkdir(parents=True, exist_ok=True)
                    (sub / f"frame_{ch}_001.xisf").touch()
                elif "StarAlignment" in script:
                    sub = working / "registered" / ch
                    sub.mkdir(parents=True, exist_ok=True)
                    (sub / f"frame_{ch}_001.xisf").touch()
                elif "ImageIntegration" in script:
                    (working / f"NGC1499_{ch}_master.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=capture_script):
            stage.execute(minimal_config)

        expected_classes = [
            "ImageCalibration",
            "StarAlignment",
            "LocalNormalization",
            "ImageIntegration",
        ]
        for cls in expected_classes:
            assert any(cls in s for s in scripts_seen), f"{cls} not found in any script"

    def test_no_dark_calibration_when_no_dark_found(self, minimal_config):
        """If no master dark in calibration_nb, dark is disabled (not an error)."""
        self._populate_raw(minimal_config)
        # No master_dark* files in calibration_nb -- that's OK

        stage = self._make_stage(minimal_config["directories"]["working"])
        captured: list[str] = []
        channels = ["Ha", "OIII", "SII"]

        def capture(script, pi_exe=None, timeout=None):
            captured.append(script)
            ch = self._detect_channel(script, channels)
            if ch:
                working = Path(minimal_config["directories"]["working"])
                for d in ["calibrated", "registered", "normalized"]:
                    p = working / d / ch
                    p.mkdir(parents=True, exist_ok=True)
                    (p / f"frame_{ch}.xisf").touch()
                if "ImageIntegration" in script:
                    (working / f"NGC1499_{ch}_master.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=capture):
            result = stage.execute(minimal_config)

        assert result == 0
        # First script per channel should be ImageCalibration with dark disabled
        cal_scripts = [s for s in captured if "ImageCalibration" in s]
        assert len(cal_scripts) == 3
        for s in cal_scripts:
            assert "masterDarkEnabled" in s
            assert "false" in s


# =============================================================================
# NBDrizzleStage
# =============================================================================


class TestNBDrizzleStage:
    def _make_stage(self, working):
        return NBDrizzleStage(
            name="NB DrizzleIntegration",
            phase=1, track="nb",
            input_spec=[str(Path(working) / f"NGC1499_{ch}_master.xisf")
                        for ch in ["Ha", "OIII", "SII"]],
            output_spec=[str(Path(working) / f"NGC1499_{ch}_drizzle.xisf")
                         for ch in ["Ha", "OIII", "SII"]],
        )

    def _make_registered_dirs(self, config):
        """Create fake registered frames + .xdrz files for all NB channels."""
        working = Path(config["directories"]["working"])
        for ch in ["Ha", "OIII", "SII"]:
            reg_dir = working / "registered" / ch
            reg_dir.mkdir(parents=True, exist_ok=True)
            for i in range(3):
                (reg_dir / f"frame_{ch}_{i:03d}_c_r.xisf").touch()
                (reg_dir / f"frame_{ch}_{i:03d}_c_r.xdrz").touch()

    @staticmethod
    def _detect_drizzle_channel(script: str) -> str | None:
        """Detect which channel a DrizzleIntegration script targets."""
        for ch in ["Ha", "OIII", "SII"]:
            if f"NGC1499_{ch}_drizzle" in script:
                return ch
        return None

    def test_calls_drizzle_per_channel(self, minimal_config):
        """One DrizzleIntegration PJSR call per NB channel."""
        self._make_registered_dirs(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        stage = self._make_stage(minimal_config["directories"]["working"])

        def side_effect(script, pi_exe=None, timeout=None):
            ch = self._detect_drizzle_channel(script)
            if ch:
                (working / f"NGC1499_{ch}_drizzle.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        assert mock_pi.call_count == 3

    def test_script_contains_drizzle_integration(self, minimal_config):
        """Generated scripts contain DrizzleIntegration."""
        self._make_registered_dirs(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        stage = self._make_stage(minimal_config["directories"]["working"])
        captured: list[str] = []

        def side_effect(script, pi_exe=None, timeout=None):
            captured.append(script)
            ch = self._detect_drizzle_channel(script)
            if ch:
                (working / f"NGC1499_{ch}_drizzle.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect):
            stage.execute(minimal_config)

        assert all("DrizzleIntegration" in s for s in captured)

    def test_skips_existing_drizzle_outputs(self, minimal_config):
        """Channels with existing drizzle output files are skipped."""
        self._make_registered_dirs(minimal_config)
        working = Path(minimal_config["directories"]["working"])

        # Pre-create Ha drizzle output
        (working / "NGC1499_Ha_drizzle.xisf").touch()

        stage = self._make_stage(minimal_config["directories"]["working"])

        def side_effect(script, pi_exe=None, timeout=None):
            ch = self._detect_drizzle_channel(script)
            if ch:
                (working / f"NGC1499_{ch}_drizzle.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        assert mock_pi.call_count == 2  # only OIII + SII

    def test_raises_when_no_registered_dir(self, minimal_config):
        """PipelineError when registered directory doesn't exist."""
        stage = self._make_stage(minimal_config["directories"]["working"])
        with pytest.raises(PipelineError, match="Registered frame directory not found"):
            stage.execute(minimal_config)

    def test_raises_when_no_drizzle_sidecars(self, minimal_config):
        """PipelineError when .xdrz files are missing."""
        working = Path(minimal_config["directories"]["working"])
        reg_dir = working / "registered" / "Ha"
        reg_dir.mkdir(parents=True, exist_ok=True)
        (reg_dir / "frame_001_c_r.xisf").touch()
        # No .xdrz files

        stage = self._make_stage(minimal_config["directories"]["working"])
        with pytest.raises(PipelineError, match="No .xdrz sidecar"):
            stage.execute(minimal_config)

    def test_raises_when_output_not_created(self, minimal_config):
        """PipelineError when PI returns 0 but drizzle file doesn't appear."""
        self._make_registered_dirs(minimal_config)
        stage = self._make_stage(minimal_config["directories"]["working"])

        with patch("stages.preprocessing.run_pjsr_inline", return_value=0):
            with pytest.raises(PipelineError, match="output not found"):
                stage.execute(minimal_config)

    def test_drizzle_config_params_in_script(self, minimal_config):
        """Scale, drop_shrink, kernel from config appear in DrizzleIntegration script."""
        self._make_registered_dirs(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        stage = self._make_stage(minimal_config["directories"]["working"])
        captured: list[str] = []

        def side_effect(script, pi_exe=None, timeout=None):
            captured.append(script)
            for ch in ["Ha", "OIII", "SII"]:
                (working / f"NGC1499_{ch}_drizzle.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect):
            stage.execute(minimal_config)

        # Scale 2, drop 0.9, Square kernel
        for s in captured:
            assert "2.0" in s or "2" in s   # drizzle scale
            assert "0.900" in s              # drop shrink
            assert "Square" in s


# =============================================================================
# RGBCalibrationStage
# =============================================================================


class TestRGBCalibrationStage:
    def _make_stage(self, working):
        return RGBCalibrationStage(
            name="RGB Calibration Registration Integration",
            phase=1, track="rgb",
            output_spec=[str(Path(working) / f"NGC1499_{ch}_master.xisf")
                         for ch in ["R", "G", "B"]],
        )

    def _populate_rgb_raw(self, config):
        raw_rgb = Path(config["directories"]["raw_rgb"])
        for ch in ["R", "G", "B"]:
            d = raw_rgb / ch
            d.mkdir(exist_ok=True)
            _make_fake_frames(d, [f"Light_{ch}_001.fit", f"Light_{ch}_002.fit"])

    @staticmethod
    def _detect_rgb_channel(script: str) -> str | None:
        # Normalize to forward slashes for cross-platform path matching
        s = script.replace("\\", "/")
        for ch in ["R", "G", "B"]:
            if f"/{ch}/" in s or f"NGC1499_{ch}" in s:
                return ch
        return None

    def test_runs_pipeline_for_rgb_channels(self, minimal_config):
        """Three channels processed; 4 PJSR scripts each = 12 total."""
        self._populate_rgb_raw(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        stage = self._make_stage(minimal_config["directories"]["working"])

        def side_effect(script, pi_exe=None, timeout=None):
            ch = self._detect_rgb_channel(script)
            if ch:
                # Check LocalNormalization before StarAlignment: the LN script mentions
                # "StarAlignment" in a comment, so order matters.
                if "ImageCalibration" in script:
                    sub = working / "calibrated" / ch
                    sub.mkdir(parents=True, exist_ok=True)
                    (sub / f"frame_{ch}.xisf").touch()
                elif "LocalNormalization" in script:
                    sub = working / "normalized" / ch
                    sub.mkdir(parents=True, exist_ok=True)
                    (sub / f"frame_{ch}.xisf").touch()
                elif "StarAlignment" in script:
                    sub = working / "registered" / ch
                    sub.mkdir(parents=True, exist_ok=True)
                    (sub / f"frame_{ch}.xisf").touch()
                elif "ImageIntegration" in script:
                    (working / f"NGC1499_{ch}_master.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        assert mock_pi.call_count == 12

    def test_uses_winsorized_sigma_clip_by_default(self, minimal_config):
        """RGB integration defaults to WinsorizedSigmaClip (appropriate for short subs)."""
        self._populate_rgb_raw(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        stage = self._make_stage(minimal_config["directories"]["working"])
        captured: list[str] = []

        def side_effect(script, pi_exe=None, timeout=None):
            captured.append(script)
            ch = self._detect_rgb_channel(script)
            if ch:
                for d in ["calibrated", "registered", "normalized"]:
                    sub = working / d / ch
                    sub.mkdir(parents=True, exist_ok=True)
                    (sub / f"frame_{ch}.xisf").touch()
                if "ImageIntegration" in script:
                    (working / f"NGC1499_{ch}_master.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect):
            stage.execute(minimal_config)

        int_scripts = [s for s in captured if "ImageIntegration" in s]
        assert len(int_scripts) == 3
        for s in int_scripts:
            assert "WinsorizedSigmaClip" in s

    def test_raises_on_missing_rgb_frames(self, minimal_config):
        stage = self._make_stage(minimal_config["directories"]["working"])
        with pytest.raises(PipelineError, match="No light frames"):
            stage.execute(minimal_config)


# =============================================================================
# RGBToNBRegistrationStage
# =============================================================================


class TestRGBToNBRegistrationStage:
    def _make_stage(self, working):
        return RGBToNBRegistrationStage(
            name="RGB to NB Frame Registration",
            phase=1, track="rgb",
            input_spec=[str(Path(working) / f"NGC1499_{ch}_master.xisf")
                        for ch in ["R", "G", "B"]],
            output_spec=[str(Path(working) / f"NGC1499_{ch}_master_registered.xisf")
                         for ch in ["R", "G", "B"]],
        )

    def _make_masters(self, config):
        """Create fake master files for Ha + RGB channels."""
        working = Path(config["directories"]["working"])
        for ch in ["Ha", "R", "G", "B"]:
            (working / f"NGC1499_{ch}_master.xisf").touch()

    def test_runs_star_alignment_to_ha(self, minimal_config):
        """Single StarAlignment call using Ha master as reference."""
        self._make_masters(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        stage = self._make_stage(minimal_config["directories"]["working"])
        captured: list[str] = []

        def side_effect(script, pi_exe=None, timeout=None):
            captured.append(script)
            # Simulate StarAlignment output (stem + _r.xisf)
            reg_dir = working / "rgb_registered"
            reg_dir.mkdir(parents=True, exist_ok=True)
            for ch in ["R", "G", "B"]:
                (reg_dir / f"NGC1499_{ch}_master_r.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0
        assert mock_pi.call_count == 1
        assert "StarAlignment" in captured[0]
        assert "NGC1499_Ha_master" in captured[0]

    def test_registered_files_renamed_correctly(self, minimal_config):
        """StarAlignment _r.xisf outputs are renamed to _master_registered.xisf."""
        self._make_masters(minimal_config)
        working = Path(minimal_config["directories"]["working"])
        stage = self._make_stage(minimal_config["directories"]["working"])

        def side_effect(script, pi_exe=None, timeout=None):
            reg_dir = working / "rgb_registered"
            reg_dir.mkdir(parents=True, exist_ok=True)
            for ch in ["R", "G", "B"]:
                (reg_dir / f"NGC1499_{ch}_master_r.xisf").touch()
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect):
            stage.execute(minimal_config)

        for ch in ["R", "G", "B"]:
            final = working / f"NGC1499_{ch}_master_registered.xisf"
            assert final.exists(), f"Expected {final.name} after registration"

    def test_raises_when_ha_master_missing(self, minimal_config):
        """PipelineError when Ha master doesn't exist (NB not yet processed)."""
        # Only RGB masters -- no Ha
        working = Path(minimal_config["directories"]["working"])
        for ch in ["R", "G", "B"]:
            (working / f"NGC1499_{ch}_master.xisf").touch()

        stage = self._make_stage(minimal_config["directories"]["working"])
        with pytest.raises(PipelineError, match="Ha master not found"):
            stage.execute(minimal_config)

    def test_raises_when_rgb_master_missing(self, minimal_config):
        """PipelineError when an RGB master doesn't exist."""
        working = Path(minimal_config["directories"]["working"])
        (working / "NGC1499_Ha_master.xisf").touch()
        # Only R and G, no B
        (working / "NGC1499_R_master.xisf").touch()
        (working / "NGC1499_G_master.xisf").touch()

        stage = self._make_stage(minimal_config["directories"]["working"])
        with pytest.raises(PipelineError, match="RGB master not found"):
            stage.execute(minimal_config)

    def test_skips_already_registered_files(self, minimal_config):
        """Pre-existing _master_registered.xisf files are left untouched."""
        self._make_masters(minimal_config)
        working = Path(minimal_config["directories"]["working"])

        # Pre-create all registered outputs
        for ch in ["R", "G", "B"]:
            (working / f"NGC1499_{ch}_master_registered.xisf").touch()

        stage = self._make_stage(minimal_config["directories"]["working"])

        def side_effect(script, pi_exe=None, timeout=None):
            # SA still runs (rename logic skips, but SA call happens)
            reg_dir = working / "rgb_registered"
            reg_dir.mkdir(parents=True, exist_ok=True)
            return 0

        with patch("stages.preprocessing.run_pjsr_inline", side_effect=side_effect) as mock_pi:
            result = stage.execute(minimal_config)

        assert result == 0

    def test_raises_when_sa_output_not_found(self, minimal_config):
        """PipelineError when StarAlignment doesn't produce expected _r.xisf file."""
        self._make_masters(minimal_config)
        stage = self._make_stage(minimal_config["directories"]["working"])

        with patch("stages.preprocessing.run_pjsr_inline", return_value=0):
            with pytest.raises(PipelineError, match="StarAlignment output not found"):
                stage.execute(minimal_config)


# =============================================================================
# Stage registry integration
# =============================================================================


class TestStageRegistry:
    """Verify that get_all_stages() returns the concrete preprocessing classes."""

    def test_preprocessing_stages_are_concrete(self, minimal_config):
        """Phase 1 stages should be concrete implementations, not StubStages."""
        from stages import get_all_stages, StubStage

        stages = get_all_stages(minimal_config)
        phase1 = [s for s in stages if s.phase == 1]

        concrete_types = (
            SubframeInspectionStage,
            NBCalibrationStage,
            NBDrizzleStage,
            RGBCalibrationStage,
            RGBToNBRegistrationStage,
        )
        for stage in phase1:
            assert isinstance(stage, concrete_types), (
                f"Expected concrete type, got {type(stage).__name__} "
                f"for stage '{stage.name}'"
            )
            assert not isinstance(stage, StubStage), (
                f"Stage '{stage.name}' is still a StubStage"
            )

    def test_all_phase1_stages_present(self, minimal_config):
        from stages import get_all_stages

        stages = get_all_stages(minimal_config)
        names = [s.name for s in stages if s.phase == 1]
        expected = [
            "Subframe Inspection and Rejection",
            "NB Calibration Registration Integration",
            "NB DrizzleIntegration",
            "RGB Calibration Registration Integration",
            "RGB to NB Frame Registration",
        ]
        for name in expected:
            assert name in names, f"Stage '{name}' missing from registry"
