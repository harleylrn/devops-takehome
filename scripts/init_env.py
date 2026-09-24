"""Create local-only credentials without overwriting an existing environment."""

import os
import secrets
from pathlib import Path


def main():
    path = Path(__file__).resolve().parents[1] / ".env"
    rabbit = secrets.token_hex(24)
    redis = secrets.token_hex(24)
    content = (
        "RABBITMQ_USER=takehome\n"
        f"RABBITMQ_PASSWORD={rabbit}\nREDIS_PASSWORD={redis}\n"
        f"RABBITMQ_URL=amqp://takehome:{rabbit}@127.0.0.1:5672/\n"
        f"REDIS_URL=redis://:{redis}@127.0.0.1:6379/0\n"
        "QUEUE_NAME=jobs\nEXTERNAL_SERVICE_URL=http://127.0.0.1:8081/infer\n"
        "MOCK_MODE=normal\nMOCK_DELAY_SECONDS=8\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Keeping existing .env")
        return
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    print("Created local .env (do not commit or share it)")


if __name__ == "__main__":
    main()
