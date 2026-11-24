"""Private acquisition storage and its manifest commit boundary (T-03)."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TypeVar

from pydantic import TypeAdapter

from ..models import LegalSource, ManualAttestation


class AcquisitionError(Exception):
    """Base exception for source acquisition failures."""


class SecurityPolicyViolationError(AcquisitionError):
    """An acquisition request violates security or governance policy."""


class SnapshotIntegrityError(AcquisitionError):
    """A snapshot or its stored provenance fails integrity validation."""


def _check_path(path: Path, *, directory: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not (stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode)
    ):
        raise SecurityPolicyViolationError(f"Unexpected file type or redirected path: {path}")


def _root(base_dir: Path) -> Path:
    path = Path(os.path.abspath(base_dir))
    if path.resolve() != path:
        raise SecurityPolicyViolationError(f"Redirected acquisition root: {path}")
    _check_path(path, directory=True)
    return path


def _private_root(base_dir: Path) -> Path:
    root = _root(base_dir)
    for parent in (root, *root.parents):
        if (parent / ".git").exists():
            staged = parent / "data" / "official_sources" / ".acquisition"
            if not root.is_relative_to(staged):
                raise SecurityPolicyViolationError(
                    "Acquisition requires a private staging directory"
                )
            break
    return root


def compute_snapshot_path(base_dir: Path, sha256_hash: str, ext: str) -> Path:
    """Return the canonical content-addressed path, rejecting redirected components."""
    digest = sha256_hash.strip().lower()
    suffix = ext.lstrip(".").lower().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise SnapshotIntegrityError(f"Invalid SHA-256 digest format: {sha256_hash}")
    if not re.fullmatch(r"[a-z0-9]+", suffix):
        raise ValueError(f"Invalid file extension: {ext}")
    snapshots = _root(base_dir) / "snapshots"
    shard = snapshots / digest[:2]
    target = shard / f"{digest}.{suffix}"
    for directory in (snapshots, shard):
        _check_path(directory, directory=True)
    _check_path(target)
    return target


def _sync_directory(path: Path) -> None:
    if sys.platform != "win32":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def _staged_bytes(target: Path, content: bytes) -> Iterator[Path]:
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=".acquire-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def _verify_snapshot(target: Path, digest: str, size: int) -> None:
    _check_path(target)
    with target.open("rb") as existing:
        if os.fstat(existing.fileno()).st_size == size:
            checksum = hashlib.sha256()
            remaining = size
            while remaining:
                chunk = existing.read(min(65_536, remaining))
                if not chunk:
                    break
                checksum.update(chunk)
                remaining -= len(chunk)
            if remaining == 0 and not existing.read(1) and checksum.hexdigest() == digest:
                return
    raise SnapshotIntegrityError(f"Snapshot corruption detected at existing file {target}")


def _publish_snapshot(temporary: Path, target: Path) -> None:
    if sys.platform == "win32":
        # Windows rename fails if the destination already exists.
        os.rename(temporary, target)
    else:
        os.link(temporary, target)


def store_snapshot(
    content_bytes: bytes,
    snapshot_dir: Path,
    ext: str,
    expected_sha256: str | None = None,
) -> tuple[Path, str]:
    """Publish complete bytes with atomic create-if-absent semantics on NTFS and POSIX."""
    digest = hashlib.sha256(content_bytes).hexdigest()
    if expected_sha256 is not None and expected_sha256.strip().lower() != digest:
        raise SnapshotIntegrityError(f"Checksum mismatch: expected {expected_sha256}, got {digest}")

    root = _private_root(snapshot_dir)
    target = compute_snapshot_path(root, digest, ext)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = compute_snapshot_path(root, digest, ext)
    if target.exists():
        _verify_snapshot(target, digest, len(content_bytes))
    else:
        with _staged_bytes(target, content_bytes) as temporary:
            try:
                _publish_snapshot(temporary, target)
            except FileExistsError:
                _verify_snapshot(target, digest, len(content_bytes))
    for directory in (target.parent, target.parent.parent, root):
        _sync_directory(directory)
    return target, digest


@contextmanager
def _manifest_lock(root: Path) -> Iterator[None]:
    """Serialize cooperating writers across processes, releasing on process exit."""
    lock = root / ".acquisition.lock"
    _check_path(lock)
    with lock.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


_SOURCE_RECORDS = TypeAdapter(list[LegalSource])
_ATTESTATION_RECORDS = TypeAdapter(list[ManualAttestation])


T = TypeVar("T")


def _read_records(path: Path, adapter: TypeAdapter[list[T]]) -> list[T]:
    _check_path(path)
    if not path.exists():
        return []
    return adapter.validate_json(path.read_bytes())


def persist_acquisition(
    root: Path,
    source: LegalSource,
    content: bytes,
    attestation: ManualAttestation | None = None,
) -> LegalSource:
    """Commit a manifest entry after its snapshot and optional attestation are durable."""
    root = _private_root(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _sync_directory(root.parent)
    manifest = root / "manifest.json"
    attestations_file = root / "attestations.json"
    suffix = {"raw_pdf": "pdf", "raw_html": "html"}[source.artifact_type]

    with _manifest_lock(root):
        sources = _read_records(manifest, _SOURCE_RECORDS)
        attestations = _read_records(attestations_file, _ATTESTATION_RECORDS)
        if len({record.source_id for record in sources}) != len(sources) or len(
            {record.attestation_id for record in attestations}
        ) != len(attestations):
            raise SnapshotIntegrityError("Duplicate acquisition records in staging")
        if attestation is not None:
            recorded = next(
                (a for a in attestations if a.attestation_id == attestation.attestation_id), None
            )
            if recorded is not None and recorded != attestation:
                raise SnapshotIntegrityError("Attestation ID already binds different evidence")
        else:
            recorded = None

        for existing in sources:
            if existing.source_id == source.source_id:
                # Automated retries may use a new connection, but cannot replace accepted evidence.
                exclude = (
                    {"retrieved_at", "tls_cert_fingerprint", "http_capture"}
                    if attestation is None
                    else set()
                )
                if existing.model_dump(exclude=exclude) != source.model_dump(exclude=exclude):
                    raise SnapshotIntegrityError("Source ID already binds a different acquisition")
                if attestation is not None and recorded is None:
                    raise SnapshotIntegrityError("Accepted source is missing its attestation")
                target = compute_snapshot_path(root, source.sha256, suffix)
                _verify_snapshot(target, source.sha256, len(content))
                return existing

        manifest_bytes = _SOURCE_RECORDS.dump_json([*sources, source], indent=2) + b"\n"
        with _staged_bytes(manifest, manifest_bytes) as staged_manifest:
            store_snapshot(content, root, suffix, expected_sha256=source.sha256)
            new_attestation = attestation is not None and recorded is None
            previous = None
            if attestation is not None and recorded is None:
                previous = attestations_file.read_bytes() if attestations_file.exists() else None
                with _staged_bytes(
                    attestations_file,
                    _ATTESTATION_RECORDS.dump_json([*attestations, attestation], indent=2) + b"\n",
                ) as staged_attestations:
                    os.replace(staged_attestations, attestations_file)
            try:
                _sync_directory(root)
                os.replace(staged_manifest, manifest)
            except OSError:
                if new_attestation:
                    if previous is None:
                        attestations_file.unlink()
                    else:
                        with _staged_bytes(attestations_file, previous) as rollback:
                            os.replace(rollback, attestations_file)
                    _sync_directory(root)
                raise
        _sync_directory(root)
    return source
