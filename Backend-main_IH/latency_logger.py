"""
Logs per-turn STT/LLM/TTS latency (time-to-first-byte) to the console.

Pipecat already computes these numbers internally whenever `enable_metrics=True` is set on
the PipelineWorker (bot.py has this on) — they just weren't being captured anywhere. This is
the fastest way to actually see them, which is what a Pipecat-vs-Retell latency comparison
needs real numbers for.

To log these into Supabase instead of just the console, call
`supabase_client.log_latency_row(...)` from inside process_frame below instead of (or in
addition to) the logger.info call.
"""

from loguru import logger
from pipecat.frames.frames import Frame, MetricsFrame
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class LatencyLogger(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for m in frame.data:
                if isinstance(m, TTFBMetricsData):
                    logger.debug(f"[LATENCY] {m.processor} TTFB: {m.value * 1000:.0f}ms")
        await self.push_frame(frame, direction)
