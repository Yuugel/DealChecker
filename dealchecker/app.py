"""Local API and coordinated resource lifetime."""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictBool, Field, SecretStr

from . import __version__
from .adapters import build_registry, source_catalog
from .config import Config, InstanceLock
from .db import Database
from .models import WatchInput
from .network import SafeHttpClient
from .notifications import DiscordNotifier
from .scheduler import Scheduler
from .security import LocalOnlyMiddleware
from .service import Engine
from .links import resolve_link
from .notification_settings import NotificationSettings
from .reef_settings import ReefSettings


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paused: StrictBool | None = None
    black_friday_until: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    notification_enabled: StrictBool | None = None


class LinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=1000)


class NotificationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    webhook_url: SecretStr


class ReefInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr
    daily_credit_limit: int = Field(default=20, ge=1, le=500, strict=True)
    delivery_postcode: str = Field(default='', max_length=5, pattern=r'^([0-9]{5})?$')


def create_app(config: Config | None = None, *, adapters=None, start_scheduler=True):
    config = config or Config.from_env()
    secrets = NotificationSettings(config.data_dir)

    @asynccontextmanager
    async def lifespan(app):
        with InstanceLock(config.data_dir / 'instance.lock'):
            db = Database(config.data_dir / 'dealchecker.sqlite3')
            client = SafeHttpClient()
            engine = scheduler = None
            try:
                reef = ReefSettings(config.data_dir, config.reef_api_key)
                registry = build_registry(client, config.feeds, reef_settings=reef) if adapters is None else adapters
                source = 'environment' if config.discord_webhook else 'local' if secrets.path.exists() else 'none'
                notification_error = None
                try:
                    webhook = config.discord_webhook or secrets.load()
                    notifier = DiscordNotifier(client, webhook) if webhook else None
                except ValueError:
                    notifier = None
                    notification_error = 'Benachrichtigungskonfiguration ungültig. Bitte neu hinterlegen oder entfernen.'
                    db.update_settings({'notification_enabled':False})
                db.bind_notification_destination(notifier.webhook_url if notifier else '')
                engine = Engine(db, registry, notifier)
                scheduler = Scheduler(engine)
                app.state.db, app.state.engine, app.state.registry = db, engine, registry
                app.state.client = client
                app.state.reef = reef
                app.state.notification_source = source
                app.state.notification_error = notification_error
                if start_scheduler:
                    await scheduler.start()
                yield
            finally:
                if scheduler is not None:
                    await scheduler.stop()
                if engine is not None:
                    await engine.close()
                await client.close()
                db.close()

    app = FastAPI(title='DealChecker', version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(LocalOnlyMiddleware)
    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static, check_dir=False), name='static')
    link_slots = asyncio.Semaphore(4)

    @app.exception_handler(RequestValidationError)
    async def safe_validation(request, exc):
        if request.url.path in {'/api/notifications/config', '/api/providers/reef'}:
            return JSONResponse(status_code=422, content={'detail':'Ungültige Zugangskonfiguration'})
        return await request_validation_exception_handler(request, exc)

    @app.get('/')
    async def index():
        return FileResponse(static / 'index.html')

    @app.get('/api/health')
    async def health():
        return {'status':'ok', 'version':__version__}

    @app.get('/api/state')
    async def state():
        db = app.state.db
        watches, checks = db.list_watches(), db.list_checks()
        minimums = {source_id: getattr(adapter, 'min_interval_seconds', 0)
                    for source_id, adapter in app.state.registry.items()}
        for watch in watches:
            watch['running'] = app.state.engine.is_running(watch['id'])
            watch['effective_source_intervals'] = db.effective_source_intervals(watch['id'], minimums)
        sources = source_catalog(app.state.registry)
        statuses = db.source_statuses()
        for source in sources:
            source.update(statuses.get(source['id'], {
                'last_checked':None, 'last_success':None, 'last_error':None,
            }))
        return {'watches':watches, 'offers':db.list_offers(), 'comparisons':db.comparisons(), 'events':db.list_events(),
                'checks':checks, 'settings':db.get_settings(), 'sources':sources,
                'notification_configured':bool(app.state.engine.notifier),
                'notification_configuration_error':app.state.notification_error,
                'notification_configuration_source':app.state.notification_source,
                'reef':app.state.reef.status()}

    @app.put('/api/providers/reef')
    async def configure_reef(payload: ReefInput):
        if app.state.reef.source == 'environment':
            raise HTTPException(409, 'ReefAPI wird über die Prozessumgebung verwaltet.')
        try:
            app.state.reef.configure(payload.api_key.get_secret_value().strip(), payload.daily_credit_limit,
                                    payload.delivery_postcode)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError:
            raise HTTPException(500, 'ReefAPI-Zugang konnte nicht lokal gespeichert werden.') from None
        return app.state.reef.status()

    @app.delete('/api/providers/reef')
    async def remove_reef():
        if app.state.reef.source == 'environment':
            raise HTTPException(409, 'ReefAPI wird über die Prozessumgebung verwaltet.')
        try:
            app.state.reef.clear()
        except OSError:
            raise HTTPException(500, 'ReefAPI-Zugang konnte nicht entfernt werden.') from None
        return app.state.reef.status()

    @app.put('/api/notifications/config')
    async def configure_notification(payload: NotificationInput):
        if config.discord_webhook:
            raise HTTPException(409, 'Webhook wird über die Prozessumgebung verwaltet.')
        value = payload.webhook_url.get_secret_value().strip()
        try:
            if not value or len(value) > 2048:
                raise ValueError()
            notifier = DiscordNotifier(app.state.client, value)
        except ValueError:
            raise HTTPException(422, 'Bitte eine gültige Discord-Webhook-Adresse eingeben.') from None
        async with app.state.engine._delivery_lock:
            try:
                secrets.save(value)
            except OSError:
                raise HTTPException(500, 'Webhook konnte nicht lokal gespeichert werden.') from None
            app.state.db.bind_notification_destination(value)
            app.state.engine.notifier = notifier
            app.state.notification_source = 'local'
            app.state.notification_error = None
        return {'configured':True, 'source':'local'}

    @app.delete('/api/notifications/config')
    async def remove_notification():
        if config.discord_webhook:
            raise HTTPException(409, 'Webhook wird über die Prozessumgebung verwaltet.')
        async with app.state.engine._delivery_lock:
            try:
                secrets.clear()
            except OSError:
                raise HTTPException(500, 'Webhook konnte nicht entfernt werden.') from None
            app.state.db.bind_notification_destination('')
            app.state.db.update_settings({'notification_enabled':False})
            app.state.engine.notifier = None
            app.state.notification_source = 'none'
            app.state.notification_error = None
        return {'configured':False, 'source':'none'}

    def validate_sources(watch):
        if any(s not in app.state.registry or not getattr(app.state.registry[s], 'configured', True)
               for s in watch.source_ids):
            raise HTTPException(422, 'Eine gewählte Quelle ist nicht konfiguriert')
        if watch.kind == 'discovery' and any(not getattr(app.state.registry[s], 'supports_search', True) for s in watch.source_ids):
            raise HTTPException(422, 'Eine gewählte Quelle unterstützt nur Produktlinks, keine freie Suche.')

    @app.post('/api/links/resolve')
    async def link_preview(payload: LinkInput):
        try:
            async with link_slots:
                return await resolve_link(payload.url.strip(), app.state.registry)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(422, str(exc)[:300]) from None

    @app.post('/api/watches', status_code=201)
    async def add_watch(watch: WatchInput):
        validate_sources(watch)
        return app.state.db.create_watch(watch)

    @app.put('/api/watches/{watch_id}')
    async def update_watch(watch_id: int, watch: WatchInput):
        validate_sources(watch)
        if not app.state.db.get_watch(watch_id):
            raise HTTPException(404, 'Überwachung nicht gefunden')
        if app.state.engine.is_running(watch_id):
            raise HTTPException(409, 'Prüfung läuft; bitte danach bearbeiten')
        return app.state.db.update_watch(watch_id, watch)

    @app.delete('/api/watches/{watch_id}')
    async def delete_watch(watch_id: int):
        if app.state.engine.is_running(watch_id):
            raise HTTPException(409, 'Prüfung läuft; bitte danach entfernen')
        if not app.state.db.delete_watch(watch_id):
            raise HTTPException(404, 'Überwachung nicht gefunden')
        return {'deleted':True}

    @app.post('/api/watches/{watch_id}/check')
    async def check(watch_id: int):
        if not app.state.db.get_watch(watch_id):
            raise HTTPException(404, 'Überwachung nicht gefunden')
        return await app.state.engine.check_watch(watch_id)

    @app.get('/api/offers/{offer_id}/history')
    async def history(offer_id: int):
        return app.state.db.history(offer_id)

    @app.put('/api/settings')
    async def settings(patch: SettingsPatch):
        values = patch.model_dump(exclude_none=True)
        if values.get('black_friday_until', 0) > time.time() + 7 * 86400 + 2:
            raise HTTPException(422, 'Black-Friday-Modus ist auf maximal sieben Tage begrenzt')
        if values.get('notification_enabled') and not app.state.engine.notifier:
            raise HTTPException(422, 'Bitte zuerst unter Benachrichtigungen einen Discord-Webhook hinterlegen.')
        try:
            return app.state.db.update_settings(values)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    return app
