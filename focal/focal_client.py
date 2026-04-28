from __future__ import annotations

import argparse
import io
import itertools
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

try:
    import PyIndi  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - dependency gate for local editing
    PyIndi = None  # type: ignore[assignment]
    PYINDI_IMPORT_ERROR = exc
else:
    PYINDI_IMPORT_ERROR = None

try:
    from astropy.io import fits
except ImportError as exc:  # pragma: no cover - dependency gate for local editing
    fits = None  # type: ignore[assignment]
    ASTROPY_IMPORT_ERROR = exc
else:
    ASTROPY_IMPORT_ERROR = None


class FocalError(RuntimeError):
    pass


class ConfigError(FocalError):
    pass


class IndiOperationError(FocalError):
    pass


class _PyIndiBaseClient(PyIndi.BaseClient if PyIndi is not None else object):  # type: ignore[misc]
    pass


@dataclass(frozen=True)
class NumberPropertyConfig:
    name: str
    element: str


@dataclass(frozen=True)
class CoolerPropertyConfig:
    name: str
    on_element: str


@dataclass(frozen=True)
class FitsHeaderConfig:
    gain: str
    egain: str | None
    offset: str
    filter: str
    xpixsz: str
    ypixsz: str
    default_filter_label: str = "NONE"


@dataclass(frozen=True)
class IndiConfig:
    host: str
    port: int
    connect_timeout_s: float
    property_timeout_s: float


@dataclass(frozen=True)
class PathsConfig:
    buffer_root: Path
    log_file: Path


@dataclass(frozen=True)
class EquipmentConfig:
    camera: str
    mount: str | None = None
    focuser: str | None = None
    filter_wheel: str | None = None


@dataclass(frozen=True)
class CameraConfig:
    bin_x: int
    bin_y: int
    pixel_size_x_um: float
    pixel_size_y_um: float
    offsets_by_gain: dict[int, int]
    egain_by_gain: dict[int, float]
    blob_property: str
    exposure: NumberPropertyConfig
    gain: NumberPropertyConfig
    offset: NumberPropertyConfig
    temperature: NumberPropertyConfig
    cooler: CoolerPropertyConfig | None
    fits_headers: FitsHeaderConfig


@dataclass(frozen=True)
class FilterWheelConfig:
    slot: NumberPropertyConfig
    filter_map: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class TelemetryConfig:
    poll_interval_s: float
    temperature_tolerance_c: float
    stable_samples: int
    stabilization_timeout_s: float


@dataclass(frozen=True)
class ExecutionConfig:
    skip_existing: bool
    filter_settle_s: float
    exposure_timeout_padding_s: float


@dataclass(frozen=True)
class FrameSetConfig:
    enabled: bool
    use_filters: bool
    exposures_s: tuple[float, ...]
    frames_per_cell: int


@dataclass(frozen=True)
class DoeConfig:
    temperatures_c: tuple[float, ...]
    gains: tuple[int, ...]
    filters: tuple[str, ...]
    frame_sets: dict[str, FrameSetConfig]


@dataclass(frozen=True)
class TransferConfig:
    remote_windows_share: str
    remote_mount: str
    target_subdir: str
    rsync_script: str
    rsync_interval_s: float


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    run_label: str


@dataclass(frozen=True)
class FocalConfig:
    project: ProjectConfig
    indi: IndiConfig
    paths: PathsConfig
    equipment: EquipmentConfig
    camera: CameraConfig
    filter_wheel: FilterWheelConfig | None
    telemetry: TelemetryConfig
    execution: ExecutionConfig
    doe: DoeConfig
    transfer: TransferConfig


@dataclass(frozen=True)
class CaptureTask:
    frame_type: str
    temperature_c: float
    gain: int
    filter_name: str | None
    exposure_s: float
    frame_index: int


@dataclass
class PendingCapture:
    task: CaptureTask
    destination: Path
    header_updates: dict[str, Any]
    completion_event: threading.Event = field(default_factory=threading.Event)
    result_path: Path | None = None
    error: BaseException | None = None


@dataclass
class TemperatureSnapshot:
    current_c: float | None = None
    target_c: float | None = None
    stable: bool = False
    stable_streak: int = 0
    sampled_at: datetime | None = None


