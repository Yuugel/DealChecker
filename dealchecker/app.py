"""Local API and coordinated resource lifetime."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictBool, Field

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


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paused: StrictBool | None = None
    black_friday_until: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    notification_enabled: StrictBool | None = None


def create_app(config: Config | None = None, *, adapters=None, start_scheduler=True):
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app):
        with InstanceLock(config.data_dir / 'instance.lock'):
            db = Database(config.data_dir / 'dealchecker.sqlite3')
            client = SafeHttpClient()
            engine = scheduler = None
            try:
                registry = build_registry(client, config.feeds) if adapters is None else adapters
                notifier = DiscordNotifier(client, config.discord_webhook) if config.discord_webhook else None
                engine = Engine(db, registry, notifier)
                scheduler = Scheduler(engine)
                app.state.db, app.state.engine, app.state.registry = db, engine, registry
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
                'notification_configured':bool(config.discord_webhook)}

    def validate_sources(watch):
        if any(s not in app.state.registry for s in watch.source_ids):
            raise HTTPException(422, 'Eine gewählte Quelle ist nicht konfiguriert')

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
        if values.get('notification_enabled') and not config.discord_webhook:
            raise HTTPException(422, 'Discord-Webhook fehlt in der lokalen Umgebungskonfiguration')
        try:
            return app.state.db.update_settings(values)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    return app
