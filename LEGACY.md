# Legacy components

The supported production architecture is `dashboard.js` (Node HTTP/API) plus
`app.py --no-dashboard` (Python Bot Manager), supervised by
`docker-entrypoint.sh`. SQLite is their shared source of truth.

The following files are retained only as migration/reference material and are
not supported production entry points:

- `dca_bot.py`
- `dashboard.py`
- top-level `indodax_client.py`
- `flask-backup/`
- `index.php`

Do not add features or security fixes to these files. They may be removed in a
future major release after operators confirm that no external launcher imports
them. The active exchange implementation is `exchanges/indodax_client.py`.
