"""Entry point: `python -m radio_discord_bridge` or `radio-discord-bridge`."""
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

# Search for .env in: cwd (and parents), then alongside the package's parent
# directory (project root). Windows-friendly because we use Path, not shell
# globbing, and we log the resolved path for easy debugging.
_PACKAGE_DIR = Path(__file__).resolve().parent
_CANDIDATES = [
    find_dotenv(usecwd=True),  # walks up from cwd
    str(_PACKAGE_DIR.parent / ".env"),  # project root next to the package
    str(_PACKAGE_DIR / ".env"),  # inside the package itself
]
_dotenv_path = next((p for p in _CANDIDATES if p and Path(p).is_file()), None)
_loaded = bool(_dotenv_path) and load_dotenv(_dotenv_path, override=False)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("radio_discord_bridge")
    if _loaded:
        log.info(".env loaded from: %s", _dotenv_path)
    else:
        log.warning(
            ".env not found (cwd=%s) — using process env. Searched: %s",
            os.getcwd(),
            [p for p in _CANDIDATES if p],
        )

    # Import after dotenv load so Config dataclass defaults pick up the values.
    from .bridge import Bridge
    from .config import Config

    cfg = Config()
    cfg.validate()

    log.info(
        "config: discord guild=%d channel=%d  vepg4 mcast=%s:%d ssrc=0x%08x",
        cfg.discord_guild_id, cfg.discord_voice_channel_id,
        cfg.mcast_group, cfg.rtp_port, cfg.our_ssrc,
    )

    async def runner():
        # py-cord's discord.Client.__init__() calls asyncio.get_event_loop(),
        # which on Python 3.14 raises if no running loop. Construct here.
        bridge = Bridge(cfg)

        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def _handle_signal(*_args):
            log.info("shutdown requested")
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except NotImplementedError:
                pass

        run_task = asyncio.create_task(bridge.run())
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        await bridge.shutdown()
        for t in done:
            if t is run_task and t.exception():
                raise t.exception()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
