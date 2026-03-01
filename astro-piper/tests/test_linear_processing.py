"""
tests/test_linear_processing.py -- Sprint 4: Phase 2 linear processing stage tests

Tests for all 8 concrete stage classes in stages/linear_processing.py.
PixInsight is never invoked: run_pjsr_inline and graxpert helpers are mocked.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from stages.linear_processing import (
    DynamicCropStage,
    GraXpertBgExtStage,
    SHOLinearCombineStage,
    BXTCorrectOnlyStage,
    BXTSharpenStage,
    ChannelSplitStage,
    GraXpertDenoiseStage,
    SXTStage,
)
from orchestrator import PipelineError
from graxpert_runner import GraXpertError


# =============================================================================
# Fixtures
# =============================================================================

NB_FILTERS  = ["Ha", "OIII", "SII"]
RGB_FILTERS = ["R", "G", "B"]


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
            "nb":  {"filters": NB_FILTERS},
            "rgb": {"filters": RGB_FILTERS},
        },
        "preprocessing": {
            "crop_pixels": 200,
        },
        "processing": {
            "graxpert_smoothing":              0.1,
            "graxpert_correction":             "Subtraction",
            "graxpert_denoise_strength":       0.5,
            "graxpert_denoise_batch_size":     4,
            "graxpert_denoise_strength_ha":    0.40,
            "graxpert_denoise_strength_oiii":  0.60,
            "graxpert_denoise_strength_sii":   0.50,
            "bxt_sharpen_stars":               0.25,
            "bxt_sharpen_nonstellar":          0.40,
            "bxt_adjust_halos":                0.05,
        },
    }


# =============================================================================
# TestDynamicCropStage
# =============================================================================

class TestDynamicCropStage:

    def _stage(self) -> DynamicCropStage:
        return DynamicCropStage(
            name="DynamicCrop All Channels", phase=2, track="nb",
        )

    def test_crops_all_channels(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        # Create input files (NB drizzle + RGB registered)
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_drizzle.xisf").touch()
        for ch in RGB_FILTERS:
            (working / f"NGC1499_{ch}_master_registered.xisf").touch()

        call_count = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            # Determine which file this script is cropping and create the output
            for ch in NB_FILTERS + RGB_FILTERS:
                if f"NGC1499_{ch}_" in script:
                    (working / f"NGC1499_{ch}_cropped.xisf").touch()
                    break
            call_count.append(1)
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert len(call_count) == len(NB_FILTERS) + len(RGB_FILTERS)
        for ch in NB_FILTERS + RGB_FILTERS:
            assert (working / f"NGC1499_{ch}_cropped.xisf").exists()

    def test_skips_existing_outputs(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        # Pre-create all crop outputs
        for ch in NB_FILTERS + RGB_FILTERS:
            (working / f"NGC1499_{ch}_cropped.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)

        assert rc == 0
        mock_pi.assert_not_called()

    def test_raises_if_input_missing(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        # Only create Ha drizzle; OIII and SII missing

        (working / "NGC1499_Ha_drizzle.xisf").touch()

        def fake_pjsr(script, pi_exe=None, timeout=None):
            if "NGC1499_Ha_" in script:
                (working / "NGC1499_Ha_cropped.xisf").touch()
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            with pytest.raises(PipelineError, match="input file not found"):
                self._stage().execute(config)

    def test_partial_skip(self, tmp_path):
        """Already-cropped channels are skipped; remaining are processed."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_drizzle.xisf").touch()
        for ch in RGB_FILTERS:
            (working / f"NGC1499_{ch}_master_registered.xisf").touch()

        # Pre-crop Ha only
        (working / "NGC1499_Ha_cropped.xisf").touch()

        call_count = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            for ch in NB_FILTERS + RGB_FILTERS:
                if f"NGC1499_{ch}_" in script:
                    (working / f"NGC1499_{ch}_cropped.xisf").touch()
                    break
            call_count.append(1)
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        # Ha was pre-cropped, so 5 remaining channels
        assert len(call_count) == len(NB_FILTERS) + len(RGB_FILTERS) - 1

    def test_pjsr_failure_raises(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_drizzle.xisf").touch()
        for ch in RGB_FILTERS:
            (working / f"NGC1499_{ch}_master_registered.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError):
                self._stage().execute(config)


# =============================================================================
# TestGraXpertBgExtStage
# =============================================================================

class TestGraXpertBgExtStage:

    def _stage(self) -> GraXpertBgExtStage:
        return GraXpertBgExtStage(
            name="GraXpert Background Extraction", phase=2, track="nb",
        )

    def test_runs_per_channel(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_cropped.xisf").touch()

        called_channels = []

        def fake_graxpert(input_path, output_path, **kwargs):
            ch = Path(input_path).stem.split("_")[1]  # NGC1499_<ch>_cropped
            Path(output_path).touch()
            called_channels.append(ch)

        with patch("stages.linear_processing.run_graxpert", side_effect=fake_graxpert):
            rc = self._stage().execute(config)

        assert rc == 0
        assert set(called_channels) == set(NB_FILTERS)
        for ch in NB_FILTERS:
            assert (working / f"NGC1499_{ch}_bgext.xisf").exists()

    def test_skips_existing(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_bgext.xisf").touch()

        with patch("stages.linear_processing.run_graxpert") as mock_gx:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_gx.assert_not_called()

    def test_graxpert_error_becomes_pipeline_error(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_cropped.xisf").touch()

        with patch(
            "stages.linear_processing.run_graxpert",
            side_effect=GraXpertError("GPU OOM"),
        ):
            with pytest.raises(PipelineError, match="GraXpert background extraction failed"):
                self._stage().execute(config)

    def test_smoothing_from_config(self, tmp_path):
        """Verify smoothing and correction are passed through from config."""
        config = make_config(tmp_path)
        config["processing"]["graxpert_smoothing"] = 0.05
        config["processing"]["graxpert_correction"] = "Division"
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_cropped.xisf").touch()

        captured_kwargs = []

        def fake_graxpert(input_path, output_path, **kwargs):
            Path(output_path).touch()
            captured_kwargs.append(kwargs)

        with patch("stages.linear_processing.run_graxpert", side_effect=fake_graxpert):
            self._stage().execute(config)

        assert captured_kwargs[0]["smoothing"] == 0.05
        assert captured_kwargs[0]["correction"] == "Division"


# =============================================================================
# TestSHOLinearCombineStage
# =============================================================================

class TestSHOLinearCombineStage:

    def _stage(self) -> SHOLinearCombineStage:
        return SHOLinearCombineStage(
            name="SHO Channel Combination for BXT", phase=2, track="nb",
        )

    def test_runs_combine(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            (working / "NGC1499_SHO_linear.xisf").touch()
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_linear.xisf").exists()

    def test_skips_if_output_exists(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_linear.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_raises_wrong_filters(self, tmp_path):
        config = make_config(tmp_path)
        config["acquisition"]["nb"]["filters"] = ["Ha", "OIII"]  # missing SII

        with patch("stages.linear_processing.run_pjsr_inline"):
            with pytest.raises(PipelineError, match="expected nb_filters"):
                self._stage().execute(config)


# =============================================================================
# TestBXTCorrectOnlyStage
# =============================================================================

class TestBXTCorrectOnlyStage:

    def _stage(self) -> BXTCorrectOnlyStage:
        return BXTCorrectOnlyStage(
            name="BlurXTerminator Correct Only", phase=2, track="nb",
        )

    def test_runs_bxt_correct_only(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "correct_only" in script
            assert "true" in script  # BXT correct_only=true
            (working / "NGC1499_SHO_bxt_corrected.xisf").touch()
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_bxt_corrected.xisf").exists()

    def test_skips_if_output_exists(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_bxt_corrected.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()


# =============================================================================
# TestBXTSharpenStage
# =============================================================================

class TestBXTSharpenStage:

    def _stage(self) -> BXTSharpenStage:
        return BXTSharpenStage(
            name="BlurXTerminator Sharpen", phase=2, track="nb",
        )

    def test_runs_bxt_sharpen(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            assert "correct_only" in script
            assert "false" in script  # BXT correct_only=false for sharpen pass
            (working / "NGC1499_SHO_bxt.xisf").touch()
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_bxt.xisf").exists()

    def test_skips_if_output_exists(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_bxt.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_bxt_params_from_config(self, tmp_path):
        config = make_config(tmp_path)
        config["processing"]["bxt_sharpen_stars"] = 0.30
        working = Path(config["directories"]["working"])

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_bxt.xisf").touch()
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "0.300" in captured[0]  # sharpen_stars=0.30 in script


# =============================================================================
# TestChannelSplitStage
# =============================================================================

class TestChannelSplitStage:

    def _stage(self) -> ChannelSplitStage:
        return ChannelSplitStage(
            name="Channel Split SHO to S H O", phase=2, track="nb",
        )

    def test_splits_channels(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        def fake_pjsr(script, pi_exe=None, timeout=None):
            # ChannelExtraction creates SII (R), Ha (G), OIII (B)
            (working / "NGC1499_SII_processed.xisf").touch()
            (working / "NGC1499_Ha_processed.xisf").touch()
            (working / "NGC1499_OIII_processed.xisf").touch()
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SII_processed.xisf").exists()
        assert (working / "NGC1499_Ha_processed.xisf").exists()
        assert (working / "NGC1499_OIII_processed.xisf").exists()

    def test_skips_if_all_outputs_exist(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_processed.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_reruns_if_partial_outputs(self, tmp_path):
        """If only some outputs exist, the split is re-run."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        # Only Ha processed exists -- stage should still run
        (working / "NGC1499_Ha_processed.xisf").touch()

        def fake_pjsr(script, pi_exe=None, timeout=None):
            for ch in NB_FILTERS:
                (working / f"NGC1499_{ch}_processed.xisf").touch()
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr) as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_called_once()


# =============================================================================
# TestGraXpertDenoiseStage
# =============================================================================

class TestGraXpertDenoiseStage:

    def _stage(self) -> GraXpertDenoiseStage:
        return GraXpertDenoiseStage(
            name="GraXpert Denoise Per NB Channel", phase=2, track="nb",
        )

    def test_per_channel_strength(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_processed.xisf").touch()

        called = {}

        def fake_denoise(input_path, output_path, strength=0.5, **kwargs):
            ch_name = Path(str(input_path)).stem.split("_")[1]
            Path(str(output_path)).touch()
            called[ch_name] = strength

        with patch("stages.linear_processing.run_graxpert_denoise", side_effect=fake_denoise):
            rc = self._stage().execute(config)

        assert rc == 0
        assert called["Ha"]   == pytest.approx(0.40)
        assert called["OIII"] == pytest.approx(0.60)
        assert called["SII"]  == pytest.approx(0.50)

    def test_default_strength_for_unknown_channel(self, tmp_path):
        config = make_config(tmp_path)
        config["acquisition"]["nb"]["filters"] = ["Hb"]  # unusual channel
        working = Path(config["directories"]["working"])
        (working / "NGC1499_Hb_processed.xisf").touch()

        captured_strength = []

        def fake_denoise(input_path, output_path, strength=0.5, **kwargs):
            Path(str(output_path)).touch()
            captured_strength.append(strength)

        with patch("stages.linear_processing.run_graxpert_denoise", side_effect=fake_denoise):
            self._stage().execute(config)

        assert captured_strength[0] == pytest.approx(0.5)

    def test_skips_existing_outputs(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_denoised.xisf").touch()

        with patch("stages.linear_processing.run_graxpert_denoise") as mock_d:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_d.assert_not_called()

    def test_graxpert_error_becomes_pipeline_error(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_processed.xisf").touch()

        with patch(
            "stages.linear_processing.run_graxpert_denoise",
            side_effect=GraXpertError("model not found"),
        ):
            with pytest.raises(PipelineError, match="GraXpert denoising failed"):
                self._stage().execute(config)

    def test_batch_size_from_config(self, tmp_path):
        config = make_config(tmp_path)
        config["processing"]["graxpert_denoise_batch_size"] = 2
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_processed.xisf").touch()

        captured_batch = []

        def fake_denoise(input_path, output_path, batch_size=4, **kwargs):
            Path(str(output_path)).touch()
            captured_batch.append(batch_size)

        with patch("stages.linear_processing.run_graxpert_denoise", side_effect=fake_denoise):
            self._stage().execute(config)

        assert all(b == 2 for b in captured_batch)


# =============================================================================
# TestSXTStage
# =============================================================================

class TestSXTStage:

    def _stage(self) -> SXTStage:
        return SXTStage(
            name="StarXTerminator Per NB Channel", phase=2, track="nb",
        )

    def test_runs_sxt_per_channel(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_denoised.xisf").touch()

        processed = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            for ch in NB_FILTERS:
                if f"NGC1499_{ch}_denoised" in script:
                    (working / f"NGC1499_{ch}_starless.xisf").touch()
                    processed.append(ch)
                    break
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert set(processed) == set(NB_FILTERS)
        for ch in NB_FILTERS:
            assert (working / f"NGC1499_{ch}_starless.xisf").exists()

    def test_stars_discarded(self, tmp_path):
        """stars_output_path=None so SXT does not try to save NB stars."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_denoised.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            for ch in NB_FILTERS:
                if f"NGC1499_{ch}_denoised" in script:
                    (working / f"NGC1499_{ch}_starless.xisf").touch()
                    break
            return 0

        with patch("stages.linear_processing.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # stars_image should be false (not saving stars-only image)
        for script in captured:
            assert "SXT.stars    = false" in script

    def test_skips_existing_outputs(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_pjsr_failure_raises(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_denoised.xisf").touch()

        with patch("stages.linear_processing.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError):
                self._stage().execute(config)


# =============================================================================
# TestStageRegistry (Phase 2 import check)
# =============================================================================

class TestPhase2Imports:
    """Verify that stages/__init__.py registers Phase 2 as concrete classes."""

    def test_phase2_stages_are_concrete(self, tmp_path):
        from stages import get_all_stages, StubStage

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": NB_FILTERS},
                "rgb": {"filters": RGB_FILTERS},
            },
        }
        stages = get_all_stages(config)
        phase2 = [s for s in stages if s.phase == 2]
        assert len(phase2) == 8, f"Expected 8 Phase 2 stages, got {len(phase2)}"
        for s in phase2:
            assert not isinstance(s, StubStage), (
                f"Phase 2 stage '{s.name}' is still a StubStage"
            )
