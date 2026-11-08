"""Portable entry point; a single process bound only to the loopback interface."""
import argparse
import json
import logging

import uvicorn


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'level':record.levelname, 'logger':record.name,
                           'message':record.getMessage()}, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description='DealChecker lokal starten')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port muss zwischen 1024 und 65535 liegen')
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    from .app import create_app
    print(f'DealChecker: http://127.0.0.1:{args.port} — Beenden mit Strg+C', flush=True)
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, workers=1, access_log=False,
                proxy_headers=False, log_config=None)


if __name__ == '__main__':
    main()
