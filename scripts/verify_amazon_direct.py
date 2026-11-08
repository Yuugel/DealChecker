"""Opt-in LIVE Amazon checks without accounts; data stays under --output.

Run: python scripts/verify_amazon_direct.py --output artifacts/unique-run
Uses ordinary public pages only. No notifications, provider keys or purchases.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from dealchecker.adapters import build_registry
from dealchecker.db import Database
from dealchecker.links import resolve_link
from dealchecker.models import WatchInput
from dealchecker.network import SafeHttpClient
from dealchecker.service import Engine


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'LIVE Amazon public HTML, no API account',
              'started_at': datetime.now(timezone.utc).isoformat(), 'checks': []}
    watch_ids = []
    try:
        for phase in (1, 2):
            db = Database(output / 'verification.sqlite3')
            client = SafeHttpClient()
            registry = build_registry(client)
            engine = Engine(db, registry)
            try:
                if phase == 1:
                    discovery = db.create_watch(WatchInput(name='Live Amazon discovery', kind='discovery',
                        query='8BitDo Ultimate 2C', source_ids=['amazon']))
                    result = await engine.check_watch(discovery['id'])
                    report['checks'].append({'phase': phase, 'kind': 'discovery', **result})
                    print(json.dumps(report['checks'][-1]), flush=True)
                    for asin, prime in (('B0D736BCNM', False), ('B0DCFG91SV', False), ('B0DCFG91SV', True)):
                        url = f'https://www.amazon.de/dp/{asin}'
                        preview = await resolve_link(url, registry, amazon_prime=prime)
                        selected = preview['offers'][0]
                        report.setdefault('previews', []).append(preview)
                        watch = db.create_watch(WatchInput(name=selected['name'][:200], url=url,
                            product_id=asin, variant=selected['variant'], source_ids=['amazon'],
                            target_cents=10000, amazon_prime=prime))
                        watch_ids.append(watch['id'])
                for watch_id in watch_ids:
                    result = await engine.check_watch(watch_id)
                    report['checks'].append({'phase': phase, 'watch_id': watch_id, 'kind': 'exact', **result})
                    print(json.dumps(report['checks'][-1]), flush=True)
                report[f'events_phase_{phase}'] = db.list_events()
                if phase == 2:
                    report['offers'] = db.list_offers()
                    report['histories'] = {str(o['id']): db.history(o['id']) for o in db.list_offers()
                                           if o['watch_id'] in watch_ids}
                    report['sqlite_integrity'] = db.conn.execute('PRAGMA integrity_check').fetchone()[0]
            finally:
                await engine.close()
                await client.close()
                db.close()
        assert all(c['status'] in ('ok', 'partial') and c['offers'] > 0 for c in report['checks'])
        # A real seller/price-basis change legitimately splits an offer history.
        # Verify persisted observations per watch instead of assuming stable keys.
        counts = {watch_id: sum(len(report['histories'][str(o['id'])]) for o in report['offers']
                               if o['watch_id'] == watch_id) for watch_id in watch_ids}
        report['observations_per_watch'] = counts
        assert all(count == 2 for count in counts.values())
        assert report['sqlite_integrity'] == 'ok'
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args().output.resolve()))
