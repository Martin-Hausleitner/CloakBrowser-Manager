"""Private Manager-owned screenshot artifact storage.

Ingests only caller-supplied bytes (never paths). Validates media type, digest,
magic, and bounded dimensions. Persists opaque paths under CBM_ARTIFACT_ROOT.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import stat
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

if __package__:
    from . import database as db
else:
    import database as db

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 4096
ALLOWED_MEDIA_TYPES = frozenset({"image/png", "image/jpeg"})
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
ARTIFACT_RETENTION_AFTER_ARCHIVE = timedelta(days=7)
MIGRATION_VERSION = "task_artifacts_v1"
ENV_ARTIFACT_ROOT = "CBM_ARTIFACT_ROOT"


class ArtifactValidationError(ValueError):
    """Raised when screenshot bytes fail validation."""


class ArtifactNotFound(LookupError):
    """Raised when no artifact exists for the requested output."""


class ArtifactExpired(LookupError):
    """Raised when artifact bytes are expired or deleted."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def normalize_media_type(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned not in ALLOWED_MEDIA_TYPES:
        raise ArtifactValidationError("unsupported media type")
    return cleaned


def normalize_sha256(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if len(cleaned) != 64 or any(ch not in "0123456789abcdef" for ch in cleaned):
        raise ArtifactValidationError("invalid sha256 digest")
    return cleaned


# PNG color-type -> legal bit depths and channel counts.
_PNG_COLOR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_LEGAL_BIT_DEPTHS = {
    0: frozenset({1, 2, 4, 8, 16}),
    2: frozenset({8, 16}),
    3: frozenset({1, 2, 4, 8}),
    4: frozenset({8, 16}),
    6: frozenset({8, 16}),
}
_PNG_CRITICAL = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})
_JPEG_SOF_MARKERS = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)
_JPEG_STANDALONE = frozenset({0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9})


def _png_is_critical(tag: bytes) -> bool:
    return len(tag) == 4 and (tag[0] & 0x20) == 0


def _png_expected_raw_size(width: int, height: int, bit_depth: int, channels: int) -> int:
    bits_per_pixel = bit_depth * channels
    row_bytes = 1 + (width * bits_per_pixel + 7) // 8
    return row_bytes * height


def _validate_png_idat_stream(
    idat: bytes, *, width: int, height: int, bit_depth: int, channels: int
) -> None:
    expected = _png_expected_raw_size(width, height, bit_depth, channels)
    bits_per_pixel = bit_depth * channels
    row_bytes = 1 + (width * bits_per_pixel + 7) // 8
    decompressor = zlib.decompressobj()
    pending = bytearray()
    produced = 0

    def _consume_complete_rows() -> None:
        nonlocal produced
        while len(pending) >= row_bytes:
            if pending[0] > 4:
                raise ArtifactValidationError("invalid png filter")
            del pending[:row_bytes]
            produced += row_bytes
            if produced > expected:
                raise ArtifactValidationError("png idat overflow")

    try:
        offset = 0
        chunk_size = 64 * 1024
        while offset < len(idat):
            piece = idat[offset : offset + chunk_size]
            offset += len(piece)
            try:
                out = decompressor.decompress(piece)
            except zlib.error as exc:
                raise ArtifactValidationError("invalid png idat zlib") from exc
            if out:
                pending.extend(out)
                if len(pending) > expected - produced + row_bytes:
                    raise ArtifactValidationError("png idat overflow")
                _consume_complete_rows()
            if decompressor.eof:
                break
        if not decompressor.eof:
            try:
                out = decompressor.flush()
            except zlib.error as exc:
                raise ArtifactValidationError("invalid png idat zlib") from exc
            if out:
                pending.extend(out)
                _consume_complete_rows()
        if not decompressor.eof:
            raise ArtifactValidationError("truncated png idat")
        if decompressor.unused_data or offset < len(idat):
            raise ArtifactValidationError("trailing png idat compressed data")
        if pending or produced != expected:
            raise ArtifactValidationError("png idat size mismatch")
    finally:
        pending.clear()
        del decompressor


