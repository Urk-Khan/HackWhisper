"""
HackWhisper — Hacktoberfest AI Voice Calling Agent (Pipecat 1.8.1 pipeline).

Features:
- Telephony inbound pipeline: Telnyx / SignalWire WebSockets (8kHz PSTN audio).
- STT & TTS: Cartesia (Ink-Whisper STT, Sonic TTS) with Silero VAD and Smart Turn.
- Automatic Event Management: Daily Full Fetch + Upsert + Historical Versioning
  from the Hacktoberfest Events API (https://hacktoberfest-api.mlh.com/api/events).
- AI Response Tools (LLM Function Calling):
  * search_events(city)
  * get_event_details(event_id)
  * get_registration_link(event_id)
  * send_event_email(email, event_id)
  * send_event_sms(phone_number, event_id)
  * transfer_to_human(reason)
  * end_call(farewell)
- Live audio recording and call transcript persistence.
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
from prompts import GREETINGS_EN, HACKTOBERFEST_SYSTEM_PROMPT, get_system_prompt
from tools import HACKTOBERFEST_TOOLS, INNOVENTIX_TOOLS
from warmup import warmup_all
import booking_db
import event_manager

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
            return HTMLResponse(
                content="<h1>HackWhisper — Hacktoberfest AI Voice Calling Agent Online</h1>",
                media_type="text/html",
            )

        return await call_next(request)


runner_module.app.add_middleware(
    TelephonyWebhookMiddleware, proxy_getter=lambda: globals().get("_tunnel_hostname")
)

# VAD parameters tuned for fast telephony responsiveness (8kHz PSTN/SIP audio).
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
    """gemini or anthropic — configured via LLM_PROVIDER in .env."""
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()

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
    elif provider == "gemini":
        from gemma_llm import GemmaLLMService

        # Gemma thinking is set to minimal (see gemma_llm.py). The cap stays at 1024
        # so a reply still fits if the model thinks more than expected.
        return GemmaLLMService(
            api_key=os.getenv("GEMINI_API_KEY"),
            settings=GemmaLLMService.Settings(
                model=os.getenv("GEMINI_MODEL", "gemma-4-26b-a4b-it"),
                system_instruction=get_system_prompt(),
                temperature=0.3,
                max_tokens=1024,
                thinking=GemmaLLMService.minimal_thinking(),
            ),
        )
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    """Run the voice bot for one incoming Hacktoberfest call session."""
    logger.info("Starting HackWhisper session")
    _call_start_time = __import__("time").time()

    stt = _build_stt_service()
    tts = _build_tts_service()
    llm = _build_llm_service()

    context = LLMContext(tools=HACKTOBERFEST_TOOLS)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VAD_PARAMS),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()]
            ),
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

    # Audio recorder for this call session
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

        # 1. Save audio recording
        if not call_state["rec_path"]:
            try:
                call_state["rec_path"] = await recorder.save()
            except Exception as e:
                logger.warning(f"recorder.save raised: {e}")

        # 2. Display call summary in console
        dur = __import__("time").time() - _call_start_time
        DemoDisplay.call_ended(dur, call_state["rec_path"])

        # 3. Persist call record to database
        try:
            await event_manager.save_call_record(
                call_id=runner_args.session_id,
                caller_phone=caller_phone,
                start_time=_call_start_time,
                messages=context.messages,
                explicit_outcome=call_state.get("outcome"),
            )
        except Exception as e:
            logger.warning(f"save_call_record raised: {e}")

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=8000,
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

        # Spoken greeting
        greeting = random.choice(GREETINGS_EN)
        DemoDisplay.bot_said(greeting)
        await worker.queue_frames([TTSSpeakFrame(greeting)])

        # Check caller context asynchronously
        if caller_phone:
            prev = await booking_db.get_customer_by_phone(caller_phone)
            if prev:
                context.add_message(
                    {
                        "role": "user",
                        "content": (
                            f"[CALLER CONTEXT: Caller previously received event details via {prev.get('last_channel', 'sms/email')}. "
                            f"Be welcoming and ready to assist with further Hacktoberfest events.]"
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
    try:
        import pyrnnoise  # noqa: F401
        from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

        return RNNoiseFilter()
    except ImportError:
        logger.warning("RNNoise not installed — running without background noise suppression.")
        return None


async def bot(runner_args: RunnerArguments):
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
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    import atexit

    from pipecat.runner.run import main

    _PORT = int(os.getenv("PORT", "7860"))
    _HOST = os.getenv("HOST", "0.0.0.0")

    async def _startup():
        (tunnel_process, tunnel_hostname), _, _ = await asyncio.gather(
            setup_tunnel_and_telnyx(_PORT),
            warmup_all(),
            event_manager.start_daily_sync_scheduler(),
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

    runner_module._print_dev_runner_banner = lambda: None
    _orig_uvicorn_run = runner_module.uvicorn.run

    def _quiet_uvicorn_run(*args, **kwargs):
        kwargs["log_level"] = "warning"
        return _orig_uvicorn_run(*args, **kwargs)

    runner_module.uvicorn.run = _quiet_uvicorn_run

    @runner_module.app.on_event("startup")
    def _on_app_startup():
        setup_demo_logging()

    setup_demo_logging()
    main()
