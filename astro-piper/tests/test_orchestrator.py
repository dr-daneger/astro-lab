"""
tests/test_orchestrator.py — Unit tests for PipelineOrchestrator

Run with: python -m pytest tests/test_orchestrator.py -v
"""

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import (
    PipelineError,
    PipelineOrchestrator,
    PipelineStage,
    StructuredLogger,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


@dataclass
class PassStage(PipelineStage):
    """A stage that always succeeds instantly."""
    _executed: bool = field(default=False, init=False, repr=False)

    def execute(self, config: dict) -> int:
        self._executed = True
        return 0


@dataclass
class FailStage(PipelineStage):
    """A stage that always returns a non-zero exit code."""
    def execute(self, config: dict) -> int:
        return 1


@dataclass
class RaisingStage(PipelineStage):
    """A stage that raises an exception."""
    def execute(self, config: dict) -> int:
        raise RuntimeError("Simulated stage crash")


def _make_orchestrator(stages, config=None, log_dir=None):
    config = config or {"directories": {"working": "/tmp"}, "breakpoints": {}}
    if log_dir is None:
        log_dir = Path(tempfile.mkdtemp())
    return PipelineOrchestrator(config, stages, log_dir)


# ── Idempotency ────────────────────────────────────────────────────────────────


def test_stage_skipped_when_complete(tmp_path):
    """A stage with all outputs present should be skipped."""
    out_file = tmp_path / "output.xisf"
    out_file.touch()

    stage = PassStage(
        name="Test Stage",
        phase=1, track="nb",
        output_spec=[str(out_file)],
    )
    orch = _make_orchestrator([stage])
    orch.run()
    assert not stage._executed, "Stage should have been skipped (output exists)"


def test_stage_runs_when_output_missing(tmp_path):
    """A stage whose output is absent should execute."""
    out_file = tmp_path / "output.xisf"
    # Don't create out_file — it doesn't exist yet

    stage = PassStage(
        name="Test Stage",
        phase=1, track="nb",
        output_spec=[str(out_file)],
    )
    # Override validate_outputs so the test doesn't fail on missing output
    stage.validate_outputs = lambda: True

    orch = _make_orchestrator([stage])
    orch.run()
    assert stage._executed, "Stage should have executed"


def test_force_reruns_complete_stage(tmp_path):
    """--force should re-run even if outputs exist."""
    out_file = tmp_path / "output.xisf"
    out_file.touch()

    stage = PassStage(
        name="Test Stage",
        phase=1, track="nb",
        output_spec=[str(out_file)],
    )
    orch = _make_orchestrator([stage])
    orch.run(force=True)
    assert stage._executed, "Stage should have executed despite output existing"


# ── Error propagation ──────────────────────────────────────────────────────────


def test_pipeline_error_on_nonzero_exit():
    stage = FailStage(name="Failing Stage", phase=1, track="nb")
    stage.validate_inputs = lambda: True

    orch = _make_orchestrator([stage])
    with pytest.raises(PipelineError, match="Failing Stage"):
        orch.run()


def test_pipeline_error_on_missing_inputs(tmp_path):
    stage = PassStage(
        name="Test Stage",
        phase=1, track="nb",
        input_spec=[str(tmp_path / "nonexistent_input.xisf")],
    )
    orch = _make_orchestrator([stage])
    with pytest.raises(PipelineError, match="missing input"):
        orch.run()


def test_exception_propagated():
    stage = RaisingStage(name="Crash Stage", phase=1, track="nb")
    stage.validate_inputs = lambda: True

    orch = _make_orchestrator([stage])
    with pytest.raises(RuntimeError, match="Simulated stage crash"):
        orch.run()


# ── Stage sequencing ───────────────────────────────────────────────────────────


def test_start_stage_skips_preceding():
    """--start-stage should skip all stages before the named one."""
    first = PassStage(name="First Stage", phase=1, track="nb")
    first.validate_outputs = lambda: True

    second = PassStage(name="Second Stage", phase=1, track="nb")
    second.validate_outputs = lambda: True

    orch = _make_orchestrator([first, second])
    orch.run(start_stage="Second Stage")

    assert not first._executed, "First stage should have been skipped"
    assert second._executed, "Second stage should have executed"


def test_dry_run_does_not_execute():
    first = PassStage(name="First Stage", phase=1, track="nb")
    second = PassStage(name="Second Stage", phase=2, track="nb")

    orch = _make_orchestrator([first, second])
    orch.run(dry_run=True)

    assert not first._executed
    assert not second._executed


# ── StructuredLogger ───────────────────────────────────────────────────────────


def test_logger_writes_json(tmp_path):
    logger = StructuredLogger(tmp_path)
    stage = PassStage(name="Log Test Stage", phase=2, track="nb")
    logger.begin_stage(stage)
    stage.execute({})
    logger.end_stage(stage, 0)

    log_files = list(tmp_path.glob("Log_Test_Stage_*.json"))
    assert len(log_files) == 1

    import json
    data = json.loads(log_files[0].read_text())
    assert data["stage_name"] == "Log Test Stage"
    assert data["exit_code"] == 0
    assert "wall_clock_seconds" in data
    assert "start_time" in data
    assert "end_time" in data


def test_logger_hashes_existing_outputs(tmp_path):
    out_file = tmp_path / "result.xisf"
    out_file.write_bytes(b"fake xisf data")

    logger = StructuredLogger(tmp_path)
    stage = PassStage(
        name="Hash Test Stage",
        phase=1, track="nb",
        output_spec=[str(out_file)],
    )
    logger.begin_stage(stage)
    logger.end_stage(stage, 0)

    import json
    log_files = list(tmp_path.glob("Hash_Test_Stage_*.json"))
    data = json.loads(log_files[0].read_text())
    hashes = data["output_file_hashes"]
    assert str(out_file) in hashes
    assert hashes[str(out_file)] is not None
    assert len(hashes[str(out_file)]) == 64  # SHA-256 hex length


def test_logger_null_hash_for_missing_output(tmp_path):
    missing = tmp_path / "missing.xisf"

    logger = StructuredLogger(tmp_path)
    stage = PassStage(
        name="Missing Hash Stage",
        phase=1, track="nb",
        output_spec=[str(missing)],
    )
    logger.begin_stage(stage)
    logger.end_stage(stage, 0)

    import json
    log_files = list(tmp_path.glob("Missing_Hash_Stage_*.json"))
    data = json.loads(log_files[0].read_text())
    assert data["output_file_hashes"][str(missing)] is None
