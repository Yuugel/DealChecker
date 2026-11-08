"""Local-only secret persistence; never included in state responses or SQLite."""
import json
import os
from pathlib import Path
import tempfile


class NotificationSettings:
    def __init__(self, data_dir: Path):
        self.path = data_dir / 'notifications.json'

    def load(self) -> str:
        if not self.path.exists():
            return ''
        try:
            if self.path.stat().st_size > 4096:
                raise ValueError()
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or set(data) != {'discord_webhook'}:
                raise ValueError()
            value = data['discord_webhook']
            if not isinstance(value, str) or len(value) > 2048:
                raise ValueError()
            return value
        except (OSError, ValueError):
            raise ValueError('Lokale Benachrichtigungskonfiguration ist ungültig') from None

    def save(self, value: str):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.notifications-', delete=False) as file:
                pending = Path(file.name)
                json.dump({'discord_webhook': value}, file)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(pending, 0o600)
            os.replace(pending, self.path)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)
