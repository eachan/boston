from __future__ import annotations

from boston.config import load_config
from boston.logging_setup import configure_logging
from boston.referee import BostonReferee
from boston.storage import Storage


def main() -> None:
    config = load_config()
    configure_logging(config.log_path)
    storage = Storage(config.database_path)
    referee = BostonReferee(config, storage)
    referee.run_forever()


if __name__ == "__main__":
    main()
