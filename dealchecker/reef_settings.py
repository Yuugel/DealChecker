"""Local ReefAPI key and conservative, persistent daily credit budget.

No account is created and no subscription is purchased by this application.
Reservations count even when a request fails: the local bound must not depend
on interpreting a provider error as an unbilled request.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.reef-', delete=False) as file:
            pending = Path(file.name)
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(pending, 0o600)
        os.replace(pending, path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _read_json(path: Path):
    if path.stat().st_size > 4096:
        raise ValueError()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError()
    return data


def validate_key(value):
    if not isinstance(value, str) or not 16 <= len(value) <= 256 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError('Bitte einen gültigen ReefAPI-Schlüssel eingeben.')
    return value


class ReefSettings:
    def __init__(self, data_dir: Path, environment_key: str = ''):
        self.path = data_dir / 'reef.json'
        self.usage_path = data_dir / 'reef-usage.json'
        self._key = ''
        self.daily_credit_limit = 20
        self.delivery_postcode = ''
        self.generation = 0
        self.error = None
        self._usage_error = False
        self._day, self._spent = self._today(), 0
        self.source = 'environment' if environment_key else 'local' if self.path.exists() else 'none'
        try:
            if environment_key:
                self._key = validate_key(environment_key)
            elif self.path.exists():
                data = _read_json(self.path)
                if set(data) - {'api_key', 'daily_credit_limit', 'delivery_postcode'}:
                    raise ValueError()
                self._validate_limit(data['daily_credit_limit'])
                self._key = validate_key(data['api_key'])
                self.daily_credit_limit = data['daily_credit_limit']
                self.delivery_postcode = self._validate_postcode(data.get('delivery_postcode', ''))
        except (OSError, ValueError, KeyError):
            self._key = ''
            self.error = 'ReefAPI-Konfiguration ungültig. Bitte neu hinterlegen oder entfernen.'
        try:
            if self.usage_path.exists():
                data = _read_json(self.usage_path)
                day, spent = data['day'], data['reserved_credits']
                if set(data) != {'day', 'reserved_credits'} or not isinstance(day, str):
                    raise ValueError()
                if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
                    raise ValueError()
                if type(spent) is not int or spent < 0:
                    raise ValueError()
                self._day, self._spent = day, spent
        except (OSError, ValueError, KeyError, TypeError):
            self._usage_error = True

    @staticmethod
    def _today():
        return datetime.now(timezone.utc).strftime('%Y-%m-%d')

    @staticmethod
    def _validate_limit(limit):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('Das Tageslimit muss zwischen 1 und 500 Credits liegen.')

    @property
    def configured(self):
        return bool(self._key)

    def key(self):
        if not self._key:
            raise ValueError('ReefAPI-Zugang fehlt. API-Schlüssel unter Einstellungen hinterlegen.')
        return self._key

    @staticmethod
    def _validate_postcode(value):
        if not isinstance(value, str) or (value and not re.fullmatch(r'[0-9]{5}', value)):
            raise ValueError('Die Liefer-PLZ muss leer oder eine fünfstellige deutsche Postleitzahl sein.')
        return value

    def configure(self, key, limit, delivery_postcode=''):
        if self.source == 'environment':
            raise ValueError('ReefAPI wird über die Prozessumgebung verwaltet.')
        key = validate_key(key)
        self._validate_limit(limit)
        postcode = self._validate_postcode(delivery_postcode)
        _write_json(self.path, {'api_key': key, 'daily_credit_limit': limit, 'delivery_postcode': postcode})
        self._key, self.daily_credit_limit = key, limit
        self.source, self.error = 'local', None
        self.delivery_postcode = postcode
        self.generation += 1

    def clear(self):
        if self.source == 'environment':
            raise ValueError('ReefAPI wird über die Prozessumgebung verwaltet.')
        self.path.unlink(missing_ok=True)
        self._key, self.source, self.error = '', 'none', None
        self.delivery_postcode = ''
        self.generation += 1

    def reserve(self, credits):
        self.key()
        if type(credits) is not int or credits < 1:
            raise ValueError('Ungültiges Credit-Limit für diese Abfrage.')
        if self._usage_error:
            raise ValueError('Lokaler ReefAPI-Verbrauchszähler ist beschädigt; Abfragen gesperrt.')
        today = self._today()
        day, spent = (today, 0) if today > self._day else (self._day, self._spent)
        if spent + credits > self.daily_credit_limit:
            raise ValueError('Lokales ReefAPI-Tageslimit erreicht. Kostenpflichtige Abfragen werden nicht automatisch freigegeben.')
        try:
            _write_json(self.usage_path, {'day': day, 'reserved_credits': spent + credits})
        except OSError:
            raise ValueError('ReefAPI-Verbrauchszähler konnte nicht gespeichert werden; Abfrage gesperrt.') from None
        self._day, self._spent = day, spent + credits

    def status(self):
        spent = 0 if self._today() > self._day else self._spent
        return {'configured': self.configured, 'source': self.source,
                'daily_credit_limit': self.daily_credit_limit, 'reserved_credits_today': spent,
                'delivery_postcode': self.delivery_postcode,
                'error': ('Lokaler Verbrauchszähler beschädigt; ReefAPI-Abfragen gesperrt.'
                          if self._usage_error else self.error)}
