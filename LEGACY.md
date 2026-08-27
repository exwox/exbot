# Legacy components

The supported production architecture is `dashboard.js` (Node HTTP/API) plus
`app.py --no-dashboard` (Python Bot Manager), supervised by
`docker-entrypoint.sh`. SQLite is their shared source of truth.

The following files are retained only as migration/reference material and are
not supported production entry points. They have been archived under `legacy/`
(see plan.md Fase 4):

- `legacy/dca_bot.py`
- `legacy/dashboard.py`
- `legacy/indodax_client.py` (top-level, legacy)
- `legacy/flask-backup/`
- `legacy/index.php`

Do not add features or security fixes to these files, and do not import them
from supported code paths. They may be removed entirely in a future major
release after operators confirm that no external launcher imports them. The
active exchange implementation is `exchanges/indodax_client.py`.
