"""Allow only one record-writing workflow per local data directory."""

import errno
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pist.paths import PIST_DIR


class RecordWriterBusyError(RuntimeError):
    """Another workflow already owns the record-writing lock."""


@contextmanager
def record_writer_lock(data_dir: Path = PIST_DIR) -> Generator[None]:
    """Hold a nonblocking OS lock until exit, including exceptions or process death.

    All record-writing entry points must share this lock. The empty lock file is
    retained: removing it could let another process lock a different file inode.
    This protects cooperating workflows, not arbitrary direct database writes.
    """
    directory = data_dir.resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file = (directory / 'record-writer.lock').open('a+b')
    except OSError as error:
        raise RuntimeError(f'无法打开记录编辑锁：{directory}：{error}') from error
    with file:
        try:
            if os.name == 'nt':
                import msvcrt

                file.seek(0)
                # Windows supports locking a byte beyond EOF; no PID or marker is needed.
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise RecordWriterBusyError(
                    f'此数据目录已有地图浏览或记录写入任务运行，请先退出已有任务：{directory}'
                ) from error
            raise RuntimeError(f'无法取得记录编辑锁：{directory}：{error}') from error
        yield
