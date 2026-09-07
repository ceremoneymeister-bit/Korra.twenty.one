from __future__ import annotations

import ctypes
import errno
import os
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .errors import Conflict, NotFound, PathEscape

_SYS_OPENAT2 = 437
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_RESOLVE = _RESOLVE_BENEATH | _RESOLVE_NO_MAGICLINKS | _RESOLVE_NO_SYMLINKS
_libc = ctypes.CDLL(None, use_errno=True)


class _OpenHow(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


def _openat2(dirfd: int, path: str, flags: int, mode: int = 0) -> int:
    encoded = path.encode("utf-8")
    how = _OpenHow(flags=flags, mode=mode, resolve=_RESOLVE)
    fd = _libc.syscall(
        _SYS_OPENAT2,
        dirfd,
        ctypes.c_char_p(encoded),
        ctypes.byref(how),
        ctypes.sizeof(how),
    )
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), path)
    return int(fd)


def _clean_relative(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        raise PathEscape("Invalid relative path")
    raw_parts = path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise PathEscape("Invalid relative path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise PathEscape("Invalid relative path")
    return pure.as_posix()


class SecureRoot:
    """Linux-only dirfd root with fail-closed no-symlink traversal."""

    def __init__(self, path: Path, *, writable: bool) -> None:
        self.path = path
        self.writable = writable
        flags = os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        self.fd = os.open(path, flags)
        probe = _openat2(self.fd, ".", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        os.close(probe)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "SecureRoot":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def open_read_fd(self, relative: str) -> int:
        relative = _clean_relative(relative)
        try:
            fd = _openat2(self.fd, relative, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise NotFound("File not found") from exc
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise PathEscape("Expected a regular file")
        return fd

    def read_bytes(self, relative: str, *, limit: int | None = None) -> bytes:
        fd = self.open_read_fd(relative)
        try:
            info = os.fstat(fd)
            if limit is not None and info.st_size > limit:
                raise ValueError("size limit exceeded")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read() if limit is None else stream.read(limit + 1)
        finally:
            os.close(fd)

    def ensure_dir(self, relative: str, *, mode: int = 0o700) -> None:
        if not self.writable:
            raise PathEscape("Root is read-only")
        relative = _clean_relative(relative)
        current = os.dup(self.fd)
        try:
            for component in PurePosixPath(relative).parts:
                try:
                    nxt = _openat2(current, component, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
                except FileNotFoundError:
                    os.mkdir(component, mode=mode, dir_fd=current)
                    nxt = _openat2(current, component, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
                os.close(current)
                current = nxt
        finally:
            os.close(current)

    def atomic_write(self, relative: str, data: bytes, *, mode: int = 0o600) -> None:
        if not self.writable:
            raise PathEscape("Root is read-only")
        relative = _clean_relative(relative)
        parent, name = _split_parent(relative)
        parentfd = self._open_dir(parent)
        tmp_name = f".tmp-{secrets.token_hex(16)}"
        fd = -1
        try:
            fd = os.open(
                tmp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                mode,
                dir_fd=parentfd,
            )
            _write_all(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            try:
                os.link(tmp_name, name, src_dir_fd=parentfd, dst_dir_fd=parentfd, follow_symlinks=False)
            except FileExistsError as exc:
                raise Conflict("Destination already exists") from exc
            os.unlink(tmp_name, dir_fd=parentfd)
            os.fsync(parentfd)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_name, dir_fd=parentfd)
            except FileNotFoundError:
                pass
            os.close(parentfd)

    def atomic_replace(self, relative: str, data: bytes, *, mode: int = 0o600) -> None:
        """Записать файл, перезаписав существующий, одним атомарным шагом.

        Отличие от :meth:`atomic_write` — намеренное. `atomic_write` линкует и
        падает на занятом имени: так защищена история ревизий, которую нельзя
        перезаписывать даже по ошибке. Здесь наоборот: указатель активной
        ревизии обязан меняться, и меняться целиком — читатель либо видит
        старое имя, либо новое, но никогда полуфайл.

        `rename` внутри ОДНОГО каталога атомарен и не меняет инод каталога.
        Это важнее, чем кажется: MCP-сервер держит открытый dirfd корня ставок
        (см. ``SecureRoot.__init__``), и подмена самого каталога вместо файла
        внутри него оставила бы его читать удалённый инод до конца жизни
        процесса.
        """
        if not self.writable:
            raise PathEscape("Root is read-only")
        relative = _clean_relative(relative)
        parent, name = _split_parent(relative)
        parentfd = self._open_dir(parent)
        tmp_name = f".tmp-{secrets.token_hex(16)}"
        fd = -1
        try:
            fd = os.open(
                tmp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                mode,
                dir_fd=parentfd,
            )
            _write_all(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.rename(tmp_name, name, src_dir_fd=parentfd, dst_dir_fd=parentfd)
            os.fsync(parentfd)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_name, dir_fd=parentfd)
            except FileNotFoundError:
                pass
            os.close(parentfd)

    def append_bytes(self, relative: str, data: bytes, *, mode: int = 0o600) -> None:
        """Дописать в конец файла, создав его при нужде.

        Для журнала публикаций: он append-only по смыслу, и `O_APPEND` даёт
        это свойство на уровне ядра — параллельные писатели не затирают друг
        друга, а запись целой строки в пределах размера трубы неделима.
        """
        if not self.writable:
            raise PathEscape("Root is read-only")
        relative = _clean_relative(relative)
        parent, name = _split_parent(relative)
        parentfd = self._open_dir(parent)
        fd = -1
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW,
                mode,
                dir_fd=parentfd,
            )
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            if fd >= 0:
                os.close(fd)
            os.close(parentfd)

    def list_names(self) -> list[str]:
        """Имена в корне. Нужны, чтобы сверить журнал с тем, что на диске."""
        dirfd = self._open_dir("")
        try:
            return sorted(os.listdir(dirfd))
        finally:
            os.close(dirfd)

    def exists(self, relative: str) -> bool:
        try:
            os.close(self.open_read_fd(relative))
            return True
        except (NotFound, PathEscape):
            return False

    def atomic_copy(self, relative: str, source: BinaryIO, *, mode: int = 0o600) -> tuple[int, str]:
        import hashlib

        if not self.writable:
            raise PathEscape("Root is read-only")
        relative = _clean_relative(relative)
        parent, name = _split_parent(relative)
        parentfd = self._open_dir(parent)
        tmp_name = f".tmp-{secrets.token_hex(16)}"
        fd = -1
        digest = hashlib.sha256()
        total = 0
        try:
            fd = os.open(
                tmp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                mode,
                dir_fd=parentfd,
            )
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                total += len(chunk)
                _write_all(fd, chunk)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            try:
                os.link(tmp_name, name, src_dir_fd=parentfd, dst_dir_fd=parentfd, follow_symlinks=False)
            except FileExistsError as exc:
                raise Conflict("Destination already exists") from exc
            os.unlink(tmp_name, dir_fd=parentfd)
            os.fsync(parentfd)
            return total, digest.hexdigest()
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_name, dir_fd=parentfd)
            except FileNotFoundError:
                pass
            os.close(parentfd)

    def _open_dir(self, relative: str) -> int:
        if not relative:
            return _openat2(self.fd, ".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        relative = _clean_relative(relative)
        try:
            return _openat2(self.fd, relative, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except FileNotFoundError as exc:
            raise NotFound("Directory not found") from exc


def _split_parent(relative: str) -> tuple[str, str]:
    pure = PurePosixPath(relative)
    parent = "" if str(pure.parent) == "." else pure.parent.as_posix()
    return parent, pure.name


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[written:]