def _parse_png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or not data.startswith(PNG_MAGIC):
        raise ArtifactValidationError("invalid png magic")
    offset = 8
    width = height = 0
    bit_depth = color_type = -1
    channels = 0
    saw_ihdr = False
    saw_plte = False
    saw_idat = False
    idat_finished = False
    idat_parts: list[bytes] = []
    idat_total = 0

    while True:
        if offset + 12 > len(data):
            raise ArtifactValidationError("truncated png")
        length = int.from_bytes(data[offset : offset + 4], "big")
        tag = data[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if length < 0 or crc_end > len(data):
            raise ArtifactValidationError("truncated png")
        chunk_data = data[data_start:data_end]
        expected_crc = int.from_bytes(data[data_end:crc_end], "big")
        actual_crc = zlib.crc32(tag + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ArtifactValidationError("invalid png crc")

        if not saw_ihdr:
            if tag != b"IHDR" or offset != 8:
                raise ArtifactValidationError("png ihdr must be first")
            if length != 13:
                raise ArtifactValidationError("invalid png ihdr")
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
            compression = chunk_data[10]
            filter_method = chunk_data[11]
            interlace = chunk_data[12]
            if width <= 0 or height <= 0:
                raise ArtifactValidationError("invalid dimensions")
            if width > MAX_DIMENSION or height > MAX_DIMENSION:
                raise ArtifactValidationError("dimensions exceed limit")
            legal = _PNG_LEGAL_BIT_DEPTHS.get(color_type)
            if legal is None or bit_depth not in legal:
                raise ArtifactValidationError("invalid png color/bit depth")
            if compression != 0 or filter_method != 0:
                raise ArtifactValidationError("invalid png compression/filter method")
            if interlace != 0:
                raise ArtifactValidationError("interlaced png not allowed")
            channels = _PNG_COLOR_CHANNELS[color_type]
            saw_ihdr = True
        elif tag == b"IHDR":
            raise ArtifactValidationError("duplicate png ihdr")
        elif tag == b"PLTE":
            if saw_idat or saw_plte:
                raise ArtifactValidationError("invalid png plte ordering")
            if length == 0 or length % 3 != 0 or length > 256 * 3:
                raise ArtifactValidationError("invalid png plte")
            saw_plte = True
        elif tag == b"IDAT":
            if idat_finished:
                raise ArtifactValidationError("non-contiguous png idat")
            if color_type == 3 and not saw_plte:
                raise ArtifactValidationError("png plte required")
            # Bound retained compressed IDAT memory to MAX_BYTES.
            idat_total += length
            if idat_total > MAX_BYTES:
                raise ArtifactValidationError("png idat exceeds size limit")
            idat_parts.append(chunk_data)
            saw_idat = True
        elif tag == b"IEND":
            if length != 0:
                raise ArtifactValidationError("invalid png iend")
            if not saw_idat:
                raise ArtifactValidationError("png missing idat")
            if crc_end != len(data):
                raise ArtifactValidationError("trailing bytes after png iend")
            concatenated = b"".join(idat_parts)
            idat_parts.clear()
            _validate_png_idat_stream(
                concatenated,
                width=width,
                height=height,
                bit_depth=bit_depth,
                channels=channels,
            )
            return width, height
        else:
            if saw_idat:
                idat_finished = True
            if _png_is_critical(tag) and tag not in _PNG_CRITICAL:
                raise ArtifactValidationError("unknown critical png chunk")
            # Ancillary chunks allowed; PLTE already handled.
        offset = crc_end


def _jpeg_skip_entropy(data: bytes, offset: int) -> int:
    """Scan entropy-coded data until the next non-RST/non-stuffed marker."""
    i = offset
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            i += 1
            continue
        if i + 1 >= n:
            raise ArtifactValidationError("truncated jpeg")
        nxt = data[i + 1]
        if nxt == 0x00:
            i += 2
            continue
        if 0xD0 <= nxt <= 0xD7:
            i += 2
            continue
        if nxt == 0xFF:
            i += 1
            continue
        return i
    raise ArtifactValidationError("truncated jpeg")


def _parse_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        raise ArtifactValidationError("invalid jpeg magic")
    if not data.startswith(JPEG_MAGIC):
        # Allow SOI followed by fill/FF before first marker byte already covered.
        raise ArtifactValidationError("invalid jpeg magic")
    offset = 2
    width = height = 0
    saw_sof = False
    saw_sos = False
    while offset < len(data):
        if data[offset] != 0xFF:
            raise ArtifactValidationError("invalid jpeg marker")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise ArtifactValidationError("truncated jpeg")
        marker = data[offset]
        offset += 1
        if marker == 0xD9:  # EOI
            if not saw_sof or not saw_sos:
                raise ArtifactValidationError("jpeg missing sof/sos")
            if offset != len(data):
                raise ArtifactValidationError("trailing bytes after jpeg eoi")
            return width, height
        if marker == 0xD8:  # stray SOI
            raise ArtifactValidationError("invalid jpeg soi")
        if marker in _JPEG_STANDALONE and marker not in {0xD8, 0xD9}:
            # TEM / RSTn outside entropy are invalid here.
            if 0xD0 <= marker <= 0xD7 or marker == 0x01:
                raise ArtifactValidationError("invalid jpeg standalone marker")
            continue
        if offset + 2 > len(data):
            raise ArtifactValidationError("truncated jpeg")
        segment_len = int.from_bytes(data[offset : offset + 2], "big")
        if segment_len < 2 or offset + segment_len > len(data):
            raise ArtifactValidationError("truncated jpeg")
        segment = data[offset : offset + segment_len]
        if marker in _JPEG_SOF_MARKERS:
            if segment_len < 8:
                raise ArtifactValidationError("invalid jpeg sof")
            precision = segment[2]
            sof_height = int.from_bytes(segment[3:5], "big")
            sof_width = int.from_bytes(segment[5:7], "big")
            components = segment[7]
            if precision < 1 or precision > 16:
                raise ArtifactValidationError("invalid jpeg sof")
            if components < 1 or components > 4:
                raise ArtifactValidationError("invalid jpeg sof")
            if segment_len != 8 + components * 3:
                raise ArtifactValidationError("invalid jpeg sof")
            if sof_width <= 0 or sof_height <= 0:
                raise ArtifactValidationError("invalid dimensions")
            if sof_width > MAX_DIMENSION or sof_height > MAX_DIMENSION:
                raise ArtifactValidationError("dimensions exceed limit")
            width, height = sof_width, sof_height
            saw_sof = True
        elif marker == 0xDA:  # SOS
            if not saw_sof:
                raise ArtifactValidationError("jpeg sos before sof")
            if segment_len < 6:
                raise ArtifactValidationError("invalid jpeg sos")
            ns = segment[2]
            if ns < 1 or ns > 4 or segment_len < 6 + 2 * ns:
                raise ArtifactValidationError("invalid jpeg sos")
            saw_sos = True
            offset += segment_len
            offset = _jpeg_skip_entropy(data, offset)
            continue
        offset += segment_len
    raise ArtifactValidationError("truncated jpeg")


def validate_screenshot_bytes(*, body: bytes, media_type: str, sha256: str) -> tuple[str, str, int, int]:
    if not isinstance(body, (bytes, bytearray)):
        raise ArtifactValidationError("body must be bytes")
    raw = bytes(body)
    if len(raw) == 0 or len(raw) > MAX_BYTES:
        raise ArtifactValidationError("body exceeds size limit")
    media = normalize_media_type(media_type)
    digest = normalize_sha256(sha256)
    actual = hashlib.sha256(raw).hexdigest()
    if not hmac_compare_hex(actual, digest):
        raise ArtifactValidationError("sha256 mismatch")
    if media == "image/png":
        if not raw.startswith(PNG_MAGIC):
            raise ArtifactValidationError("png magic mismatch")
        width, height = _parse_png_dimensions(raw)
    else:
        if not raw.startswith(JPEG_MAGIC):
            raise ArtifactValidationError("jpeg magic mismatch")
        width, height = _parse_jpeg_dimensions(raw)
    if width <= 0 or height <= 0:
        raise ArtifactValidationError("invalid dimensions")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ArtifactValidationError("dimensions exceed limit")
    return media, digest, width, height


def hmac_compare_hex(left: str, right: str) -> bool:
    import hmac as _hmac

    return _hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


def default_artifact_root() -> Path:
    configured = os.environ.get(ENV_ARTIFACT_ROOT)
    if configured:
        return Path(configured).expanduser()
    return Path(db.DATA_DIR) / "artifacts"


@dataclass(frozen=True)
class IngestedArtifact:
    artifact_id: str
    output_id: str
    run_id: str
    task_session_id: str
    sandbox_id: str
    media_type: str
    sha256: str
    width: int
    height: int
    created_at: str
    expires_at: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "output_id": self.output_id,
            "run_id": self.run_id,
            "task_session_id": self.task_session_id,
            "sandbox_id": self.sandbox_id,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ScreenshotPayload:
    body: bytes
    media_type: str
    filename: str
    artifact_id: str
    sandbox_id: str


class ArtifactStore:
    """Opaque private screenshot store bound to task output metadata."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        get_db: Callable = db.get_db,
    ) -> None:
        self.root = Path(root) if root is not None else default_artifact_root()
        self._clock = clock or _utc_now
        self._get_db = get_db
        self._ensure_root()

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ArtifactValidationError("artifact root must be a real directory")
        os.chmod(self.root, 0o700)

    def ensure_schema(self) -> None:
        """Idempotent transactional metadata table for screenshot artifacts."""
        self._ensure_root()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                already = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (MIGRATION_VERSION,),
                ).fetchone()
                if already:
                    conn.commit()
                    return
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS task_artifacts (
                        id TEXT PRIMARY KEY,
                        output_id TEXT NOT NULL UNIQUE
                            REFERENCES task_outputs(id) ON DELETE RESTRICT,
                        run_id TEXT NOT NULL,
                        task_session_id TEXT NOT NULL
                            REFERENCES task_sessions(id) ON DELETE RESTRICT,
                        sandbox_id TEXT NOT NULL,
                        media_type TEXT NOT NULL
                            CHECK (media_type IN ('image/png', 'image/jpeg')),
                        sha256 TEXT NOT NULL,
                        width INTEGER NOT NULL CHECK (width > 0 AND width <= 4096),
                        height INTEGER NOT NULL CHECK (height > 0 AND height <= 4096),
                        storage_relpath TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        deleted_at TEXT,
                        delete_failed_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_task_artifacts_task
                        ON task_artifacts(task_session_id)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_task_artifacts_expire
                        ON task_artifacts(expires_at)
                        WHERE deleted_at IS NULL
                    """
                )
                violation = conn.execute("PRAGMA foreign_key_check").fetchone()
                if violation is not None:
                    raise RuntimeError(
                        f"Foreign key violation after {MIGRATION_VERSION}: {tuple(violation)}"
                    )
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (MIGRATION_VERSION, _iso(self._clock())),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def ingest_screenshot(
        self,
        *,
        output_id: str,
        body: bytes,
        media_type: str,
        sha256: str,
        **_ignored: Any,
    ) -> IngestedArtifact:
        """Validate and store screenshot bytes for an existing output.

        Extra kwargs (path/filename/etc.) are intentionally ignored so callers
        cannot influence storage layout.
        """
        self.ensure_schema()
        media, digest, width, height = validate_screenshot_bytes(
            body=body, media_type=media_type, sha256=sha256
        )
        now = self._clock()
        artifact_id = secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:24]
        if not artifact_id:
            artifact_id = uuid.uuid4().hex
        run_id = ""
        task_session_id = ""
        sandbox_id = ""
        expires_at: str | None = None
        abs_final: Path | None = None

        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                output = conn.execute(
                    "SELECT id, run_id, kind FROM task_outputs WHERE id = ?",
                    (output_id,),
                ).fetchone()
                if output is None:
                    conn.commit()
                    raise ArtifactNotFound(output_id)
                if str(output["kind"]) != "screenshot":
                    conn.commit()
                    raise ArtifactValidationError("output is not a screenshot")
                run = conn.execute(
                    "SELECT id, task_session_id, sandbox_id FROM task_runs WHERE id = ?",
                    (output["run_id"],),
                ).fetchone()
                if run is None:
                    conn.commit()
                    raise ArtifactNotFound(output_id)
                existing = conn.execute(
                    "SELECT id FROM task_artifacts WHERE output_id = ?",
                    (output_id,),
                ).fetchone()
                if existing is not None:
                    conn.commit()
                    raise ArtifactValidationError("artifact already exists for output")

                run_id = str(run["id"])
                task_session_id = str(run["task_session_id"])
                sandbox_id = str(run["sandbox_id"])
                task_row = conn.execute(
                    """
                    SELECT archived_at, status FROM task_sessions WHERE id = ?
                    """,
                    (task_session_id,),
                ).fetchone()
                expires_at = None
                if task_row is not None:
                    archived_at = _parse_dt(task_row["archived_at"])
                    if archived_at is not None or str(task_row["status"]) == "archived":
                        when = archived_at or now
                        expires_at = _iso(when + ARTIFACT_RETENTION_AFTER_ARCHIVE)
                rel_dir = secrets.token_hex(16)
                rel_file = secrets.token_hex(16)
                relpath = f"{rel_dir}/{rel_file}"
                abs_dir = self.root / rel_dir
                abs_tmp = abs_dir / f".{rel_file}.tmp"
                abs_final = abs_dir / rel_file
                self._atomic_write(abs_dir, abs_tmp, abs_final, bytes(body))

                conn.execute(
                    """
                    INSERT INTO task_artifacts (
                        id, output_id, run_id, task_session_id, sandbox_id,
                        media_type, sha256, width, height, storage_relpath,
                        created_at, expires_at, deleted_at, delete_failed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (
                        artifact_id,
                        output_id,
                        run_id,
                        task_session_id,
                        sandbox_id,
                        media,
                        digest,
                        width,
                        height,
                        relpath,
                        _iso(now),
                        expires_at,
                    ),
                )
                # Screenshot metadata is derived from task_artifacts on read;
                # never copy server fields into task_outputs.payload_json.
                conn.commit()
            except Exception:
                conn.rollback()
                if abs_final is not None:
                    try:
                        if abs_final.exists() and not abs_final.is_symlink():
                            os.unlink(abs_final)
                        parent = abs_final.parent
                        if parent != self.root and parent.is_dir() and not any(parent.iterdir()):
                            parent.rmdir()
                    except OSError:
                        pass
                raise

        return IngestedArtifact(
            artifact_id=artifact_id,
            output_id=output_id,
            run_id=run_id,
            task_session_id=task_session_id,
            sandbox_id=sandbox_id,
            media_type=media,
            sha256=digest,
            width=width,
            height=height,
            created_at=_iso(now),
            expires_at=expires_at,
        )

    def _unlink_quiet(self, path: Path) -> None:
        try:
            if path.exists() or path.is_symlink():
                os.unlink(path)
        except OSError:
            pass

    def _atomic_write(self, abs_dir: Path, abs_tmp: Path, abs_final: Path, body: bytes) -> None:
        if abs_dir.exists() and (abs_dir.is_symlink() or not abs_dir.is_dir()):
            raise ArtifactValidationError("refusing symlink storage directory")
        abs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(abs_dir, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd: int | None = None
        renamed = False
        write_error: BaseException | None = None
        try:
            fd = os.open(str(abs_tmp), flags, 0o600)
            written = 0
            view = memoryview(body)
            while written < len(body):
                n = os.write(fd, view[written:])
                if n <= 0:
                    raise OSError("short write to artifact temp file")
                written += n
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.chmod(abs_tmp, 0o600)
            # Reject replacement with a symlink at the destination.
            if abs_final.exists() or abs_final.is_symlink():
                raise ArtifactValidationError("refusing to overwrite existing path")
            os.rename(abs_tmp, abs_final)
            renamed = True
            # Confirm final is a regular file, not a symlink.
            st = os.lstat(abs_final)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise ArtifactValidationError("refusing symlink artifact file")
            os.chmod(abs_final, 0o600)
            # Persist the directory entry before the caller commits metadata.
            dir_fd = os.open(str(abs_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException as exc:
            write_error = exc
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            # Best-effort cleanup without masking the original error.
            try:
                self._unlink_quiet(abs_tmp)
                if renamed:
                    self._unlink_quiet(abs_final)
                if abs_dir.exists() and abs_dir.is_dir() and not abs_dir.is_symlink():
                    try:
                        if not any(abs_dir.iterdir()):
                            abs_dir.rmdir()
                    except OSError:
                        pass
            except Exception:
                pass
            raise write_error

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self._get_db() as conn:
            row = conn.execute(
                "SELECT * FROM task_artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_artifact_for_output(self, output_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        with self._get_db() as conn:
            row = conn.execute(
                "SELECT * FROM task_artifacts WHERE output_id = ?",
                (output_id,),
            ).fetchone()
            return dict(row) if row else None

    def _is_expired(self, row: dict[str, Any], now: datetime) -> bool:
        if row.get("deleted_at"):
            return True
        expires = _parse_dt(row.get("expires_at"))
        # Strictly after the scheduled expiry instant.
        return expires is not None and expires < now

    def read_for_output(self, output_id: str) -> ScreenshotPayload:
        self.ensure_schema()
        now = self._clock()
        row = self.get_artifact_for_output(output_id)
        if row is None:
            raise ArtifactNotFound(output_id)
        if self._is_expired(row, now):
            raise ArtifactExpired(output_id)
        rel = str(row["storage_relpath"])
        if ".." in rel.split("/") or rel.startswith("/") or "\\" in rel:
            raise ArtifactNotFound(output_id)
        abs_path = self.root.joinpath(*rel.split("/"))
        if not abs_path.is_relative_to(self.root):
            raise ArtifactNotFound(output_id)
        try:
            st = os.lstat(abs_path)
        except OSError as exc:
            raise ArtifactNotFound(output_id) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ArtifactNotFound(output_id)
        flags = os.O_RDONLY | os.O_NOFOLLOW
        fd = os.open(str(abs_path), flags)
        try:
            body = os.read(fd, MAX_BYTES + 1)
        finally:
            os.close(fd)
        if len(body) > MAX_BYTES:
            raise ArtifactNotFound(output_id)
        media = str(row["media_type"])
        digest = str(row["sha256"])
        try:
            validate_screenshot_bytes(body=body, media_type=media, sha256=digest)
        except ArtifactValidationError as exc:
            raise ArtifactNotFound(output_id) from exc
        filename = "screenshot.png" if media == "image/png" else "screenshot.jpg"
        return ScreenshotPayload(
            body=body,
            media_type=media,
            filename=filename,
            artifact_id=str(row["id"]),
            sandbox_id=str(row["sandbox_id"]),
        )

    def mark_task_archived(self, task_session_id: str, *, archived_at: datetime | None = None) -> int:
        self.ensure_schema()
        when = archived_at or self._clock()
        expires = when + ARTIFACT_RETENTION_AFTER_ARCHIVE
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """
                    UPDATE task_artifacts
                    SET expires_at = ?
                    WHERE task_session_id = ?
                      AND deleted_at IS NULL
                    """,
                    (_iso(expires), task_session_id),
                )
                conn.commit()
                return int(cursor.rowcount)
            except Exception:
                conn.rollback()
                raise

    def mark_task_reopened(self, task_session_id: str) -> int:
        self.ensure_schema()
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """
                    UPDATE task_artifacts
                    SET expires_at = NULL, delete_failed_at = NULL
                    WHERE task_session_id = ?
                      AND deleted_at IS NULL
                    """,
                    (task_session_id,),
                )
                conn.commit()
                return int(cursor.rowcount)
            except Exception:
                conn.rollback()
                raise

    def _delete_bytes(self, conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any], now: datetime) -> bool:
        artifact_id = str(row["id"])
        rel = str(row["storage_relpath"])
        if ".." in rel.split("/") or rel.startswith("/") or "\\" in rel:
            conn.execute(
                """
                UPDATE task_artifacts
                SET deleted_at = ?, delete_failed_at = NULL, storage_relpath = storage_relpath
                WHERE id = ?
                """,
                (_iso(now), artifact_id),
            )
            return True
        abs_path = self.root.joinpath(*rel.split("/"))
        if not abs_path.is_relative_to(self.root):
            conn.execute(
                "UPDATE task_artifacts SET delete_failed_at = ? WHERE id = ?",
                (_iso(now), artifact_id),
            )
            return False
        try:
            if abs_path.exists() or abs_path.is_symlink():
                st = os.lstat(abs_path)
                if stat.S_ISLNK(st.st_mode):
                    # Do not follow; refuse and leave retryable metadata.
                    conn.execute(
                        "UPDATE task_artifacts SET delete_failed_at = ? WHERE id = ?",
                        (_iso(now), artifact_id),
                    )
                    return False
                os.unlink(abs_path)
            # Best-effort remove empty opaque directory.
            parent = abs_path.parent
            if parent != self.root and parent.is_dir() and not parent.is_symlink():
                try:
                    parent.rmdir()
                except OSError:
                    pass
        except OSError:
            conn.execute(
                "UPDATE task_artifacts SET delete_failed_at = ? WHERE id = ?",
                (_iso(now), artifact_id),
            )
            logger.info(
                "artifact_delete_failed artifact_id=%s",
                artifact_id,
            )
            return False
        conn.execute(
            """
            UPDATE task_artifacts
            SET deleted_at = ?, delete_failed_at = NULL
            WHERE id = ?
            """,
            (_iso(now), artifact_id),
        )
        logger.info("artifact_deleted artifact_id=%s", artifact_id)
        return True

    def repair_expiry_drift(self) -> dict[str, int]:
        """Idempotently repair archived/active artifact expiry drift."""
        self.ensure_schema()
        repaired_archived = 0
        cleared_active = 0
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                table = conn.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'task_artifacts'
                    """
                ).fetchone()
                if table is None:
                    conn.commit()
                    return {"repaired_archived": 0, "cleared_active": 0}
                archived_rows = conn.execute(
                    """
                    SELECT a.id AS artifact_id, s.archived_at AS archived_at, a.expires_at AS expires_at
                    FROM task_artifacts a
                    JOIN task_sessions s ON s.id = a.task_session_id
                    WHERE a.deleted_at IS NULL
                      AND s.archived_at IS NOT NULL
                    """
                ).fetchall()
                for row in archived_rows:
                    archived_at = _parse_dt(row["archived_at"])
                    if archived_at is None:
                        continue
                    expected = _iso(archived_at + ARTIFACT_RETENTION_AFTER_ARCHIVE)
                    current = row["expires_at"]
                    if current != expected:
                        conn.execute(
                            "UPDATE task_artifacts SET expires_at = ? WHERE id = ?",
                            (expected, row["artifact_id"]),
                        )
                        repaired_archived += 1
                active_rows = conn.execute(
                    """
                    SELECT a.id AS artifact_id
                    FROM task_artifacts a
                    JOIN task_sessions s ON s.id = a.task_session_id
                    WHERE a.deleted_at IS NULL
                      AND a.expires_at IS NOT NULL
                      AND s.archived_at IS NULL
                      AND s.status != 'archived'
                    """
                ).fetchall()
                for row in active_rows:
                    conn.execute(
                        """
                        UPDATE task_artifacts
                        SET expires_at = NULL, delete_failed_at = NULL
                        WHERE id = ?
                        """,
                        (row["artifact_id"],),
                    )
                    cleared_active += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "repaired_archived": repaired_archived,
            "cleared_active": cleared_active,
        }

    def expire_due_once(self) -> dict[str, int]:
        """Delete due screenshot bytes; keep metadata on failure for retry."""
        self.ensure_schema()
        repair = self.repair_expiry_drift()
        now = self._clock()
        deleted = 0
        failures = 0
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM task_artifacts
                    WHERE deleted_at IS NULL
                      AND expires_at IS NOT NULL
                      AND expires_at < ?
                    ORDER BY created_at ASC
                    """,
                    (_iso(now),),
                ).fetchall()
                for row in rows:
                    if self._delete_bytes(conn, row, now):
                        deleted += 1
                    else:
                        failures += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "deleted": deleted,
            "delete_failures": failures,
            "repaired_archived": int(repair.get("repaired_archived") or 0),
            "cleared_active": int(repair.get("cleared_active") or 0),
        }

    def task_has_pending_bytes(self, task_session_id: str) -> bool:
        self.ensure_schema()
        with self._get_db() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM task_artifacts
                WHERE task_session_id = ? AND deleted_at IS NULL
                LIMIT 1
                """,
                (task_session_id,),
            ).fetchone()
            return row is not None

    def delete_artifact_rows_for_task(self, conn: sqlite3.Connection, task_session_id: str) -> int:
        """Remove artifact metadata rows only after bytes are gone."""
        pending = conn.execute(
            """
            SELECT 1 FROM task_artifacts
            WHERE task_session_id = ? AND deleted_at IS NULL
            LIMIT 1
            """,
            (task_session_id,),
        ).fetchone()
        if pending is not None:
            raise RuntimeError("cannot drop artifact metadata while bytes remain")
        cursor = conn.execute(
            "DELETE FROM task_artifacts WHERE task_session_id = ?",
            (task_session_id,),
        )
        return int(cursor.rowcount)

    def artifact_expired_for_output(self, output_id: str, *, now: datetime | None = None) -> bool:
        row = self.get_artifact_for_output(output_id)
        if row is None:
            return False
        return self._is_expired(row, now or self._clock())