def require_runtime_dependencies() -> None:
    missing: list[str] = []
    if PYINDI_IMPORT_ERROR is not None:
        missing.append(f"PyIndi import failed: {PYINDI_IMPORT_ERROR}")
    if ASTROPY_IMPORT_ERROR is not None:
        missing.append(f"astropy import failed: {ASTROPY_IMPORT_ERROR}")
    if missing:
        raise FocalError("Missing runtime dependencies. Install requirements.txt first. " + " | ".join(missing))


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"Expected mapping for {label}")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"Expected list for {label}")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Expected non-empty string for {label}")
    return value


def _number_property(payload: dict[str, Any], label: str) -> NumberPropertyConfig:
    return NumberPropertyConfig(
        name=_require_str(payload.get("name"), f"{label}.name"),
        element=_require_str(payload.get("element"), f"{label}.element"),
    )


def load_config(config_path: Path) -> FocalConfig:
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ConfigError("Top-level config must be a mapping")

    project_raw = _require_mapping(raw.get("project"), "project")
    indi_raw = _require_mapping(raw.get("indi"), "indi")
    paths_raw = _require_mapping(raw.get("paths"), "paths")
    equipment_raw = _require_mapping(raw.get("equipment"), "equipment")
    camera_raw = _require_mapping(raw.get("camera"), "camera")
    telemetry_raw = _require_mapping(raw.get("telemetry"), "telemetry")
    execution_raw = _require_mapping(raw.get("execution"), "execution")
    doe_raw = _require_mapping(raw.get("doe"), "doe")
    transfer_raw = _require_mapping(raw.get("transfer"), "transfer")

    binning_raw = _require_mapping(camera_raw.get("binning"), "camera.binning")
    pixel_size_raw = _require_mapping(camera_raw.get("pixel_size_um"), "camera.pixel_size_um")
    properties_raw = _require_mapping(camera_raw.get("properties"), "camera.properties")
    fits_headers_raw = _require_mapping(camera_raw.get("fits_header_keys"), "camera.fits_header_keys")
    offsets_raw = _require_mapping(camera_raw.get("offsets_by_gain"), "camera.offsets_by_gain")
    egain_raw = _require_mapping(camera_raw.get("egain_by_gain", {}), "camera.egain_by_gain")

    cooler_payload = properties_raw.get("cooler")
    cooler = None
    if cooler_payload is not None:
        cooler_mapping = _require_mapping(cooler_payload, "camera.properties.cooler")
        cooler = CoolerPropertyConfig(
            name=_require_str(cooler_mapping.get("name"), "camera.properties.cooler.name"),
            on_element=_require_str(
                cooler_mapping.get("on_element"),
                "camera.properties.cooler.on_element",
            ),
        )

    filter_wheel = None
    filter_wheel_raw = raw.get("filter_wheel")
    if filter_wheel_raw is not None:
        fw_mapping = _require_mapping(filter_wheel_raw, "filter_wheel")
        fw_properties = _require_mapping(fw_mapping.get("properties"), "filter_wheel.properties")
        fw_filter_map = _require_mapping(fw_mapping.get("filter_map", {}), "filter_wheel.filter_map")
        filter_wheel = FilterWheelConfig(
            slot=_number_property(_require_mapping(fw_properties.get("slot"), "filter_wheel.properties.slot"), "filter_wheel.properties.slot"),
            filter_map={_require_str(name, "filter_wheel.filter_map key"): int(slot) for name, slot in fw_filter_map.items()},
        )

    frame_sets_raw = _require_mapping(doe_raw.get("frame_sets"), "doe.frame_sets")
    frame_sets: dict[str, FrameSetConfig] = {}
    for frame_name, payload in frame_sets_raw.items():
        frame_mapping = _require_mapping(payload, f"doe.frame_sets.{frame_name}")
        frame_sets[str(frame_name)] = FrameSetConfig(
            enabled=bool(frame_mapping.get("enabled", True)),
            use_filters=bool(frame_mapping.get("use_filters", True)),
            exposures_s=tuple(float(value) for value in _require_list(frame_mapping.get("exposures_s"), f"doe.frame_sets.{frame_name}.exposures_s")),
            frames_per_cell=int(frame_mapping.get("frames_per_cell", 1)),
        )

    if not frame_sets:
        raise ConfigError("At least one frame set must be defined")

    return FocalConfig(
        project=ProjectConfig(
            name=_require_str(project_raw.get("name"), "project.name"),
            run_label=_require_str(project_raw.get("run_label"), "project.run_label"),
        ),
        indi=IndiConfig(
            host=_require_str(indi_raw.get("host"), "indi.host"),
            port=int(indi_raw.get("port", 7624)),
            connect_timeout_s=float(indi_raw.get("connect_timeout_s", 15)),
            property_timeout_s=float(indi_raw.get("property_timeout_s", 15)),
        ),
        paths=PathsConfig(
            buffer_root=Path(_require_str(paths_raw.get("buffer_root"), "paths.buffer_root")),
            log_file=Path(_require_str(paths_raw.get("log_file"), "paths.log_file")),
        ),
        equipment=EquipmentConfig(
            camera=_require_str(equipment_raw.get("camera"), "equipment.camera"),
            mount=equipment_raw.get("mount"),
            focuser=equipment_raw.get("focuser"),
            filter_wheel=equipment_raw.get("filter_wheel"),
        ),
        camera=CameraConfig(
            bin_x=int(binning_raw.get("x", 1)),
            bin_y=int(binning_raw.get("y", 1)),
            pixel_size_x_um=float(pixel_size_raw.get("x")),
            pixel_size_y_um=float(pixel_size_raw.get("y")),
            offsets_by_gain={int(gain): int(offset) for gain, offset in offsets_raw.items()},
            egain_by_gain={int(gain): float(value) for gain, value in egain_raw.items()},
            blob_property=_require_str(properties_raw.get("blob"), "camera.properties.blob"),
            exposure=_number_property(_require_mapping(properties_raw.get("exposure"), "camera.properties.exposure"), "camera.properties.exposure"),
            gain=_number_property(_require_mapping(properties_raw.get("gain"), "camera.properties.gain"), "camera.properties.gain"),
            offset=_number_property(_require_mapping(properties_raw.get("offset"), "camera.properties.offset"), "camera.properties.offset"),
            temperature=_number_property(_require_mapping(properties_raw.get("temperature"), "camera.properties.temperature"), "camera.properties.temperature"),
            cooler=cooler,
            fits_headers=FitsHeaderConfig(
                gain=_require_str(fits_headers_raw.get("gain"), "camera.fits_header_keys.gain"),
                egain=fits_headers_raw.get("egain"),
                offset=_require_str(fits_headers_raw.get("offset"), "camera.fits_header_keys.offset"),
                filter=_require_str(fits_headers_raw.get("filter"), "camera.fits_header_keys.filter"),
                xpixsz=_require_str(fits_headers_raw.get("xpixsz"), "camera.fits_header_keys.xpixsz"),
                ypixsz=_require_str(fits_headers_raw.get("ypixsz"), "camera.fits_header_keys.ypixsz"),
                default_filter_label=str(fits_headers_raw.get("default_filter_label", "NONE")),
            ),
        ),
        filter_wheel=filter_wheel,
        telemetry=TelemetryConfig(
            poll_interval_s=float(telemetry_raw.get("poll_interval_s", 10)),
            temperature_tolerance_c=float(telemetry_raw.get("temperature_tolerance_c", 0.2)),
            stable_samples=int(telemetry_raw.get("stable_samples", 3)),
            stabilization_timeout_s=float(telemetry_raw.get("stabilization_timeout_s", 1800)),
        ),
        execution=ExecutionConfig(
            skip_existing=bool(execution_raw.get("skip_existing", True)),
            filter_settle_s=float(execution_raw.get("filter_settle_s", 2)),
            exposure_timeout_padding_s=float(execution_raw.get("exposure_timeout_padding_s", 30)),
        ),
        doe=DoeConfig(
            temperatures_c=tuple(float(value) for value in _require_list(doe_raw.get("temperatures_c"), "doe.temperatures_c")),
            gains=tuple(int(value) for value in _require_list(doe_raw.get("gains"), "doe.gains")),
            filters=tuple(str(value) for value in _require_list(doe_raw.get("filters"), "doe.filters")),
            frame_sets=frame_sets,
        ),
        transfer=TransferConfig(
            remote_windows_share=_require_str(transfer_raw.get("remote_windows_share"), "transfer.remote_windows_share"),
            remote_mount=_require_str(transfer_raw.get("remote_mount"), "transfer.remote_mount"),
            target_subdir=_require_str(transfer_raw.get("target_subdir"), "transfer.target_subdir"),
            rsync_script=_require_str(transfer_raw.get("rsync_script"), "transfer.rsync_script"),
            rsync_interval_s=float(transfer_raw.get("rsync_interval_s", 30)),
        ),
    )


