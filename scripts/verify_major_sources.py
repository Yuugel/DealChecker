"""Opt-in LIVE acceptance against public shops, isolated from personal watches.

Usage: python scripts/verify_major_sources.py --output artifacts/unique-run
Does not load provider keys, create subscriptions or send notifications.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from dealchecker.adapters import build_registry
from dealchecker.db import Database
from dealchecker.models import WatchInput
from dealchecker.network import SafeHttpClient
from dealchecker.service import Engine


EXACT = [
    ('A: Nicer Dicer 16', 'lidl', 'https://www.lidl.de/p/genius-nicer-dicer-nicer-dicer-fusion-smart-16-teilig-1-25-l-auffangbehaelter-klingen-aus-edelstahl/p100227834', None),
    ('B: MediaMarkt green PC controller', 'mediamarkt', 'https://www.mediamarkt.de/de/product/_8bitdo-ultimate-2c-gamepad-grun-157963406.html', 4500),
    ('B: Saturn same controller', 'saturn', 'https://www.saturn.de/de/product/_8bitdo-ultimate-2c-gamepad-grun-157963406.html', 4500),
    ('C: KOENIC 8 kg', 'mediamarkt', 'https://www.mediamarkt.de/de/product/_koenic-kwm-8142-a-10-inv-waschmaschine-8-kg-1351-umin-a-3014762.html', 40000),
]


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'LIVE public shop requests', 'started_at': datetime.now(timezone.utc).isoformat(), 'checks': []}
    exact_ids = []
    for phase in (1, 2):
        # Closing and rebuilding all resources exercises a real persisted restart
        # and avoids mistaking a local cache read for a second retailer observation.
        db = Database(output / 'verification.sqlite3')
        client = SafeHttpClient()
        engine = Engine(db, build_registry(client))
        try:
            if phase == 1:
                for name, query, sources, budget in [
                    ('A discovery', 'Nicer Dicer Fusion Smart 16', ['mediamarkt', 'alternate', 'berrybase'], None),
                    ('B discovery', '8BitDo Ultimate 2C', ['mediamarkt', 'alternate', 'berrybase'], None),
                    ('C discovery', 'Waschmaschine 8 kg', ['mediamarkt', 'alternate'], 40000),
                ]:
                    watch = db.create_watch(WatchInput(name=name, kind='discovery', query=query,
                                                       source_ids=sources, max_price_cents=budget))
                    result = await engine.check_watch(watch['id'])
                    report['checks'].append({'phase': phase, 'name': name, **result})
                    print(json.dumps(report['checks'][-1], ensure_ascii=False), flush=True)
                for name, source, url, target in EXACT:
                    watch = db.create_watch(WatchInput(name=name, url=url, source_ids=[source], target_cents=target))
                    exact_ids.append(watch['id'])
            for watch_id in exact_ids:
                result = await engine.check_watch(watch_id)
                report['checks'].append({'phase': phase, 'name': db.get_watch(watch_id)['name'], **result})
                print(json.dumps(report['checks'][-1], ensure_ascii=False), flush=True)
            if phase == 1:
                report['events_before_restart'] = len(db.list_events())
            else:
                report['offers'] = db.list_offers()
                report['comparisons'] = db.comparisons()
                report['events_after_restart'] = len(db.list_events())
                report['histories'] = {str(offer['id']): db.history(offer['id']) for offer in db.list_offers()
                                       if offer['watch_id'] in exact_ids}
                report['sqlite_integrity'] = db.conn.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            await engine.close()
            await client.close()
            db.close()
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('events_before_restart', 'events_after_restart', 'sqlite_integrity')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output.resolve()))
