"""
tests/test_nonlinear.py -- Sprint 5: Phase 4 nonlinear processing stage tests

Tests for all 6 concrete stage classes in stages/nonlinear.py.
PixInsight and GraXpert are never invoked: run_pjsr_inline and
run_graxpert_denoise are mocked throughout.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from stages.nonlinear import (
    SCNRStage,
    CurvesHueStage,
    CurvesContrastSatStage,
    HDRMultiscaleStage,
    LHEStage,
    GraXpertDenoiseNonlinearStage,
)
from orchestrator import PipelineError
from graxpert_runner import GraXpertError


# =============================================================================
# Fixtures
# =============================================================================


def make_config(tmp_path: Path) -> dict:
    working = tmp_path / "working"
    working.mkdir()
    return {
        "directories": {
            "working": str(working),
            "output":  str(tmp_path / "output"),
        },
        "tools": {
            "pixinsight_exe": "C:/fake/PixInsight.exe",
            "graxpert_exe":   "C:/fake/GraXpert.EXE",
        },
        "acquisition": {
            "nb":  {"filters": ["Ha", "OIII", "SII"]},
            "rgb": {"filters": ["R", "G", "B"]},
        },
        "preprocessing": {},
        "processing": {
            "scnr_amount":                         0.65,
            "hdrmt_layers":                        6,
            "hdrmt_iterations":                    1,
            "lhe_kernel_radius":                   96,
            "lhe_contrast_limit":                  2.0,
            "lhe_amount":                          0.35,
            "graxpert_denoise_strength_nonlinear": 0.35,
            "graxpert_denoise_batch_size":         4,
        },
    }


# =============================================================================
# TestSCNRStage
# =============================================================================

class TestSCNRStage:

    def _stage(self) -> SCNRStage:
        return SCNRStage(name="SCNR Green Removal", phase=4, track="nb")

    def test_runs_scnr(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "SCNR" in script
            (working / "NGC1499_SHO_scnr.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_scnr.xisf").exists()

    def test_amount_from_config(self, tmp_path):
        config  = make_config(tmp_path)
        config["processing"]["scnr_amount"] = 0.80
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_scnr.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "0.800" in captured[0]

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_scnr.xisf").touch()

        with patch("stages.nonlinear.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_pjsr_failure_raises(self, tmp_path):
        config = make_config(tmp_path)
        with patch("stages.nonlinear.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError):
                self._stage().execute(config)


# =============================================================================
# TestCurvesHueStage
# =============================================================================

class TestCurvesHueStage:

    def _stage(self) -> CurvesHueStage:
        return CurvesHueStage(name="CurvesTransformation Hue Shift", phase=4, track="nb")

    def test_runs_hue_shift(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "CurvesTransformation" in script or "CT" in script
            (working / "NGC1499_SHO_hue.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_hue.xisf").exists()

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_hue.xisf").touch()

        with patch("stages.nonlinear.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_hue_curve_in_script(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_hue.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # Script should contain CT.H (hue curve)
        assert "CT.H" in captured[0]


# =============================================================================
# TestCurvesContrastSatStage
# =============================================================================

class TestCurvesContrastSatStage:

    def _stage(self) -> CurvesContrastSatStage:
        return CurvesContrastSatStage(
            name="CurvesTransformation Contrast Saturation", phase=4, track="nb"
        )

    def test_runs_contrast_sat(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            (working / "NGC1499_SHO_curves.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_curves.xisf").exists()

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_curves.xisf").touch()

        with patch("stages.nonlinear.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_sat_curve_in_script(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_curves.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # Script should contain CT.S (saturation) and CT.K (luminance)
        assert "CT.S" in captured[0]
        assert "CT.K" in captured[0]


# =============================================================================
# TestHDRMultiscaleStage
# =============================================================================

class TestHDRMultiscaleStage:

    def _stage(self) -> HDRMultiscaleStage:
        return HDRMultiscaleStage(name="HDRMultiscaleTransform", phase=4, track="nb")

    def test_runs_hdrmt(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "HDRMultiscaleTransform" in script
            (working / "NGC1499_SHO_hdr.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_hdr.xisf").exists()

    def test_layers_from_config(self, tmp_path):
        config  = make_config(tmp_path)
        config["processing"]["hdrmt_layers"] = 4
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_hdr.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "numberOfLayers     = 4" in captured[0]

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_hdr.xisf").touch()

        with patch("stages.nonlinear.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()


# =============================================================================
# TestLHEStage
# =============================================================================

class TestLHEStage:

    def _stage(self) -> LHEStage:
        return LHEStage(name="LocalHistogramEqualization", phase=4, track="nb")

    def test_runs_lhe(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "LocalHistogramEqualization" in script
            (working / "NGC1499_SHO_lhe.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_lhe.xisf").exists()

    def test_params_from_config(self, tmp_path):
        config  = make_config(tmp_path)
        config["processing"]["lhe_kernel_radius"]  = 128
        config["processing"]["lhe_contrast_limit"] = 1.5
        config["processing"]["lhe_amount"]         = 0.25
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_lhe.xisf").touch()
            return 0

        with patch("stages.nonlinear.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "radius        = 128" in captured[0]
        assert "1.500" in captured[0]
        assert "0.250" in captured[0]

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_lhe.xisf").touch()

        with patch("stages.nonlinear.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()


# =============================================================================
# TestGraXpertDenoiseNonlinearStage
# =============================================================================

class TestGraXpertDenoiseNonlinearStage:

    def _stage(self) -> GraXpertDenoiseNonlinearStage:
        return GraXpertDenoiseNonlinearStage(
            name="GraXpert Denoise Nonlinear", phase=4, track="nb"
        )

    def test_runs_denoise(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_denoise(input_path, output_path, **kwargs):
            Path(str(output_path)).touch()

        with patch("stages.nonlinear.run_graxpert_denoise", side_effect=fake_denoise):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_final_starless.xisf").exists()

    def test_strength_from_config(self, tmp_path):
        config  = make_config(tmp_path)
        config["processing"]["graxpert_denoise_strength_nonlinear"] = 0.20
        working = Path(config["directories"]["working"])

        captured = {}

        def fake_denoise(input_path, output_path, strength=0.5, **kwargs):
            Path(str(output_path)).touch()
            captured["strength"] = strength

        with patch("stages.nonlinear.run_graxpert_denoise", side_effect=fake_denoise):
            self._stage().execute(config)

        assert captured["strength"] == pytest.approx(0.20)

    def test_skips_if_output_exists(self, tmp_path):
        config  = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_final_starless.xisf").touch()

        with patch("stages.nonlinear.run_graxpert_denoise") as mock_d:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_d.assert_not_called()

    def test_graxpert_error_becomes_pipeline_error(self, tmp_path):
        config = make_config(tmp_path)

        with patch(
            "stages.nonlinear.run_graxpert_denoise",
            side_effect=GraXpertError("CUDA error"),
        ):
            with pytest.raises(PipelineError, match="nonlinear denoising failed"):
                self._stage().execute(config)

    def test_uses_nonlinear_strength_key(self, tmp_path):
        """nonlinear stage reads graxpert_denoise_strength_nonlinear, not the linear key."""
        config  = make_config(tmp_path)
        config["processing"]["graxpert_denoise_strength"]            = 0.99  # linear key
        config["processing"]["graxpert_denoise_strength_nonlinear"]  = 0.20  # nonlinear key
        working = Path(config["directories"]["working"])

        captured = {}

        def fake_denoise(input_path, output_path, strength=0.5, **kwargs):
            Path(str(output_path)).touch()
            captured["strength"] = strength

        with patch("stages.nonlinear.run_graxpert_denoise", side_effect=fake_denoise):
            self._stage().execute(config)

        assert captured["strength"] == pytest.approx(0.20)


# =============================================================================
# TestPhase4Imports
# =============================================================================

class TestPhase4Imports:
    """Verify that stages/__init__.py registers Phase 4 as concrete classes."""

    def test_phase4_stages_are_concrete(self, tmp_path):
        from stages import get_all_stages, StubStage

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": ["Ha", "OIII", "SII"]},
                "rgb": {"filters": ["R", "G", "B"]},
            },
        }
        stages = get_all_stages(config)
        phase4 = [s for s in stages if s.phase == 4]
        assert len(phase4) == 7, f"Expected 7 Phase 4 stages, got {len(phase4)}"
        for s in phase4:
            assert not isinstance(s, StubStage), (
                f"Phase 4 stage '{s.name}' is still a StubStage"
            )
