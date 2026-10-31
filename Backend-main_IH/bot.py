"""
Innoventix Hub — Pipecat voice pipeline (pipecat-ai 1.8.1 API).

Cascade pipeline: Telnyx audio in -> RNNoise background-noise suppression (if installed) ->
Cartesia STT -> LLM (English-only, with meeting-booking and lead-tracking tools) -> Cartesia
TTS -> Telnyx audio out. STT and TTS always use Cartesia. LLM provider is picked at runtime
from .env (LLM_PROVIDER: openai or anthropic).

The greeting is spoken directly (randomly chosen from prompts.GREETINGS_EN) without an LLM
call, so it starts the instant the call connects. The caller's phone number is looked up in
Supabase (booking_db.get_customer_by_phone) right after — if they're an existing customer,
their purchase history is injected into the LLM's context so it can consider a cross-sell,
per prompts.py's SCENARIO 3. This lookup runs while the greeting is already playing, not
before it, so it costs no perceptible delay.

No N8N anywhere — check_availability/book_meeting talk straight to Cal.com's v2 API
(cal_com.py), confirmations go out via email_sender.py, and everything gets logged to
Supabase (booking_db.py). See PROJECT_DOCUMENTATION.md for the full architecture writeup.

Run with:
    python bot.py

This one command does everything: starts a cloudflared tunnel, updates your Telnyx TeXML
Application's Voice URL automatically, pre-warms the VAD model and Cartesia's connection
path, then starts Pipecat's built-in server (default: http://localhost:7860), which serves
the Telnyx WebSocket at /ws.
"""

import asyncio
import os
import random
import socket
import sys

# Force IPv4 socket resolution on Windows to avoid AWS/Cartesia WebSocket handshake timeouts.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

