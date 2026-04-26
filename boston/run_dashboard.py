from __future__ import annotations

import uvicorn

from boston.config import load_config
from boston.dashboard import create_dashboard_app
from boston.logging_setup import configure_logging
from boston.storage import Storage


def main() -> None:
    config = load_config()
    configure_logging(config.log_path)
    storage = Storage(config.database_path)
    app = create_dashboard_app(config, storage)
    uvicorn.run(app, host=config.dashboard.host, port=config.dashboard.port)


if __name__ == "__main__":
    main()
