import argparse

import uvicorn

from takehome.config import Settings
from takehome.logging import configure_logging


def main():
    parser = argparse.ArgumentParser(description="Start one application process")
    parser.add_argument("service", choices=("api", "worker", "mock"))
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    if args.service == "api":
        from takehome.api import create_app
    elif args.service == "worker":
        from takehome.worker import create_app
    else:
        from takehome.mock import create_app
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=getattr(settings, f"app_{args.service}_port"),
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
