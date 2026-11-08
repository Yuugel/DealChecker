"""Local runtime configuration. Credentials never enter API or SQLite state."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path.home() / '.dealchecker')
    feeds: list[dict] = field(default_factory=list)
    discord_webhook: str = field(default='', repr=False)

    @classmethod
    def from_env(cls):
        try:
            feeds = json.loads(os.environ.get('DEALCHECKER_FEEDS', '[]'))
            if not isinstance(feeds, list) or len(feeds) > 20 or any(not isinstance(f, dict) for f in feeds):
                raise ValueError()
        except ValueError:
            raise ValueError('DEALCHECKER_FEEDS muss eine JSON-Liste mit höchstens 20 Feed-Konfigurationen sein') from None
        return cls(
            data_dir=Path(os.environ.get('DEALCHECKER_DATA_DIR', str(Path.home() / '.dealchecker'))).expanduser().resolve(),
            feeds=feeds, discord_webhook=os.environ.get('DEALCHECKER_DISCORD_WEBHOOK', ''),
        )


class InstanceLock:
    """OS-owned lock: automatically released after crash, no stale PID guessing."""
    def __init__(self, path: Path):
        self.path, self._file = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open('a+b')
        self._file.seek(0, 2)
        if self._file.tell() == 0:
            self._file.write(b'0')
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            self._file = None
            raise RuntimeError('DealChecker läuft bereits mit diesem Datenverzeichnis') from None
        return self

    def __exit__(self, *args):
        if self._file is not None:
            if os.name == 'nt':
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