def configure_logging(log_file: Path, level: str) -> logging.Logger:
    logger = logging.getLogger("focal")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(threadName)s | %(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger


def _indi_call(obj: Any, *candidates: str, default: Any = None) -> Any:
    for candidate in candidates:
        method = getattr(obj, candidate, None)
        if callable(method):
            return method()
        if method is not None:
            return method
    return default


def _slug(value: str) -> str:
    return value.replace(" ", "_").replace("/", "-")


def _temp_token(value: float) -> str:
    return f"{value:+05.1f}".replace("+", "p").replace("-", "m").replace(".", "p")


def _exp_token(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".").replace(".", "p")


def iter_capture_tasks(config: FocalConfig) -> Iterable[CaptureTask]:
    for temperature_c in config.doe.temperatures_c:
        for gain in config.doe.gains:
            for frame_type, frame_config in config.doe.frame_sets.items():
                if not frame_config.enabled:
                    continue
                filter_values = config.doe.filters if frame_config.use_filters else (None,)
                for filter_name, exposure_s in itertools.product(filter_values, frame_config.exposures_s):
                    for frame_index in range(1, frame_config.frames_per_cell + 1):
                        yield CaptureTask(
                            frame_type=frame_type,
                            temperature_c=temperature_c,
                            gain=gain,
                            filter_name=filter_name,
                            exposure_s=exposure_s,
                            frame_index=frame_index,
                        )


def summarize_matrix(config: FocalConfig) -> dict[str, Any]:
    total_frames = 0
    frame_types: dict[str, int] = {}
    for frame_type, frame_config in config.doe.frame_sets.items():
        if not frame_config.enabled:
            continue
        filter_count = len(config.doe.filters) if frame_config.use_filters else 1
        count = (
            len(config.doe.temperatures_c)
            * len(config.doe.gains)
            * filter_count
            * len(frame_config.exposures_s)
            * frame_config.frames_per_cell
        )
        frame_types[frame_type] = count
        total_frames += count
    return {
        "temperatures": list(config.doe.temperatures_c),
        "gains": list(config.doe.gains),
        "filters": list(config.doe.filters),
        "frame_counts": frame_types,
        "total_frames": total_frames,
    }


class FocalIndiClient(_PyIndiBaseClient):
    def __init__(self, config: FocalConfig, logger: logging.Logger) -> None:
        super().__init__()
        self.config = config
        self.logger = logger
        self.stop_event = threading.Event()
        self.writer_queue: queue.Queue[PendingCapture] = queue.Queue()
        self.writer_thread = threading.Thread(target=self._blob_writer_loop, name="blob-writer", daemon=True)
        self.telemetry_thread = threading.Thread(target=self._telemetry_loop, name="telemetry", daemon=True)
        self.pending_lock = threading.Lock()
        self.pending_capture: PendingCapture | None = None
        self.temperature_lock = threading.Lock()
        self.temperature_snapshot = TemperatureSnapshot()

    def newDevice(self, device: Any) -> None:  # noqa: N802
        self.logger.info("INDI device discovered: %s", _indi_call(device, "getDeviceName", "getName", default="unknown"))

    def newProperty(self, prop: Any) -> None:  # noqa: N802
        self.logger.debug(
            "INDI property available: %s.%s",
            _indi_call(prop, "getDeviceName", default="unknown"),
            _indi_call(prop, "getName", default="unknown"),
        )

    def newMessage(self, device: Any, message_id: int) -> None:  # noqa: N802
        self.logger.debug("INDI message from %s id=%s", _indi_call(device, "getDeviceName", "getName", default="unknown"), message_id)

    def newBLOB(self, blob: Any) -> None:  # noqa: N802
        with self.pending_lock:
            pending = self.pending_capture
            if pending is None:
                self.logger.warning("Received unexpected BLOB with no pending capture")
                return
            self.pending_capture = None

        blob_data = _indi_call(blob, "getblobdata", default=None)
        if blob_data is None:
            pending.error = IndiOperationError("BLOB callback returned no payload")
            pending.completion_event.set()
            return

        payload = bytes(blob_data)
        suffix = str(_indi_call(blob, "getformat", "getFormat", default=".fits"))
        if suffix and not pending.destination.suffix:
            pending.destination = pending.destination.with_suffix(suffix)

        pending.header_updates.setdefault("_blob_payload", payload)
        self.writer_queue.put(pending)

    def initialize_hardware(self) -> None:
        self.logger.info("Connecting to INDI server %s:%s", self.config.indi.host, self.config.indi.port)
        connected = self.setServer(self.config.indi.host, self.config.indi.port)
        if connected is False:
            raise IndiOperationError("Failed to set INDI server host/port")
        if not self.connectServer():
            raise IndiOperationError("Failed to connect to INDI server")

        for device_name in (
            self.config.equipment.camera,
            self.config.equipment.mount,
            self.config.equipment.focuser,
            self.config.equipment.filter_wheel,
        ):
            if device_name:
                self._connect_device(device_name)

        self._enable_blobs()
        self._ensure_cooler_enabled()
        self.writer_thread.start()
        self.telemetry_thread.start()
        self.logger.info("Hardware initialization complete")

    def shutdown(self) -> None:
        self.stop_event.set()
        if self.pending_capture is not None:
            self.pending_capture.error = IndiOperationError("Shutdown interrupted pending capture")
            self.pending_capture.completion_event.set()
        if self.writer_thread.is_alive():
            self.writer_thread.join(timeout=5)
        if self.telemetry_thread.is_alive():
            self.telemetry_thread.join(timeout=5)
        disconnect = getattr(self, "disconnectServer", None)
        if callable(disconnect):
            disconnect()

    def execute_doe(self) -> None:
        self.logger.info("Starting DoE run %s", self.config.project.run_label)
        for temperature_c in self.config.doe.temperatures_c:
            self._set_target_temperature(temperature_c)
            self.await_temperature_stability(temperature_c)
            for gain in self.config.doe.gains:
                self._set_gain_and_offset(gain)
                for frame_type, frame_config in self.config.doe.frame_sets.items():
                    if not frame_config.enabled:
                        continue
                    filter_values = self.config.doe.filters if frame_config.use_filters else (None,)
                    for filter_name in filter_values:
                        self._set_filter(filter_name)
                        for exposure_s in frame_config.exposures_s:
                            for frame_index in range(1, frame_config.frames_per_cell + 1):
                                task = CaptureTask(
                                    frame_type=frame_type,
                                    temperature_c=temperature_c,
                                    gain=gain,
                                    filter_name=filter_name,
                                    exposure_s=exposure_s,
                                    frame_index=frame_index,
                                )
                                path = self.capture_task(task)
                                self.logger.info("Completed %s", path)

    def capture_task(self, task: CaptureTask) -> Path:
        destination = self._build_destination(task)
        if destination.exists() and self.config.execution.skip_existing:
            self.logger.info("Skipping existing frame %s", destination)
            return destination

        actual_gain = int(round(self._read_number_value(self.config.equipment.camera, self.config.camera.gain)))
        actual_offset = int(round(self._read_number_value(self.config.equipment.camera, self.config.camera.offset)))
        filter_name = self._current_filter_name(task.filter_name)
        header_updates = {
            self.config.camera.fits_headers.gain: actual_gain,
            self.config.camera.fits_headers.offset: actual_offset,
            self.config.camera.fits_headers.filter: filter_name,
            self.config.camera.fits_headers.xpixsz: self.config.camera.pixel_size_x_um,
            self.config.camera.fits_headers.ypixsz: self.config.camera.pixel_size_y_um,
            "FRAME": task.frame_type.upper(),
            "EXPTIME": task.exposure_s,
            "SETTEMP": task.temperature_c,
            "XBINNING": self.config.camera.bin_x,
            "YBINNING": self.config.camera.bin_y,
        }
        if self.config.camera.fits_headers.egain and task.gain in self.config.camera.egain_by_gain:
            header_updates[self.config.camera.fits_headers.egain] = self.config.camera.egain_by_gain[task.gain]

        pending = PendingCapture(task=task, destination=destination, header_updates=header_updates)
        with self.pending_lock:
            if self.pending_capture is not None:
                raise IndiOperationError("Attempted to start a new capture before the previous BLOB arrived")
            self.pending_capture = pending

        self.logger.info(
            "Starting exposure frame=%s temp=%s gain=%s filter=%s exp=%ss index=%s",
            task.frame_type,
            task.temperature_c,
            task.gain,
            filter_name,
            task.exposure_s,
            task.frame_index,
        )
        self._set_number_value(self.config.equipment.camera, self.config.camera.exposure, task.exposure_s)
        if not pending.completion_event.wait(task.exposure_s + self.config.execution.exposure_timeout_padding_s):
            with self.pending_lock:
                self.pending_capture = None
            raise IndiOperationError(f"Timed out waiting for BLOB delivery for {destination.name}")
        if pending.error is not None:
            raise IndiOperationError(str(pending.error)) from pending.error
        if pending.result_path is None:
            raise IndiOperationError("Capture completed without a file path")
        return pending.result_path

    def await_temperature_stability(self, target_c: float) -> None:
        deadline = time.monotonic() + self.config.telemetry.stabilization_timeout_s
        self.logger.info("Waiting for thermal stabilization at %.2fC", target_c)
        while time.monotonic() < deadline:
            with self.temperature_lock:
                snapshot = TemperatureSnapshot(
                    current_c=self.temperature_snapshot.current_c,
                    target_c=self.temperature_snapshot.target_c,
                    stable=self.temperature_snapshot.stable,
                    stable_streak=self.temperature_snapshot.stable_streak,
                    sampled_at=self.temperature_snapshot.sampled_at,
                )
            if snapshot.stable and snapshot.current_c is not None:
                self.logger.info(
                    "Temperature stable at %.2fC after %s samples",
                    snapshot.current_c,
                    snapshot.stable_streak,
                )
                return
            time.sleep(self.config.telemetry.poll_interval_s)
        raise IndiOperationError(f"Temperature did not stabilize near {target_c:.2f}C before timeout")

    def _blob_writer_loop(self) -> None:
        while not self.stop_event.is_set() or not self.writer_queue.empty():
            try:
                pending = self.writer_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            payload = pending.header_updates.pop("_blob_payload", None)
            try:
                if not isinstance(payload, (bytes, bytearray)):
                    raise IndiOperationError("Missing BLOB payload for FITS writer")
                self._write_blob_to_disk(bytes(payload), pending.destination, pending.header_updates)
                pending.result_path = pending.destination
            except BaseException as exc:  # pragma: no cover - hardware callback path
                pending.error = exc
            finally:
                pending.completion_event.set()
                self.writer_queue.task_done()

    def _telemetry_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                current = self._read_number_value(self.config.equipment.camera, self.config.camera.temperature)
            except BaseException as exc:  # pragma: no cover - hardware callback path
                self.logger.warning("Temperature poll failed: %s", exc)
                self.stop_event.wait(self.config.telemetry.poll_interval_s)
                continue

            target = getattr(self, "current_target_temperature", None)
            delta = abs(current - target) if target is not None else None
            with self.temperature_lock:
                if delta is not None and delta <= self.config.telemetry.temperature_tolerance_c:
                    stable_streak = self.temperature_snapshot.stable_streak + 1
                else:
                    stable_streak = 0
                self.temperature_snapshot = TemperatureSnapshot(
                    current_c=current,
                    target_c=target,
                    stable=stable_streak >= self.config.telemetry.stable_samples,
                    stable_streak=stable_streak,
                    sampled_at=datetime.now(timezone.utc),
                )
            self.logger.info(
                "Telemetry current=%.2fC target=%s stable_streak=%s",
                current,
                "none" if target is None else f"{target:.2f}C",
                stable_streak,
            )
            self.stop_event.wait(self.config.telemetry.poll_interval_s)

    def _connect_device(self, device_name: str) -> None:
        device = self._wait_for_device(device_name)
        is_connected = getattr(device, "isConnected", None)
        if callable(is_connected) and is_connected():
            self.logger.info("Device already connected: %s", device_name)
            return
        self._set_switch_element(device_name, "CONNECTION", "CONNECT")
        self.logger.info("Connected device %s", device_name)

    def _enable_blobs(self) -> None:
        if PyIndi is None:
            raise IndiOperationError("PyIndi is required to enable BLOB streaming")
        self.setBLOBMode(PyIndi.B_ALSO, self.config.equipment.camera, self.config.camera.blob_property)
        self.logger.info(
            "Enabled BLOB streaming for %s.%s -> %s",
            self.config.equipment.camera,
            self.config.camera.blob_property,
            self.config.paths.buffer_root,
        )

    def _ensure_cooler_enabled(self) -> None:
        cooler = self.config.camera.cooler
        if cooler is None:
            return
        self._set_switch_element(self.config.equipment.camera, cooler.name, cooler.on_element)
        self.logger.info("Camera cooler enabled")

    def _set_target_temperature(self, temperature_c: float) -> None:
        self.current_target_temperature = temperature_c
        self._set_number_value(self.config.equipment.camera, self.config.camera.temperature, temperature_c)
        self.logger.info("Camera temperature target set to %.2fC", temperature_c)

    def _set_gain_and_offset(self, gain: int) -> None:
        if gain not in self.config.camera.offsets_by_gain:
            raise ConfigError(f"No offset mapping configured for gain {gain}")
        self._set_number_value(self.config.equipment.camera, self.config.camera.gain, gain)
        self._set_number_value(
            self.config.equipment.camera,
            self.config.camera.offset,
            self.config.camera.offsets_by_gain[gain],
        )
        self.logger.info(
            "Set gain=%s offset=%s",
            gain,
            self.config.camera.offsets_by_gain[gain],
        )

    def _set_filter(self, filter_name: str | None) -> None:
        if filter_name is None or self.config.filter_wheel is None or self.config.equipment.filter_wheel is None:
            return
        if filter_name not in self.config.filter_wheel.filter_map:
            raise ConfigError(f"Filter {filter_name} is not present in filter_wheel.filter_map")
        slot = self.config.filter_wheel.filter_map[filter_name]
        self._set_number_value(self.config.equipment.filter_wheel, self.config.filter_wheel.slot, slot)
        self.logger.info("Selected filter %s (slot %s)", filter_name, slot)
        time.sleep(self.config.execution.filter_settle_s)

    def _current_filter_name(self, requested_filter: str | None) -> str:
        if requested_filter is None:
            return self.config.camera.fits_headers.default_filter_label
        if self.config.filter_wheel is None or self.config.equipment.filter_wheel is None:
            return requested_filter
        current_slot = int(round(self._read_number_value(self.config.equipment.filter_wheel, self.config.filter_wheel.slot)))
        for filter_name, slot in self.config.filter_wheel.filter_map.items():
            if slot == current_slot:
                return filter_name
        return requested_filter

    def _build_destination(self, task: CaptureTask) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filter_name = task.filter_name or self.config.camera.fits_headers.default_filter_label
        frame_dir = (
            self.config.paths.buffer_root
            / task.frame_type
            / f"temp_{_temp_token(task.temperature_c)}C"
            / f"gain_{task.gain}"
            / _slug(filter_name)
        )
        filename = (
            f"{task.frame_type}_"
            f"temp_{_temp_token(task.temperature_c)}C_"
            f"gain_{task.gain}_"
            f"filter_{_slug(filter_name)}_"
            f"exp_{_exp_token(task.exposure_s)}s_"
            f"frame_{task.frame_index:04d}_"
            f"{timestamp}.fits"
        )
        return frame_dir / filename

    def _write_blob_to_disk(self, payload: bytes, destination: Path, header_updates: dict[str, Any]) -> None:
        if fits is None:
            raise IndiOperationError("astropy is required to update FITS headers")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with fits.open(io.BytesIO(payload)) as hdul:
            header = hdul[0].header
            for key, value in header_updates.items():
                header[key] = value
            hdul.writeto(destination, overwrite=True)

    def _wait_for_device(self, device_name: str) -> Any:
        deadline = time.monotonic() + self.config.indi.connect_timeout_s
        while time.monotonic() < deadline:
            device = self.getDevice(device_name)
            if device is not None:
                return device
            time.sleep(0.2)
        raise IndiOperationError(f"Timed out waiting for device {device_name}")

    def _wait_for_number_property(self, device_name: str, prop: NumberPropertyConfig) -> Any:
        deadline = time.monotonic() + self.config.indi.property_timeout_s
        while time.monotonic() < deadline:
            device = self._wait_for_device(device_name)
            number_vector = device.getNumber(prop.name)
            if number_vector is not None:
                return number_vector
            time.sleep(0.2)
        raise IndiOperationError(f"Timed out waiting for property {device_name}.{prop.name}")

    def _wait_for_switch_property(self, device_name: str, property_name: str) -> Any:
        deadline = time.monotonic() + self.config.indi.property_timeout_s
        while time.monotonic() < deadline:
            device = self._wait_for_device(device_name)
            switch_vector = device.getSwitch(property_name)
            if switch_vector is not None:
                return switch_vector
            time.sleep(0.2)
        raise IndiOperationError(f"Timed out waiting for switch property {device_name}.{property_name}")

    def _set_number_value(self, device_name: str, prop: NumberPropertyConfig, value: float) -> None:
        if PyIndi is None:
            raise IndiOperationError("PyIndi is required to set INDI number properties")
        number_vector = self._wait_for_number_property(device_name, prop)
        number = PyIndi.IUFindNumber(number_vector, prop.element)
        if number is None:
            raise IndiOperationError(f"Property element missing: {device_name}.{prop.name}.{prop.element}")
        number.value = value
        self.sendNewNumber(number_vector)

    def _read_number_value(self, device_name: str, prop: NumberPropertyConfig) -> float:
        if PyIndi is None:
            raise IndiOperationError("PyIndi is required to read INDI number properties")
        number_vector = self._wait_for_number_property(device_name, prop)
        number = PyIndi.IUFindNumber(number_vector, prop.element)
        if number is None:
            raise IndiOperationError(f"Property element missing: {device_name}.{prop.name}.{prop.element}")
        return float(number.value)

    def _set_switch_element(self, device_name: str, property_name: str, element_name: str) -> None:
        if PyIndi is None:
            raise IndiOperationError("PyIndi is required to set INDI switch properties")
        switch_vector = self._wait_for_switch_property(device_name, property_name)
        PyIndi.IUResetSwitch(switch_vector)
        switch = PyIndi.IUFindSwitch(switch_vector, element_name)
        if switch is None:
            raise IndiOperationError(f"Switch element missing: {device_name}.{property_name}.{element_name}")
        switch.s = PyIndi.ISS_ON
        self.sendNewSwitch(switch_vector)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FOCAL INDI calibration runner")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the DoE matrix summary without touching INDI hardware",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    logger = configure_logging(config.paths.log_file, args.log_level)
    matrix_summary = summarize_matrix(config)
    logger.info("Loaded FOCAL config for %s", config.project.name)
    logger.info("Matrix summary: %s", matrix_summary)
    logger.info(
        "Asynchronous transfer target: %s mounted at %s via %s",
        config.transfer.remote_windows_share,
        config.transfer.remote_mount,
        config.transfer.rsync_script,
    )

    if args.plan_only:
        return 0

    require_runtime_dependencies()
    client = FocalIndiClient(config, logger)
    try:
        client.initialize_hardware()
        client.execute_doe()
    finally:
        client.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())