# Project architecture

Runtime code is grouped by responsibility:

- `app/`: process entry points, web application, health server, and configuration.
- `bot/`: Telegram handlers, admin commands, keyboards, and user-facing text.
- `services/`: shared business logic and integrations.
- `database/`: database implementation and schema operations.
- `api/`: admin HTTP API and dashboard.
- `tests/`: automated tests.
- `docs/`: architecture and development documentation.

The root-level Python modules remain compatibility entry points for existing
deployment commands, scripts, and external imports. New code should import
from the corresponding package directly.
