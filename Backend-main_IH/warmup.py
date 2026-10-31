"""
Pre-warming, run once when the server boots — not per call.

What "10 seconds before the bot responds" is actually made of, and what each piece here does
about it:

1. Silero VAD model load: every call constructs a fresh SileroVADAnalyzer (it holds
   per-call state, like a conversation's running silence/speech counters, so it can't be
   shared live across simultaneous calls). But loading the ONNX model file from disk and
   spinning up an onnxruntime.InferenceSession is the same work every time, and the first
   one after a cold boot is slow (disk read, session init). preload_vad_model() does that
   once at startup so the OS file cache is warm and onnxruntime's first-run cost is already
   paid before any caller dials in.

2. Cartesia STT/TTS connection: Pipecat already sets up all of a call's processors
   *concurrently* (confirmed by reading pipecat's own source — this isn't sequential), so
   the real bottleneck is the slower of the two Cartesia WebSocket handshakes (DNS + TCP +
   TLS + auth to api.cartesia.ai), not their sum. warmup_cartesia() opens a throwaway
   connection to Cartesia once at startup and closes it immediately. This can't make the
   *next* call's connection reuse this one (each call needs its own authenticated session —
   see PROJECT_DOCUMENTATION.md for why a live connection pool isn't safe to do here), but it
   does warm the DNS resolution and TLS/TCP path to Cartesia's servers, which measurably
   helps on Windows machines in particular (this project already needed an IPv4-forcing
   patch for exactly this kind of handshake slowness).

Both run once, concurrently, at server boot — see the end of bot.py's __main__ block.
Neither one blocks startup if it fails; a warmup failure just means the first real call pays
the full cold-start cost, same as before this existed.
"""

import asyncio
import os
import time

from loguru import logger


def preload_vad_model() -> None:
    """Synchronous — forces the Silero ONNX model and Smart Turn ONNX model to load once, into OS file cache."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

    start = time.monotonic()
    try:
        SileroVADAnalyzer()  # constructed, then immediately discarded — the load is the point
        LocalSmartTurnAnalyzerV3()  # pre-warms smart-turn ONNX session
        logger.info(f"VAD + Smart Turn models preloaded in {time.monotonic() - start:.2f}s")
    except Exception as e:
        logger.warning(f"VAD/Turn preload failed (non-fatal, first call will just be slower): {e}")


async def _warmup_one(name: str, connect_coro, disconnect_coro) -> None:
    start = time.monotonic()
    try:
        await connect_coro()
        logger.info(f"Cartesia {name} connection path warmed in {time.monotonic() - start:.2f}s")
    except Exception as e:
        logger.warning(f"Cartesia {name} warmup failed (non-fatal): {e}")
    finally:
        try:
            await disconnect_coro()
        except Exception:
            pass


async def warmup_cartesia() -> None:
    """Opens and immediately closes a throwaway connection to Cartesia's STT and TTS
    WebSocket endpoints, concurrently, to warm the DNS/TCP/TLS path before the first call."""
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        logger.warning("CARTESIA_API_KEY not set — skipping Cartesia warmup")
        return

    from pipecat.services.cartesia.stt import CartesiaSTTService
    from pipecat.services.cartesia.tts import CartesiaTTSService

    stt = CartesiaSTTService(api_key=api_key)
    tts = CartesiaTTSService(api_key=api_key)

    await asyncio.gather(
        _warmup_one("STT", stt._connect_websocket, stt._disconnect_websocket),
        _warmup_one("TTS", tts._connect_websocket, tts._disconnect_websocket),
    )


async def warmup_all() -> None:
    """Run every warmup step concurrently. Call once at server startup."""
    logger.info("Warming up VAD + Cartesia before accepting calls...")
    start = time.monotonic()
    loop = asyncio.get_running_loop()
    await asyncio.gather(
        loop.run_in_executor(None, preload_vad_model),
        warmup_cartesia(),
    )
    logger.info(f"Warmup complete in {time.monotonic() - start:.2f}s total")
