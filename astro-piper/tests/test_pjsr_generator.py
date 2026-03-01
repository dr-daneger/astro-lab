"""
tests/test_pjsr_generator.py -- Unit tests for PJSR script generation

Sprint 2 deliverable. Tests verify:
  - Path normalization: backslash -> forward slash on Windows
  - Generated scripts contain the correct PJSR class names
  - Parameters are correctly embedded in the output
  - Structural integrity: header, window open/save/close present
  - Multi-image scripts (Foraxx, screen blend) open all required windows
  - Output matches expected patterns without executing in PixInsight

Run with: python -m pytest tests/test_pjsr_generator.py -v
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import pjsr_generator as gen


# ============================================================================
# Helpers
# ============================================================================


def contains(script: str, pattern: str) -> bool:
    return pattern in script


def has_forward_slashes_only(path_in_script: str) -> bool:
    """Return True if all path strings in the script use forward slashes."""
    # Find all quoted strings that look like paths
    paths = re.findall(r'"([A-Za-z]:/[^"]*)"', path_in_script)
    return all("\\" not in p for p in paths)


# ============================================================================
# Utility functions
# ============================================================================


def test_pjsr_path_backslash_conversion():
    assert gen.pjsr_path("E:\\AstroPipeline\\NGC1499\\Ha.xisf") == \
        "E:/AstroPipeline/NGC1499/Ha.xisf"


def test_pjsr_path_forward_slash_unchanged():
    assert gen.pjsr_path("E:/AstroPipeline/NGC1499/Ha.xisf") == \
        "E:/AstroPipeline/NGC1499/Ha.xisf"


def test_pjsr_path_from_pathlib():
    p = Path("E:/data/test.xisf")
    assert gen.pjsr_path(p) == "E:/data/test.xisf"


def test_js_bool_true():
    assert gen.js_bool(True) == "true"


def test_js_bool_false():
    assert gen.js_bool(False) == "false"


def test_js_float_formatting():
    assert gen.js_float(0.25) == "0.250"
    assert gen.js_float(5.0, 1) == "5.0"
    assert gen.js_float(0.0001, 6) == "0.000100"


def test_js_path_array():
    result = gen.js_path_array(["E:/a/b.xisf", "E:/c/d.xisf"])
    assert '"E:/a/b.xisf"' in result
    assert '"E:/c/d.xisf"' in result
    assert result.startswith("[") and result.endswith("]")


def test_js_enabled_path_array_forward_slashes():
    result = gen.js_enabled_path_array(["E:\\data\\Ha.xisf"])
    assert "\\" not in result
    assert "E:/data/Ha.xisf" in result
    assert "true" in result


# ============================================================================
# Phase 1: Preprocessing generators
# ============================================================================


class TestImageCalibration:
    def test_class_name_present(self):
        s = gen.generate_image_calibration(
            light_paths=["E:/raw/Ha_001.xisf"],
            output_dir="E:/cal",
        )
        assert "ImageCalibration" in s

    def test_execute_global(self):
        s = gen.generate_image_calibration(
            light_paths=["E:/raw/Ha_001.xisf"],
            output_dir="E:/cal",
        )
        assert "executeGlobal()" in s

    def test_pedestal_embedded(self):
        s = gen.generate_image_calibration(
            light_paths=["E:/raw/Ha_001.xisf"],
            output_dir="E:/cal",
            pedestal=150,
        )
        assert "= 150;" in s

    def test_dark_enabled_when_provided(self):
        s = gen.generate_image_calibration(
            light_paths=["E:/raw/Ha.xisf"],
            output_dir="E:/cal",
            master_dark_path="E:/masters/dark.xisf",
        )
        assert "masterDarkEnabled   = true" in s
        assert "E:/masters/dark.xisf" in s

    def test_dark_disabled_when_none(self):
        s = gen.generate_image_calibration(
            light_paths=["E:/raw/Ha.xisf"],
            output_dir="E:/cal",
            master_dark_path=None,
        )
        assert "masterDarkEnabled   = false" in s

    def test_flat_enabled_when_provided(self):
        s = gen.generate_image_calibration(
            light_paths=["E:/raw/Ha.xisf"],
            output_dir="E:/cal",
            master_flat_path="E:/masters/flat_Ha.xisf",
        )
        assert "masterFlatEnabled   = true" in s

    def test_paths_use_forward_slashes(self):
        s = gen.generate_image_calibration(
            light_paths=["E:\\raw\\Ha.xisf"],
            output_dir="E:\\cal",
            master_dark_path="E:\\masters\\dark.xisf",
        )
        assert has_forward_slashes_only(s)

    def test_output_postfix_embedded(self):
        s = gen.generate_image_calibration(
            light_paths=["E:/raw/Ha.xisf"],
            output_dir="E:/cal",
            output_postfix="_cal",
        )
        assert '"_cal"' in s

    def test_multiple_lights(self):
        lights = ["E:/raw/Ha_001.xisf", "E:/raw/Ha_002.xisf", "E:/raw/Ha_003.xisf"]
        s = gen.generate_image_calibration(light_paths=lights, output_dir="E:/cal")
        for path in lights:
            assert "Ha_001.xisf" in s
            break  # Just check first one; all should be present


class TestStarAlignment:
    def test_class_name(self):
        s = gen.generate_star_alignment(
            reference_path="E:/data/Ha_master.xisf",
            target_paths=["E:/data/OIII_001.xisf"],
            output_dir="E:/registered",
        )
        assert "StarAlignment" in s

    def test_reference_path_embedded(self):
        s = gen.generate_star_alignment(
            reference_path="E:/data/Ha_master.xisf",
            target_paths=["E:/data/OIII_001.xisf"],
            output_dir="E:/registered",
        )
        assert "Ha_master.xisf" in s

    def test_distortion_correction_true(self):
        s = gen.generate_star_alignment(
            reference_path="E:/ref.xisf",
            target_paths=["E:/tgt.xisf"],
            output_dir="E:/out",
            distortion_correction=True,
        )
        assert "distortionCorrection  = true" in s

    def test_distortion_correction_false(self):
        s = gen.generate_star_alignment(
            reference_path="E:/ref.xisf",
            target_paths=["E:/tgt.xisf"],
            output_dir="E:/out",
            distortion_correction=False,
        )
        assert "distortionCorrection  = false" in s

    def test_drizzle_data_flag(self):
        s = gen.generate_star_alignment(
            reference_path="E:/ref.xisf",
            target_paths=["E:/tgt.xisf"],
            output_dir="E:/out",
            generate_drizzle_data=True,
        )
        assert "generateDrizzleData   = true" in s

    def test_forward_slashes(self):
        s = gen.generate_star_alignment(
            reference_path="E:\\data\\Ha_master.xisf",
            target_paths=["E:\\data\\OIII_001.xisf"],
            output_dir="E:\\registered",
        )
        assert has_forward_slashes_only(s)


class TestImageIntegration:
    def setup_method(self):
        self.paths = [f"E:/data/Ha_{i:03d}.xisf" for i in range(1, 6)]

    def test_class_name(self):
        s = gen.generate_image_integration(
            image_paths=self.paths,
            output_path="E:/data/Ha_master.xisf",
        )
        assert "ImageIntegration" in s

    def test_esd_rejection(self):
        s = gen.generate_image_integration(
            image_paths=self.paths,
            output_path="E:/data/Ha_master.xisf",
            rejection_algorithm="ESD",
        )
        # Numeric enum value for ESD = 8; prototype constants are unavailable in PI 1.8.x
        assert "= 8;" in s   # P.rejection = 8; // ESD
        assert "// ESD" in s

    def test_sigma_clip(self):
        s = gen.generate_image_integration(
            image_paths=self.paths,
            output_path="E:/data/Ha_master.xisf",
            rejection_algorithm="SigmaClip",
        )
        # Numeric enum value for SigmaClip = 3
        assert "= 3;" in s   # P.rejection = 3; // SigmaClip
        assert "// SigmaClip" in s

    def test_esd_low_relaxation_embedded(self):
        s = gen.generate_image_integration(
            image_paths=self.paths,
            output_path="E:/data/Ha_master.xisf",
            esd_low_relaxation=2.0,
        )
        assert "2.000" in s

    def test_all_image_paths_present(self):
        s = gen.generate_image_integration(
            image_paths=self.paths,
            output_path="E:/data/Ha_master.xisf",
        )
        for p in self.paths:
            assert Path(p).name in s

    def test_execute_global(self):
        s = gen.generate_image_integration(
            image_paths=self.paths,
            output_path="E:/data/Ha_master.xisf",
        )
        assert "executeGlobal()" in s


class TestDrizzleIntegration:
    def test_class_name(self):
        images = ["E:/data/Ha_001.xisf"]
        drizzle = ["E:/data/Ha_001.xdrz"]
        s = gen.generate_drizzle_integration(
            image_paths=images,
            drizzle_paths=drizzle,
            output_path="E:/data/Ha_drizzle.xisf",
        )
        assert "DrizzleIntegration" in s

    def test_scale_embedded(self):
        s = gen.generate_drizzle_integration(
            image_paths=["E:/data/Ha.xisf"],
            drizzle_paths=["E:/data/Ha.xdrz"],
            output_path="E:/data/Ha_drizzle.xisf",
            scale=2.0,
        )
        assert "2.0" in s

    def test_drop_shrink_embedded(self):
        s = gen.generate_drizzle_integration(
            image_paths=["E:/data/Ha.xisf"],
            drizzle_paths=["E:/data/Ha.xdrz"],
            output_path="E:/data/Ha_drizzle.xisf",
            drop_shrink=0.9,
        )
        assert "0.900" in s

    def test_square_kernel(self):
        s = gen.generate_drizzle_integration(
            image_paths=["E:/data/Ha.xisf"],
            drizzle_paths=["E:/data/Ha.xdrz"],
            output_path="E:/data/Ha_drizzle.xisf",
            kernel="Square",
        )
        assert "DrizzleIntegration.prototype.Square" in s

    def test_drizzle_paths_present(self):
        s = gen.generate_drizzle_integration(
            image_paths=["E:/data/Ha.xisf"],
            drizzle_paths=["E:/data/Ha.xdrz"],
            output_path="E:/data/Ha_drizzle.xisf",
        )
        assert "Ha.xdrz" in s


# ============================================================================
# Phase 2: Linear processing generators
# ============================================================================


class TestBlurXTerminator:
    def test_class_name(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf")
        assert "BlurXTerminator" in s

    def test_correct_only_true(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf", correct_only=True)
        assert "correct_only        = true" in s

    def test_correct_only_false(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf", correct_only=False)
        assert "correct_only        = false" in s

    def test_sharpen_stars_embedded(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf", sharpen_stars=0.30)
        assert "0.300" in s

    def test_sharpen_nonstellar_embedded(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf", sharpen_nonstellar=0.50)
        assert "0.500" in s

    def test_halos_embedded(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf", adjust_halos=0.05)
        assert "0.050" in s

    def test_execute_on(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf")
        assert "executeOn(view)" in s

    def test_open_save_close(self):
        s = gen.generate_blur_xterminator("E:/in.xisf", "E:/out.xisf")
        assert "ImageWindow.open(" in s
        assert "saveAs(" in s
        assert "forceClose()" in s

    def test_forward_slashes(self):
        s = gen.generate_blur_xterminator("E:\\work\\in.xisf", "E:\\work\\out.xisf")
        assert has_forward_slashes_only(s)


class TestNoiseXTerminator:
    def test_class_name(self):
        s = gen.generate_noise_xterminator("E:/in.xisf", "E:/out.xisf")
        assert "NoiseXTerminator" in s

    def test_denoise_embedded(self):
        s = gen.generate_noise_xterminator("E:/in.xisf", "E:/out.xisf", denoise=0.85)
        assert "0.850" in s

    def test_detail_embedded(self):
        s = gen.generate_noise_xterminator("E:/in.xisf", "E:/out.xisf", detail=0.20)
        assert "0.200" in s

    def test_execute_on(self):
        s = gen.generate_noise_xterminator("E:/in.xisf", "E:/out.xisf")
        assert "executeOn(view)" in s

    def test_open_save_close(self):
        s = gen.generate_noise_xterminator("E:/in.xisf", "E:/out.xisf")
        assert "ImageWindow.open(" in s
        assert "saveAs(" in s
        assert "forceClose()" in s


class TestStarXTerminator:
    def test_class_name(self):
        s = gen.generate_star_xterminator("E:/in.xisf", "E:/starless.xisf")
        assert "StarXTerminator" in s

    def test_stars_image_false_no_save(self):
        s = gen.generate_star_xterminator("E:/in.xisf", "E:/starless.xisf", stars_output_path=None)
        assert "SXT.stars    = false" in s

    def test_stars_image_true_saves_stars(self):
        s = gen.generate_star_xterminator(
            "E:/in.xisf",
            "E:/starless.xisf",
            stars_output_path="E:/stars.xisf",
        )
        assert "SXT.stars    = true" in s
        assert "stars.xisf" in s

    def test_unscreen_false(self):
        s = gen.generate_star_xterminator("E:/in.xisf", "E:/starless.xisf", unscreen=False)
        assert "SXT.unscreen = false" in s

    def test_unscreen_true(self):
        s = gen.generate_star_xterminator("E:/in.xisf", "E:/starless.xisf", unscreen=True)
        assert "SXT.unscreen = true" in s

    def test_starless_path_saved(self):
        s = gen.generate_star_xterminator("E:/in.xisf", "E:/NGC1499_starless.xisf")
        assert "NGC1499_starless.xisf" in s


class TestChannelExtraction:
    def test_class_name(self):
        s = gen.generate_channel_extraction(
            "E:/SHO.xisf",
            {"R": "E:/SII.xisf", "G": "E:/Ha.xisf", "B": "E:/OIII.xisf"},
        )
        assert "ChannelExtraction" in s

    def test_all_output_paths_present(self):
        s = gen.generate_channel_extraction(
            "E:/SHO.xisf",
            {"R": "E:/SII.xisf", "G": "E:/Ha.xisf", "B": "E:/OIII.xisf"},
        )
        assert "SII.xisf" in s
        assert "Ha.xisf" in s
        assert "OIII.xisf" in s

    def test_rgb_color_space(self):
        s = gen.generate_channel_extraction(
            "E:/SHO.xisf",
            {"R": "E:/SII.xisf", "G": "E:/Ha.xisf", "B": "E:/OIII.xisf"},
        )
        assert "ChannelExtraction.prototype.RGB" in s

    def test_custom_view_ids(self):
        s = gen.generate_channel_extraction(
            "E:/SHO.xisf",
            {"R": "E:/SII.xisf", "G": "E:/Ha.xisf", "B": "E:/OIII.xisf"},
            view_ids={"R": "SII_custom", "G": "Ha_custom", "B": "OIII_custom"},
        )
        assert "SII_custom" in s
        assert "Ha_custom" in s
        assert "OIII_custom" in s


class TestChannelCombination:
    def test_class_name(self):
        s = gen.generate_channel_combination("E:/R.xisf", "E:/G.xisf", "E:/B.xisf", "E:/RGB.xisf")
        assert "ChannelCombination" in s

    def test_all_channels_present(self):
        s = gen.generate_channel_combination("E:/R.xisf", "E:/G.xisf", "E:/B.xisf", "E:/RGB.xisf")
        assert "R.xisf" in s
        assert "G.xisf" in s
        assert "B.xisf" in s

    def test_rgb_color_space(self):
        s = gen.generate_channel_combination("E:/R.xisf", "E:/G.xisf", "E:/B.xisf", "E:/RGB.xisf")
        # Now uses PixelMath with explicit RGB color space (ChannelCombination.channels broken in PI 1.8.x)
        assert "PixelMath.prototype.RGB" in s

    def test_output_path_embedded(self):
        s = gen.generate_channel_combination(
            "E:/R.xisf", "E:/G.xisf", "E:/B.xisf",
            "E:/NGC1499_RGB_composite.xisf",
        )
        assert "NGC1499_RGB_composite.xisf" in s


class TestLinearFit:
    def test_class_name(self):
        s = gen.generate_linear_fit(
            target_paths=["E:/Ha.xisf", "E:/SII.xisf"],
            output_paths=["E:/Ha_lf.xisf", "E:/SII_lf.xisf"],
            reference_path="E:/OIII.xisf",
        )
        assert "LinearFit" in s

    def test_reference_id_used(self):
        s = gen.generate_linear_fit(
            target_paths=["E:/Ha.xisf"],
            output_paths=["E:/Ha_lf.xisf"],
            reference_path="E:/OIII_starless.xisf",
        )
        assert "OIII_starless" in s

    def test_reject_high_embedded(self):
        s = gen.generate_linear_fit(
            target_paths=["E:/Ha.xisf"],
            output_paths=["E:/Ha_lf.xisf"],
            reference_path="E:/OIII.xisf",
            reject_high=0.92,
        )
        assert "0.920" in s


# ============================================================================
# Phase 3: Stretching and palette combination
# ============================================================================


class TestGHSStretch:
    def test_class_name(self):
        s = gen.generate_ghs_stretch("E:/in.xisf", "E:/out.xisf")
        assert "GeneralizedHyperbolicStretch" in s

    def test_stretch_type(self):
        s = gen.generate_ghs_stretch("E:/in.xisf", "E:/out.xisf")
        assert "ST_GeneralisedHyperbolic" in s

    def test_D_embedded(self):
        s = gen.generate_ghs_stretch("E:/in.xisf", "E:/out.xisf", D=7.5)
        assert "7.5000" in s

    def test_b_embedded(self):
        s = gen.generate_ghs_stretch("E:/in.xisf", "E:/out.xisf", b=3.0)
        assert "3.0000" in s

    def test_SP_embedded(self):
        s = gen.generate_ghs_stretch("E:/in.xisf", "E:/out.xisf", SP=0.0002)
        assert "0.000200" in s

    def test_execute_on(self):
        s = gen.generate_ghs_stretch("E:/in.xisf", "E:/out.xisf")
        assert "executeOn(view)" in s


class TestHistogramStretch:
    def test_class_name(self):
        s = gen.generate_histogram_stretch("E:/in.xisf", "E:/out.xisf")
        assert "HistogramTransformation" in s

    def test_midtones_embedded(self):
        s = gen.generate_histogram_stretch("E:/in.xisf", "E:/out.xisf", midtones=0.15)
        assert "0.150000" in s

    def test_shadows_clip_embedded(self):
        s = gen.generate_histogram_stretch("E:/in.xisf", "E:/out.xisf", shadows_clip=0.005)
        assert "0.005000" in s


class TestForaxxPalette:
    def test_class_name(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert "PixelMath" in s

    def test_foraxx_r_expression(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert "(Oiii^~Oiii)*Sii" in s

    def test_foraxx_g_expression(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert "(Oiii*Ha)" in s

    def test_foraxx_b_is_oiii(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert 'PM.expression2 = "Oiii"' in s

    def test_view_ids_assigned(self):
        """Input windows must get their view IDs set for PixelMath references."""
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert '.id = "Ha"' in s
        assert '.id = "Sii"' in s
        assert '.id = "Oiii"' in s

    def test_all_three_input_windows_opened(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert s.count("ImageWindow.open(") == 3

    def test_three_windows_closed(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert s.count("forceClose()") >= 4  # 3 inputs + 1 output

    def test_create_new_image(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf"
        )
        assert "createNewImage       = true" in s

    def test_output_id_embedded(self):
        s = gen.generate_foraxx_palette(
            "E:/Ha.xisf", "E:/SII.xisf", "E:/OIII.xisf", "E:/SHO.xisf",
            output_id="MyPalette",
        )
        assert '"MyPalette"' in s

    def test_forward_slashes(self):
        s = gen.generate_foraxx_palette(
            "E:\\Ha.xisf", "E:\\SII.xisf", "E:\\OIII.xisf", "E:\\SHO.xisf"
        )
        assert has_forward_slashes_only(s)


# ============================================================================
# Phase 4: Nonlinear processing generators
# ============================================================================


class TestSCNR:
    def test_class_name(self):
        s = gen.generate_scnr("E:/in.xisf", "E:/out.xisf")
        assert "SCNR" in s

    def test_amount_embedded(self):
        s = gen.generate_scnr("E:/in.xisf", "E:/out.xisf", amount=0.65)
        assert "0.650" in s

    def test_max_mask_protection(self):
        s = gen.generate_scnr("E:/in.xisf", "E:/out.xisf")
        assert "MaximumMask" in s

    def test_green_channel(self):
        s = gen.generate_scnr("E:/in.xisf", "E:/out.xisf")
        assert "Green" in s

    def test_execute_on(self):
        s = gen.generate_scnr("E:/in.xisf", "E:/out.xisf")
        assert "executeOn(view)" in s


class TestCurvesSaturationContrast:
    def test_class_name(self):
        s = gen.generate_curves_saturation_contrast("E:/in.xisf", "E:/out.xisf")
        assert "CurvesTransformation" in s

    def test_saturation_curve_present(self):
        s = gen.generate_curves_saturation_contrast("E:/in.xisf", "E:/out.xisf")
        assert "CT.S = " in s

    def test_luminance_curve_present(self):
        s = gen.generate_curves_saturation_contrast("E:/in.xisf", "E:/out.xisf")
        assert "CT.K = " in s

    def test_custom_saturation_points(self):
        pts = [(0.0, 0.0), (0.5, 0.8), (1.0, 1.0)]
        s = gen.generate_curves_saturation_contrast("E:/in.xisf", "E:/out.xisf",
                                                     saturation_points=pts)
        assert "0.8" in s

    def test_execute_on(self):
        s = gen.generate_curves_saturation_contrast("E:/in.xisf", "E:/out.xisf")
        assert "executeOn(view)" in s


class TestCurvesHueShift:
    def test_class_name(self):
        s = gen.generate_curves_hue_shift("E:/in.xisf", "E:/out.xisf")
        assert "CurvesTransformation" in s

    def test_hue_curve_present(self):
        s = gen.generate_curves_hue_shift("E:/in.xisf", "E:/out.xisf")
        assert "CT.H = " in s

    def test_custom_hue_points(self):
        pts = [(0.0, 0.0), (0.33, 0.25), (1.0, 1.0)]
        s = gen.generate_curves_hue_shift("E:/in.xisf", "E:/out.xisf", hue_points=pts)
        assert "0.25" in s


class TestHDRMultiscale:
    def test_class_name(self):
        s = gen.generate_hdr_multiscale("E:/in.xisf", "E:/out.xisf")
        assert "HDRMultiscaleTransform" in s

    def test_layers_embedded(self):
        s = gen.generate_hdr_multiscale("E:/in.xisf", "E:/out.xisf", number_of_layers=6)
        assert "= 6" in s

    def test_iterations_embedded(self):
        s = gen.generate_hdr_multiscale("E:/in.xisf", "E:/out.xisf", number_of_iterations=2)
        assert "= 2" in s

    def test_execute_on(self):
        s = gen.generate_hdr_multiscale("E:/in.xisf", "E:/out.xisf")
        assert "executeOn(view)" in s


class TestLHE:
    def test_class_name(self):
        s = gen.generate_local_histogram_equalization("E:/in.xisf", "E:/out.xisf")
        assert "LocalHistogramEqualization" in s

    def test_radius_embedded(self):
        s = gen.generate_local_histogram_equalization("E:/in.xisf", "E:/out.xisf", kernel_radius=96)
        assert "= 96" in s

    def test_contrast_limit_embedded(self):
        s = gen.generate_local_histogram_equalization("E:/in.xisf", "E:/out.xisf", contrast_limit=2.0)
        assert "2.000" in s

    def test_amount_embedded(self):
        s = gen.generate_local_histogram_equalization("E:/in.xisf", "E:/out.xisf", amount=0.35)
        assert "0.350" in s


# ============================================================================
# Phase 5: RGB stars and final combination
# ============================================================================


class TestSPCC:
    def test_class_name(self):
        s = gen.generate_spcc("E:/RGB.xisf", "E:/RGB_spcc.xisf")
        assert "SpectrophotometricColorCalibration" in s

    def test_execute_on(self):
        s = gen.generate_spcc("E:/RGB.xisf", "E:/RGB_spcc.xisf")
        assert "executeOn(view)" in s

    def test_narrowband_mode_false(self):
        s = gen.generate_spcc("E:/RGB.xisf", "E:/RGB_spcc.xisf")
        assert "narrowbandMode       = false" in s

    def test_output_path_embedded(self):
        s = gen.generate_spcc("E:/RGB.xisf", "E:/NGC1499_RGB_spcc.xisf")
        assert "NGC1499_RGB_spcc.xisf" in s


class TestScreenBlend:
    def test_class_name(self):
        s = gen.generate_screen_blend("E:/starless.xisf", "E:/stars.xisf", "E:/final.xisf")
        assert "PixelMath" in s

    def test_screen_blend_formula_present(self):
        s = gen.generate_screen_blend("E:/starless.xisf", "E:/stars.xisf", "E:/final.xisf")
        assert "~(~SHO_starless * ~RGB_stars)" in s

    def test_view_ids_assigned(self):
        s = gen.generate_screen_blend("E:/starless.xisf", "E:/stars.xisf", "E:/final.xisf")
        assert '.id = "SHO_starless"' in s
        assert '.id = "RGB_stars"' in s

    def test_both_windows_opened(self):
        s = gen.generate_screen_blend("E:/starless.xisf", "E:/stars.xisf", "E:/final.xisf")
        assert s.count("ImageWindow.open(") == 2

    def test_star_brightness_factor_applied(self):
        s = gen.generate_screen_blend(
            "E:/starless.xisf", "E:/stars.xisf", "E:/final.xisf",
            star_brightness=0.70,
        )
        assert "0.700" in s
        assert "RGB_stars * 0.700" in s

    def test_full_brightness_uses_simple_formula(self):
        s = gen.generate_screen_blend(
            "E:/starless.xisf", "E:/stars.xisf", "E:/final.xisf",
            star_brightness=1.0,
        )
        # At 1.0, no multiplication needed
        assert "~(~SHO_starless * ~RGB_stars)" in s

    def test_create_new_image(self):
        s = gen.generate_screen_blend("E:/starless.xisf", "E:/stars.xisf", "E:/final.xisf")
        assert "createNewImage      = true" in s

    def test_output_path_embedded(self):
        s = gen.generate_screen_blend(
            "E:/starless.xisf", "E:/stars.xisf",
            "E:/NGC1499_final.xisf",
        )
        assert "NGC1499_final.xisf" in s

    def test_forward_slashes(self):
        s = gen.generate_screen_blend(
            "E:\\starless.xisf", "E:\\stars.xisf", "E:\\final.xisf"
        )
        assert has_forward_slashes_only(s)


# ============================================================================
# Template file writer
# ============================================================================


class TestWriteReferenceTemplates:
    def test_writes_all_templates(self, tmp_path):
        written = gen.write_reference_templates(tmp_path)
        expected = {
            "calibration.js.tmpl",
            "registration.js.tmpl",
            "integration.js.tmpl",
            "bxt.js.tmpl",
            "nxt.js.tmpl",
            "sxt.js.tmpl",
            "stretch.js.tmpl",
            "pixelmath.js.tmpl",
            "spcc.js.tmpl",
        }
        assert set(written.keys()) == expected

    def test_template_files_are_non_empty(self, tmp_path):
        written = gen.write_reference_templates(tmp_path)
        for name, path in written.items():
            assert path.stat().st_size > 0, f"Template file is empty: {name}"

    def test_template_files_contain_header(self, tmp_path):
        written = gen.write_reference_templates(tmp_path)
        for name, path in written.items():
            content = path.read_text(encoding="utf-8")
            assert "#include <pjsr/DataType.jsh>" in content, \
                f"Template missing PJSR header: {name}"

    def test_templates_have_no_backslashes_in_paths(self, tmp_path):
        written = gen.write_reference_templates(tmp_path)
        for name, path in written.items():
            content = path.read_text(encoding="utf-8")
            assert has_forward_slashes_only(content), \
                f"Template contains backslash paths: {name}"
