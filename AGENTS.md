# DealChecker

This is the sole DealChecker repository. Do not modify sibling projects.
Python 3.11+; FastAPI, SQLite, plain HTML/CSS/JS. Default listener is 127.0.0.1.
Use integer EUR cents. Unknown shipping is not zero. Never invent historical prices.
Network adapters must use the shared guarded HTTP client, bounded calls and explicit source policies.
Tests use synthetic fixtures clearly labelled as such; live verification is opt-in.
No credentials, user databases or local logs in Git. No paid services, purchases, bypasses or automatic startup changes.
Run `python -m pytest` and `python -m compileall -q dealchecker` before delivery.
Workers use separate worktrees/branches and edit only their assigned files; parent integrates and reviews.
