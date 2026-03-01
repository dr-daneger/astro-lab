"""
tests/test_stretching.py -- Sprint 4: Phase 3 stretching and palette stage tests

Tests for the 3 concrete stage classes in stages/stretching.py:
  StretchNBStage, LinearFitStage, ForaxxPaletteStage.

PixInsight is never invoked: run_pjsr_inline is mocked throughout.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from stages.stretching import StretchNBStage, LinearFitStage, ForaxxPaletteStage
from orchestrator import PipelineError


# =============================================================================
# Fixtures
# =============================================================================

NB_FILTERS = ["Ha", "OIII", "SII"]


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
        },
        "acquisition": {
            "nb": {"filters": NB_FILTERS},
        },
        "processing": {
            "ghs_stretch_factor": 5.0,
            "ghs_shape_param":    2.0,
        },
    }


# =============================================================================
# TestStretchNBStage
# =============================================================================

class TestStretchNBStage:

    def _stage(self) -> StretchNBStage:
        return StretchNBStage(
            name="Stretch Starless NB Channels", phase=3, track="nb",
        )

    def test_stretches_all_channels(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless.xisf").touch()

        processed = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            for ch in NB_FILTERS:
                if f"NGC1499_{ch}_starless" in script:
                    (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()
                    processed.append(ch)
                    break
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert set(processed) == set(NB_FILTERS)
        for ch in NB_FILTERS:
            assert (working / f"NGC1499_{ch}_starless_stretched.xisf").exists()

    def test_skips_existing_outputs(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()

        with patch("stages.stretching.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_partial_skip(self, tmp_path):
        """Channels with existing stretched outputs are skipped individually."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless.xisf").touch()
        # Pre-stretch Ha only
        (working / "NGC1499_Ha_starless_stretched.xisf").touch()

        call_count = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            for ch in NB_FILTERS:
                if f"NGC1499_{ch}_starless" in script:
                    (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()
                    break
            call_count.append(1)
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        # Ha was pre-stretched, so only 2 channels processed
        assert len(call_count) == len(NB_FILTERS) - 1

    def test_ghs_params_from_config(self, tmp_path):
        config = make_config(tmp_path)
        config["processing"]["ghs_stretch_factor"] = 7.5
        config["processing"]["ghs_shape_param"] = 3.0
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            for ch in NB_FILTERS:
                if f"NGC1499_{ch}_starless" in script:
                    (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()
                    break
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # D=7.5, b=3.0 should appear in each script
        for script in captured:
            assert "7.5000" in script
            assert "3.0000" in script

    def test_sp_is_fixed_at_0001(self, tmp_path):
        """SP (symmetry point) is always 0.0001 regardless of config."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            for ch in NB_FILTERS:
                if f"NGC1499_{ch}_starless" in script:
                    (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()
                    break
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        for script in captured:
            assert "0.000100" in script  # SP=0.0001 formatted to 6 decimal places

    def test_pjsr_failure_raises(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless.xisf").touch()

        with patch("stages.stretching.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError):
                self._stage().execute(config)


# =============================================================================
# TestLinearFitStage
# =============================================================================

class TestLinearFitStage:

    def _stage(self) -> LinearFitStage:
        return LinearFitStage(
            name="LinearFit Ha+SII to OIII Reference", phase=3, track="nb",
        )

    def test_runs_linear_fit(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        # Create input stretched files
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()

        def fake_pjsr(script, pi_exe=None, timeout=None):
            (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
            (working / "NGC1499_SII_starless_linearfit.xisf").touch()
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_Ha_starless_linearfit.xisf").exists()
        assert (working / "NGC1499_SII_starless_linearfit.xisf").exists()

    def test_skips_if_both_outputs_exist(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
        (working / "NGC1499_SII_starless_linearfit.xisf").touch()

        with patch("stages.stretching.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_reruns_if_only_one_output_exists(self, tmp_path):
        """If only Ha linearfit exists (but not SII), the stage re-runs."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()
        (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
        # SII linearfit missing

        def fake_pjsr(script, pi_exe=None, timeout=None):
            (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
            (working / "NGC1499_SII_starless_linearfit.xisf").touch()
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr) as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_called_once()

    def test_oiii_is_reference_not_target(self, tmp_path):
        """OIII is the LinearFit reference; the script should not re-save OIII."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
            (working / "NGC1499_SII_starless_linearfit.xisf").touch()
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # OIII stretched path should appear as the reference
        assert "OIII_starless_stretched" in captured[0]
        # OIII linearfit path should NOT appear (OIII is never a target)
        assert "OIII_starless_linearfit" not in captured[0]

    def test_pjsr_failure_raises(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        for ch in NB_FILTERS:
            (working / f"NGC1499_{ch}_starless_stretched.xisf").touch()

        with patch("stages.stretching.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError):
                self._stage().execute(config)


# =============================================================================
# TestForaxxPaletteStage
# =============================================================================

class TestForaxxPaletteStage:

    def _stage(self) -> ForaxxPaletteStage:
        return ForaxxPaletteStage(
            name="Foraxx Dynamic Palette Combination", phase=3, track="nb",
        )

    def test_runs_foraxx(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])

        # Create linearfit inputs for Ha and SII, and stretched for OIII
        (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
        (working / "NGC1499_SII_starless_linearfit.xisf").touch()
        (working / "NGC1499_OIII_starless_stretched.xisf").touch()

        def fake_pjsr(script, pi_exe=None, timeout=None):
            (working / "NGC1499_SHO_foraxx.xisf").touch()
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            rc = self._stage().execute(config)

        assert rc == 0
        assert (working / "NGC1499_SHO_foraxx.xisf").exists()

    def test_skips_if_output_exists(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_SHO_foraxx.xisf").touch()

        with patch("stages.stretching.run_pjsr_inline") as mock_pi:
            rc = self._stage().execute(config)
        assert rc == 0
        mock_pi.assert_not_called()

    def test_raises_wrong_filters(self, tmp_path):
        config = make_config(tmp_path)
        config["acquisition"]["nb"]["filters"] = ["Ha", "OIII"]  # missing SII

        with patch("stages.stretching.run_pjsr_inline"):
            with pytest.raises(PipelineError, match="expected nb_filters"):
                self._stage().execute(config)

    def test_uses_linearfit_paths_for_ha_sii(self, tmp_path):
        """Foraxx takes Ha/SII from _linearfit paths, not _stretched."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
        (working / "NGC1499_SII_starless_linearfit.xisf").touch()
        (working / "NGC1499_OIII_starless_stretched.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_foraxx.xisf").touch()
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        assert "Ha_starless_linearfit" in captured[0]
        assert "SII_starless_linearfit" in captured[0]
        assert "OIII_starless_stretched" in captured[0]
        # OIII linearfit should NOT be referenced
        assert "OIII_starless_linearfit" not in captured[0]

    def test_foraxx_expression_in_script(self, tmp_path):
        """The generated script should contain the Foraxx PixelMath expression."""
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
        (working / "NGC1499_SII_starless_linearfit.xisf").touch()
        (working / "NGC1499_OIII_starless_stretched.xisf").touch()

        captured = []

        def fake_pjsr(script, pi_exe=None, timeout=None):
            captured.append(script)
            (working / "NGC1499_SHO_foraxx.xisf").touch()
            return 0

        with patch("stages.stretching.run_pjsr_inline", side_effect=fake_pjsr):
            self._stage().execute(config)

        # Foraxx formula landmark: PIP weighting with Oiii
        assert "Oiii" in captured[0]
        assert "Sii" in captured[0]
        assert "Ha" in captured[0]

    def test_pjsr_failure_raises(self, tmp_path):
        config = make_config(tmp_path)
        working = Path(config["directories"]["working"])
        (working / "NGC1499_Ha_starless_linearfit.xisf").touch()
        (working / "NGC1499_SII_starless_linearfit.xisf").touch()
        (working / "NGC1499_OIII_starless_stretched.xisf").touch()

        with patch("stages.stretching.run_pjsr_inline", return_value=1):
            with pytest.raises(PipelineError):
                self._stage().execute(config)


# =============================================================================
# TestStageRegistry (Phase 3 import check)
# =============================================================================

class TestPhase3Imports:
    """Verify that stages/__init__.py registers Phase 3 as concrete classes."""

    def test_phase3_stages_are_concrete(self, tmp_path):
        from stages import get_all_stages, StubStage

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": NB_FILTERS},
                "rgb": {"filters": ["R", "G", "B"]},
            },
        }
        stages = get_all_stages(config)
        phase3 = [s for s in stages if s.phase == 3]
        assert len(phase3) == 5, f"Expected 5 Phase 3 stages, got {len(phase3)}"
        for s in phase3:
            assert not isinstance(s, StubStage), (
                f"Phase 3 stage '{s.name}' is still a StubStage"
            )

    def test_linearfit_stage_present(self, tmp_path):
        """LinearFit is a new stage added in Sprint 4 (not in original design_doc)."""
        from stages import get_all_stages

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": NB_FILTERS},
                "rgb": {"filters": ["R", "G", "B"]},
            },
        }
        stages = get_all_stages(config)
        names = [s.name for s in stages]
        assert any("LinearFit" in n for n in names), (
            "LinearFitStage not found in stage registry"
        )

    def test_foraxx_uses_linearfit_inputs(self, tmp_path):
        """Foraxx input_spec should reference _linearfit paths, not _stretched."""
        from stages import get_all_stages

        config = {
            "directories": {"working": str(tmp_path), "output": str(tmp_path)},
            "acquisition": {
                "nb":  {"filters": NB_FILTERS},
                "rgb": {"filters": ["R", "G", "B"]},
            },
        }
        stages = get_all_stages(config)
        foraxx = next(s for s in stages if "Foraxx" in s.name)
        assert any("linearfit" in p for p in foraxx.input_spec), (
            "Foraxx input_spec should include _linearfit paths; "
            f"got: {foraxx.input_spec}"
        )