# SignalWire & Twilio compatibility (Commented out — switched back to Telnyx)
# if os.getenv("SIGNALWIRE_PROJECT_ID"):
#     os.environ.setdefault("TWILIO_ACCOUNT_SID", os.getenv("SIGNALWIRE_PROJECT_ID", ""))
#     os.environ.setdefault("TWILIO_AUTH_TOKEN", os.getenv("SIGNALWIRE_API_TOKEN", ""))
#
# from pipecat.serializers.twilio import TwilioFrameSerializer
#
# _orig_twilio_init = TwilioFrameSerializer.__init__
#
# def _patched_twilio_init(self, *args, **kwargs):
#     if not kwargs.get("base_url"):
#         space_url = os.getenv("SIGNALWIRE_SPACE_URL")
#         if space_url:
#             kwargs["base_url"] = space_url.rstrip("/") + "/api/laml"
#     _orig_twilio_init(self, *args, **kwargs)
#
# TwilioFrameSerializer.__init__ = _patched_twilio_init

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_mute.mute_until_first_bot_complete_user_mute_strategy import (
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_mute.function_call_user_mute_strategy import (
    FunctionCallUserMuteStrategy,
)
from pipecat.turns.user_turn_strategies import (
    TranscriptionUserTurnStartStrategy,
    UserTurnStrategies,
    VADUserTurnStartStrategy,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

from auto_tunnel import setup_tunnel_and_telnyx
from call_recorder import CallRecorder
from demo_display import DemoDisplay, setup_demo_logging
from latency_logger import LatencyLogger
from prompts import GREETINGS_EN, INNOVENTIX_SYSTEM_PROMPT, get_system_prompt
from tools import INNOVENTIX_TOOLS
from warmup import warmup_all
import booking_db

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse
import pipecat.runner.run as runner_module


class TelephonyWebhookMiddleware(BaseHTTPMiddleware):
    """Intercepts telephony webhook calls at POST / to inject caller metadata and
    return the appropriate XML (SignalWire/Twilio LaML or Telnyx TeXML)."""

    def __init__(self, app, proxy_getter):
        super().__init__(app)
        self.proxy_getter = proxy_getter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/")
        if not path:
            path = "/"

        if request.method == "POST" and path in ("/", "/voice"):
            is_signalwire = bool(
                os.getenv("SIGNALWIRE_PROJECT_ID") or os.getenv("SIGNALWIRE_SPACE_URL")
            )
            proxy = self.proxy_getter() or request.headers.get("host", "localhost")
            if is_signalwire:
                try:
                    form = await request.form()
                    caller = form.get("From", "")
                    called = form.get("To", "")
                except Exception:
                    caller, called = "", ""

                param_xml = ""
                if caller:
                    param_xml += f'\n      <Parameter name="from_number" value="{caller}" />'
                if called:
                    param_xml += f'\n      <Parameter name="to_number" value="{called}" />'

                xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{proxy}/ws" track="inbound_track">{param_xml}
    </Stream>
  </Connect>
</Response>"""
                logger.info(f"Serving SignalWire LaML webhook (path={request.url.path}) for caller={caller}")
                return HTMLResponse(content=xml, media_type="application/xml")
            else:
                xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{proxy}/ws" bidirectionalMode="rtp"></Stream>
  </Connect>
</Response>"""
                logger.info(f"Serving Telnyx TeXML webhook (path={request.url.path})")
                return HTMLResponse(content=xml, media_type="application/xml")

        elif request.method == "POST" and path in ("/status", "/voice/status"):
            return HTMLResponse(content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>", media_type="application/xml")

        elif request.method == "GET" and path in ("/", "/voice", "/health"):
            return HTMLResponse(content="<h1>Innoventix Hub Voice Agent Online</h1>", media_type="text/html")

        return await call_next(request)


runner_module.app.add_middleware(
    TelephonyWebhookMiddleware, proxy_getter=lambda: globals().get("_tunnel_hostname")
)

# VAD parameters tuned for fast telephony responsiveness (8kHz PSTN/SIP audio).
# confidence=0.70 — speech threshold that reliably rejects line hiss and clicks while capturing normal voice.
# min_volume=0.04 — filters low digital background noise.
# start_secs=0.20 — 200ms voice detection ensures fast, responsive turn detection and barge-in.
# stop_secs=0.60 — 600ms silence threshold before turn end. Provides natural conversational rhythm,
# avoids Pipecat STT p99 timeout collapse, and reduces turn turnaround latency by ~600ms.
VAD_PARAMS = VADParams(
    confidence=0.70,
    min_volume=0.04,
    start_secs=0.20,
    stop_secs=0.60,
)


def _build_stt_service():
    """Cartesia only (Ink-Whisper)."""
    from pipecat.services.cartesia.stt import CartesiaSTTService

    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise ValueError("CARTESIA_API_KEY is not set in .env")
    return CartesiaSTTService(api_key=api_key)


def _build_tts_service():
    """Cartesia only (Sonic)."""
    from pipecat.services.cartesia.tts import CartesiaTTSService

    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise ValueError("CARTESIA_API_KEY is not set in .env")
    return CartesiaTTSService(
        api_key=api_key,
        settings=CartesiaTTSService.Settings(
            voice=os.getenv("CARTESIA_VOICE_ID", "86e30c1d-714b-4074-a1f2-1cb6b552fb49"),
        ),
    )


def _build_llm_service():
    """openai or anthropic — both cloud APIs, both require a key."""
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "anthropic":
        from pipecat.services.anthropic.llm import AnthropicLLMService

        return AnthropicLLMService(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            settings=AnthropicLLMService.Settings(
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
                system_instruction=get_system_prompt(),
                temperature=0.3,
                max_tokens=120,
            ),
        )
    elif provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        model_name = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
        return OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY"),
            settings=OpenAILLMService.Settings(
                model=model_name,
                system_instruction=get_system_prompt(),
                temperature=0.3,
                max_completion_tokens=120,
            ),
        )
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Run the voice bot for one incoming call session."""
    logger.info("Starting Innoventix Hub session")
    _call_start_time = __import__("time").time()

    stt = _build_stt_service()
    tts = _build_tts_service()
    llm = _build_llm_service()

    context = LLMContext(tools=INNOVENTIX_TOOLS)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VAD_PARAMS),
            # VAD-only start: prevents partial STT hypothesis jitter from triggering premature barge-in
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()]
            ),
            # MuteUntilFirstBotComplete: mutes only during initial greeting so connection clicks
            # don't interrupt greeting. Afterwards user is unmuted for normal barge-in.
            # FunctionCallUserMuteStrategy: mutes user during backend API tool calls.
            user_mute_strategies=[
                MuteUntilFirstBotCompleteUserMuteStrategy(),
                FunctionCallUserMuteStrategy(),
            ],
            user_turn_stop_timeout=1.2,
            audio_idle_timeout=1.8,
            user_idle_timeout=15.0,
        ),
    )

    call_data = runner_args.call_data
    caller_phone = None
    called_phone = None
    call_control_id = None
    if call_data:
        caller_phone = getattr(call_data, "from_number", None) or (
            call_data.get("from") if isinstance(call_data, dict) else None
        )
        called_phone = getattr(call_data, "to_number", None) or (
            call_data.get("to") if isinstance(call_data, dict) else None
        )
        call_control_id = getattr(call_data, "call_id", None) or (
            call_data.get("call_id") if isinstance(call_data, dict) else None
        )

    # Initialize non-blocking audio recorder for this call session
    recorder = CallRecorder(
        output_dir="call_recordings",
        caller_phone=caller_phone,
        call_id=runner_args.session_id,
        target_sample_rate=8000,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            recorder.input_tap(),
            stt,
            user_aggregator,
            llm,
            tts,
            LatencyLogger(),
            recorder.output_tap(),
            transport.output(),
            assistant_aggregator,
        ]
    )

    call_state = {
        "call_id": runner_args.session_id,
        "caller_phone": caller_phone,
        "start_time": _call_start_time,
        "outcome": None,
        "db_saved": False,
        "rec_path": None,
    }

    async def _finalize_call():
        if call_state["db_saved"]:
            return
        call_state["db_saved"] = True

        # 1. Save audio recording if not already saved
        if not call_state["rec_path"]:
            try:
                call_state["rec_path"] = await recorder.save()
            except Exception as e:
                logger.warning(f"recorder.save raised: {e}")

        # 2. Display call summary in console
        dur = __import__("time").time() - _call_start_time
        DemoDisplay.call_ended(dur, call_state["rec_path"])

        # 3. Guaranteed save of call record (transcript, duration, outcome) to Supabase
        try:
            await booking_db.save_call_record(
                call_id=runner_args.session_id,
                caller_phone=caller_phone,
                start_time=_call_start_time,
                messages=context.messages,
                explicit_outcome=call_state.get("outcome"),
            )
        except Exception as e:
            logger.warning(f"save_call_record raised unexpectedly: {e}")

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=8000,  # Telnyx/SignalWire PSTN audio is 8kHz
            audio_out_sample_rate=8000,
        ),
        app_resources={
            "caller_phone": caller_phone,
            "called_phone": called_phone,
            "call_control_id": call_control_id,
            "session_id": runner_args.session_id,
            "call_state": call_state,
        },
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Call connected, caller={caller_phone}")
        DemoDisplay.call_started(caller_phone, runner_args.session_id)

        # The greeting is spoken via TTS. assistant_aggregator automatically records
        # the spoken greeting in context, so no duplicate manual context.add_message is needed.
        greeting = random.choice(GREETINGS_EN)
        DemoDisplay.bot_said(greeting)
        await worker.queue_frames([TTSSpeakFrame(greeting)])

        # Runs while the greeting is already playing rather than blocking it — it only needs
        # to land in context before the LLM's first real inference, which can't happen until
        # the caller finishes speaking their first sentence, comfortably longer than this
        # Supabase round-trip.
        customer = await booking_db.get_customer_by_phone(caller_phone) if caller_phone else None
        if customer:
            purchased = customer.get("purchased_services") or "no specific services on file"
            context.add_message(
                {
                    "role": "user",
                    "content": (
                        f"[KNOWN CALLER CONTEXT: This caller is an existing customer, "
                        f"name={customer.get('name', 'unknown')}, purchased={purchased}. "
                        f"Use this per the CROSS-SELL scenario in your instructions — never "
                        f"mention this context to the caller directly.]"
                    ),
                }
            )

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        DemoDisplay.caller_speaking()

    @user_aggregator.event_handler("on_user_turn_message_added")
    async def on_user_turn_message_added(aggregator, message):
        content = getattr(message, "content", None) or (
            message.get("content") if isinstance(message, dict) else str(message)
        )
        if content:
            DemoDisplay.caller_said(content)

    @user_aggregator.event_handler("on_user_turn_inference_triggered")
    async def on_user_turn_inference_triggered(aggregator, strategy):
        DemoDisplay.bot_thinking()

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        content = getattr(message, "content", None) or (
            message.get("content") if isinstance(message, dict) else str(message)
        )
        if content:
            DemoDisplay.bot_said(content)

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        """Re-prompt when the caller has been silent too long (user_idle_timeout seconds).
        Prevents dead-air situations where STT missed the caller's speech or the caller
        drifted away. Matches the system prompt's instruction: 'If the caller goes silent,
        gently prompt once ("Hello, are you still there?")'. Only prompts once per idle
        cycle — the timer resets when the caller next speaks."""
        logger.info("User idle — sending re-prompt")
        reprompt = "Hello, are you still there?"
        DemoDisplay.bot_said(reprompt)
        await worker.queue_frames([TTSSpeakFrame(reprompt)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Call ended (client disconnected)")
        await _finalize_call()
        await worker.cancel()

    try:
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()
    finally:
        await _finalize_call()


def _build_noise_filter():
    """Reduces background noise (fans, traffic, TV, general ambient sound) on incoming
    caller audio, before it reaches VAD/STT — fewer false interruptions from noise, cleaner
    transcription. Free, fully local, no account or API key (unlike Krisp VIVA/Arctan/AIC,
    which do full voice ISOLATION — actually separating two simultaneous human voices — but
    require a paid SDK license; see PROJECT_DOCUMENTATION.md if RNNoise isn't enough for a
    consistently noisy environment).

    Guarded because the `rnnoise` extra is optional: if it isn't installed, the bot should
    still run exactly as before rather than crash on startup.
    """
    try:
        import pyrnnoise  # noqa: F401
        from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

        return RNNoiseFilter()
    except ImportError:
        logger.warning(
            "RNNoise not installed — running without background noise suppression. "
            'Run `pip install "pipecat-ai[rnnoise]"` to enable it.'
        )
        return None


async def bot(runner_args: RunnerArguments):
    """Entry point the Pipecat runner calls for each new connection."""
    transport_params = {
        "telnyx": lambda: FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_filter=_build_noise_filter(),
        ),
        "twilio": lambda: FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_filter=_build_noise_filter(),
        ),
    }

    transport = await create_transport(runner_args, transport_params)

    call_data = runner_args.call_data
    caller_phone = None
    if call_data:
        caller_phone = getattr(call_data, "from_number", None) or (
            call_data.get("from") if isinstance(call_data, dict) else None
        )
        logger.info(f"From number: {caller_phone}")

    await run_bot(transport, runner_args)


if __name__ == "__main__":
    import atexit

    from pipecat.runner.run import main

    _PORT = int(os.getenv("PORT", "7860"))
    _HOST = os.getenv("HOST", "0.0.0.0")

    async def _startup():
        (tunnel_process, tunnel_hostname), _ = await asyncio.gather(
            setup_tunnel_and_telnyx(_PORT),
            warmup_all(),
        )
        return tunnel_process, tunnel_hostname

    _tunnel_process, _tunnel_hostname = asyncio.run(_startup())

    if _tunnel_process is not None:

        @atexit.register
        def _cleanup_tunnel():
            logger.info("Stopping cloudflared tunnel...")
            _tunnel_process.terminate()

    active_transport = (
        "twilio"
        if (os.getenv("SIGNALWIRE_PROJECT_ID") or os.getenv("SIGNALWIRE_SPACE_URL"))
        else "telnyx"
    )
    sys.argv = [
        sys.argv[0],
        "-t",
        active_transport,
        "--host",
        _HOST,
        "--port",
        str(_PORT),
    ]
    if _tunnel_hostname:
        sys.argv += ["--proxy", _tunnel_hostname]
    else:
        logger.warning("No tunnel hostname available — the server will only be reachable locally.")

    provider_name = "SignalWire" if active_transport == "twilio" else "Telnyx"
    phone_number = os.getenv("TELNYX_PHONE_NUMBER", "+1 (855) 501-0702")
    DemoDisplay.banner(phone_number, provider_name)

    # Silence Pipecat's noisy ASCII runner banner and quiet Uvicorn access/lifecycle logs
    runner_module._print_dev_runner_banner = lambda: None
    _orig_uvicorn_run = runner_module.uvicorn.run

    def _quiet_uvicorn_run(*args, **kwargs):
        kwargs["log_level"] = "warning"
        return _orig_uvicorn_run(*args, **kwargs)

    runner_module.uvicorn.run = _quiet_uvicorn_run

    # Re-apply setup_demo_logging once FastAPI boots to prevent main() from resetting sinks
    @runner_module.app.on_event("startup")
    def _on_app_startup():
        setup_demo_logging()

    setup_demo_logging()
    main()

