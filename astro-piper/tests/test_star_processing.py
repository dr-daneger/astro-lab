"""
tests/test_star_processing.py -- Sprint 5: Phase 5 star processing stage tests

Tests for all 7 concrete stage classes in stages/star_processing.py.
PixInsight is never invoked: run_pjsr_inline is mocked throughout.
StarHaloReductionStage uses shutil.copy2 which operates on real tmp_path files.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from stages.star_processing import (
    RGBChannelCombineStage,
    SPCCStage,
    RGBStretchStage,
    SXTRGBStage,
    StarHaloReductionStage,
    ScreenBlendStage,
    FinalCropStage,
)
from orchestrator import PipelineError


# =============================================================================
# Fixtures
# =============================================================================

RGB_FILTERS = ["R", "G", "B"]


def make_config(tmp_path: Path) -> dict:
    working = tmp_path / "working"
    working.mkdir()
    output  = tmp_path / "output"
    return {
        "directories": {
            "working": str(working),
            "output":  str(output),
        },
        "tools": {
            "pixinsight_exe": "C:/fake/PixInsight.exe",
        },
        "acquisition": {
            "nb":  {"filters": ["Ha", "OIII", "SII"]},
            "rgb": {"filters": RGB_FILTERS},
        },
        "preprocessing": {
            "crop_pixels":       200,
            "final_crop_pixels": 0,
        },
        "processing": {
            "ghs_rgb_stretch_factor": 3.0,
            "ghs_shape_param":        2.0,
            "star_brightness_factor": 0.70,
        },
    }


# =============================================================================
# TestRGBChannelCombineStage
# =============================================================================

class TestRGBChannelCombineStage:

    def _stage(self) -> RGBChannelCombineStage:
        return RGBChannelCombineStage(
            name="RGB ChannelCombination", phase=5, track="rgb"
        )

    def test_combines_rgb(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in RGB_FILTERS:
            (working / f"NGC1499_{ch}_cropped.xisf").touch()

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "ChannelCombination" in script
            (working / "NGC1499_RGB_composite.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_RGB_composite.xisf").exists()

    def test_uses_cropped_inputs(self, tmp_path):
        """Stage reads from _cropped.xisf, not _master_registered.xisf."""
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in RGB_FILTERS:
            (working / f"NGC1499_{ch}_cropped.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_RGB_composite.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        for ch in RGB_FILTERS:
            assert f"NGC1499_{ch}_cropped" in captured[0]

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_RGB_composite.xisf").touch()

        with patch("stages.star_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_pjsr_failure_raises(self, tmp_path):
        config = make_config(tmp_path)
        with patch("stages.star_processing.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError):
                self._stage().execute(config)


# =============================================================================
# TestSPCCStage
# =============================================================================

class TestSPCCStage:

    def _stage(self) -> SPCCStage:
        return SPCCStage(name="SpectrophotometricColorCalibration", phase=5, track="rgb")

    def test_runs_spcc(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "SpectrophotometricColorCalibration" in script
            (working / "NGC1499_RGB_spcc.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_RGB_spcc.xisf").exists()

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_RGB_spcc.xisf").touch()

        with patch("stages.star_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()


# =============================================================================
# TestRGBStretchStage
# =============================================================================

class TestRGBStretchStage:

    def _stage(self) -> RGBStretchStage:
        return RGBStretchStage(name="RGB Star Stretch", phase=5, track="rgb")

    def test_runs_ghs_stretch(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "GeneralizedHyperbolicStretch" in script
            (working / "NGC1499_RGB_stretched.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_RGB_stretched.xisf").exists()

    def test_uses_rgb_stretch_factor(self, tmp_path):
        """RGB stretch uses ghs_rgb_stretch_factor, not ghs_stretch_factor."""
        config  = make_config(tmp_path)
        config["processing"]["ghs_rgb_stretch_factor"] = 3.5
        config["processing"]["ghs_stretch_factor"]     = 7.0  # NB factor, not used here
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_RGB_stretched.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "3.5000" in captured[0]  # D=3.5 in script
        assert "7.0000" not in captured[0]

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_RGB_stretched.xisf").touch()

        with patch("stages.star_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()


# =============================================================================
# TestSXTRGBStage
# =============================================================================

class TestSXTRGBStage:

    def _stage(self) -> SXTRGBStage:
        return SXTRGBStage(name="StarXTerminator RGB Composite", phase=5, track="rgb")

    def test_extracts_stars(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            (working / "NGC1499_RGB_starless.xisf").touch()
            (working / "NGC1499_RGB_stars_only.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_RGB_starless.xisf").exists()
        assert (working / "NGC1499_RGB_stars_only.xisf").exists()

    def test_stars_image_enabled(self, tmp_path):
        """RGB SXT must save the stars-only image (stars_image = true)."""
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_RGB_starless.xisf").touch()
            (working / "NGC1499_RGB_stars_only.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "SXT.stars    = true" in captured[0]

    def test_skips_if_both_outputs_exist(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_RGB_starless.xisf").touch()
        (working / "NGC1499_RGB_stars_only.xisf").touch()

        with patch("stages.star_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_reruns_if_only_starless_exists(self, tmp_path):
        """If stars_only is missing (even if starless exists), re-run."""
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_RGB_starless.xisf").touch()
        # stars_only missing

        def fake_pjsr(script, pi_exe=None, timeout=None):
            (working / "NGC1499_RGB_starless.xisf").touch()
            (working / "NGC1499_RGB_stars_only.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr) as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_called_once()


# =============================================================================
# TestStarHaloReductionStage
# =============================================================================

class TestStarHaloReductionStage:

    def _stage(self) -> StarHaloReductionStage:
        return StarHaloReductionStage(name="Star Halo Reduction", phase=5, track="rgb")

    def test_copies_stars_to_haloreduced(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        # Create a real stars_only file with content so copy can be verified
        stars_only   = working / "NGC1499_RGB_stars_only.xisf"
        haloreduced  = working / "NGC1499_RGB_stars_haloreduced.xisf"
        stars_only.write_bytes(b"fake_stars_data")

        rc = self._stage().execute(config)

        assert rc == 0
        assert haloreduced.exists()
        assert haloreduced.read_bytes() == b"fake_stars_data"

    def test_skips_if_haloreduced_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_RGB_stars_haloreduced.xisf").write_bytes(b"manual_halo_reduced")

        rc = self._stage().execute(config)

        assert rc == 0
        # File should not be overwritten
        assert (working / "NGC1499_RGB_stars_haloreduced.xisf").read_bytes() == b"manual_halo_reduced"

    def test_raises_if_stars_only_missing(self, tmp_path):
        config  = make_config(tmp_path)
        # stars_only does not exist

        with pytest.raises(PipelineError, match="stars-only file not found"):
            self._stage().execute(config)


# =============================================================================
# TestScreenBlendStage
# =============================================================================

class TestScreenBlendStage:

    def _stage(self) -> ScreenBlendStage:
        return ScreenBlendStage(name="Screen Blend Star Recombination", phase=5, track="merge")

    def test_runs_screen_blend(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "screen" in script.lower() or "~" in script
            (working / "NGC1499_combined.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_combined.xisf").exists()

    def test_star_brightness_in_script(self, tmp_path):
        config  = make_config(tmp_path)
        config["processing"]["star_brightness_factor"] = 0.50
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_combined.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "0.50" in captured[0]

    def test_screen_blend_formula_in_script(self, tmp_path):
        """Script should contain the screen blend ~(~A * ~B) pattern."""
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_combined.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # Screen blend formula uses ~(~X * ~Y) notation
        assert "~" in captured[0]
        assert "SHO_starless" in captured[0]
        assert "RGB_stars" in captured[0]

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_combined.xisf").touch()

        with patch("stages.star_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()


# =============================================================================
# TestFinalCropStage
# =============================================================================

class TestFinalCropStage:

    def _stage(self) -> FinalCropStage:
        return FinalCropStage(name="Final Crop and Cleanup", phase=5, track="final")

    def test_writes_to_output_dir(self, tmp_path):
        config     = make_config(tmp_path)
        working    = Path(config["directories"]["working"])
        output_dir = Path(config["directories"]["output"])

        (working / "NGC1499_combined.xisf").touch()

        def fake_pjsr(script, pi_exe=None, timeout=None):
            # Verify script writes to the output directory
            assert "NGC1499_final" in script
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "NGC1499_final.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (output_dir / "NGC1499_final.xisf").exists()

    def test_creates_output_dir_if_missing(self, tmp_path):
        config     = make_config(tmp_path)
        working    = Path(config["directories"]["working"])
        output_dir = Path(config["directories"]["output"])
        assert not output_dir.exists()

        (working / "NGC1499_combined.xisf").touch()

        def fake_pjsr(script, pi_exe=None, timeout=None):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "NGC1499_final.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert output_dir.exists()

    def test_skips_if_output_exists(self, tmp_path):
        config     = make_config(tmp_path)
        output_dir = Path(config["directories"]["output"])
        output_dir.mkdir(parents=True)
        (output_dir / "NGC1499_final.xisf").touch()

        with patch("stages.star_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_zero_crop_uses_generate_crop(self, tmp_path):
        """final_crop_pixels=0 still runs generate_crop (zero-margin pass)."""
        config  = make_config(tmp_path)
        config["preprocessing"]["final_crop_pixels"] = 0
        working    = Path(config["directories"]["working"])
        output_dir = Path(config["directories"]["output"])

        (working / "NGC1499_combined.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "NGC1499_final.xisf").touch()
            return 0

        with patch("stages.star_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # Script should contain Crop with 0 margins
        assert "Crop" in captured[0]
        assert "leftMargin   = 0" in captured[0]


# =============================================================================
# TestPhase5Imports
# =============================================================================

class TestPhase5Imports:
    """Verify that stages/__init__.py registers Phase 5 as concrete classes."""

    def test_phase5_stages_are_concrete(self, tmp_path):
        from stages import get_all_stages, StubStage

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": ["Ha", "OIII", "SII"]},
                "rgb": {"filters": RGB_FILTERS},
            },
        }
        stages = get_all_stages(config)
        phase5 = [s for s in stages if s.phase == 5]
        assert len(phase5) == 7, f"Expected 7 Phase 5 stages, got {len(phase5)}"
        for s in phase5:
            assert not isinstance(s, StubStage), (
                f"Phase 5 stage '{s.name}' is still a StubStage"
            )

    def test_rgb_combo_uses_cropped_inputs(self, tmp_path):
        """RGBChannelCombineStage input_spec should reference _cropped.xisf."""
        from stages import get_all_stages

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": ["Ha", "OIII", "SII"]},
                "rgb": {"filters": RGB_FILTERS},
            },
        }
        stages = get_all_stages(config)
        rgb_combine = next(s for s in stages if "ChannelCombination" in s.name)
        for path in rgb_combine.input_spec:
            assert "_cropped" in path, (
                f"RGBChannelCombineStage input should use _cropped.xisf; got: {path}"
            )

    def test_total_stage_count(self, tmp_path):
        """All 30 stages registered (5 Phase1 + 8 Phase2 + 4 Phase3 + 6 Phase4 + 7 Phase5)."""
        from stages import get_all_stages

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": ["Ha", "OIII", "SII"]},
                "rgb": {"filters": RGB_FILTERS},
            },
        }
        # Sprint 6 added MeasureHistogramStage + 2 QualityCheckStages = 32 total
        stages = get_all_stages(config)
        assert len(stages) == 32, (
            f"Expected 32 total stages, got {len(stages)}"
        )
