"""Entry point: `python -m radio_discord_bridge` or `radio-discord-bridge`."""
import asyncio
import logging
import signal
import sys

from .bridge import Bridge
from .config import Config


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("radio_discord_bridge")

    cfg = Config()
    cfg.validate()

    log.info(
        "config: discord guild=%d channel=%d  vepg4 mcast=%s:%d ssrc=0x%08x",
        cfg.discord_guild_id, cfg.discord_voice_channel_id,
        cfg.mcast_group, cfg.rtp_port, cfg.our_ssrc,
    )

    bridge = Bridge(cfg)

    async def runner():
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
