"""Opt-in Telegram smoke test for a dedicated, non-production test bot."""

import asyncio
import os
import sys
from pathlib import Path

from telethon import TelegramClient, events


async def main() -> int:
    if os.getenv("E2E_ALLOW_TELEGRAM") != "1":
        print("Set E2E_ALLOW_TELEGRAM=1 to explicitly enable the Telegram smoke test.")
        return 2

    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    bot_username = os.getenv("E2E_BOT_USERNAME", "").strip()
    session_path = os.getenv("E2E_SESSION_PATH", "").strip()
    missing = [
        name
        for name, value in (
            ("API_ID", api_id),
            ("API_HASH", api_hash),
            ("E2E_BOT_USERNAME", bot_username),
            ("E2E_SESSION_PATH", session_path),
        )
        if not value
    ]
    if missing:
        print("Missing required environment variables: " + ", ".join(missing))
        return 2
    if bot_username.lower() in {"@blgh_bot", "blgh_bot"}:
        print("Refusing the known production bot; configure a dedicated test bot.")
        return 2

    try:
        numeric_api_id = int(api_id)
    except ValueError:
        print("API_ID must be an integer.")
        return 2

    session = Path(session_path).expanduser().resolve()
    session.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session), numeric_api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("The configured test session is not authorized.")
            return 1

        target = await client.get_entity(bot_username)
        loop = asyncio.get_running_loop()
        response: asyncio.Future[str] = loop.create_future()

        async def capture_reply(event) -> None:
            if not response.done() and event.message.message:
                response.set_result(event.message.message)

        client.add_event_handler(
            capture_reply,
            events.NewMessage(from_users=target),
        )
        await client.send_message(target, "/start")
        try:
            reply = await asyncio.wait_for(response, timeout=20)
        except TimeoutError:
            print("The test bot did not reply to /start within 20 seconds.")
            return 1

        print("The dedicated test bot replied to /start:")
        print(reply[:500])
        return 0
    finally:
        await client.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
