"""Temporary debug processor — logs incoming audio frame stats.

Insert between transport.input() and stt in the pipeline to see what the
transport is actually delivering. Remove once the issue is diagnosed.
"""

import struct
import time

from loguru import logger
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class AudioDebugProcessor(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._frame_count = 0
        self._last_log = 0.0
        self._total_bytes = 0
        self._silent_frames = 0
        self._window_max_peak = 0.0
        self._window_sum_rms = 0.0
        self._window_frames = 0
        self._window_speech_frames = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            self._frame_count += 1
            audio = frame.audio
            self._total_bytes += len(audio)

            # Compute RMS volume of the PCM s16le audio
            num_samples = len(audio) // 2
            if num_samples > 0:
                samples = struct.unpack(f"<{num_samples}h", audio[: num_samples * 2])
                rms = (sum(s * s for s in samples) / num_samples) ** 0.5
                peak = max(abs(s) for s in samples)
                rms_norm = rms / 32768.0
                peak_norm = peak / 32768.0
            else:
                rms_norm = 0.0
                peak_norm = 0.0

            if rms_norm < 0.001:
                self._silent_frames += 1

            self._window_frames += 1
            self._window_sum_rms += rms_norm
            if peak_norm > self._window_max_peak:
                self._window_max_peak = peak_norm
            if rms_norm >= 0.015:
                self._window_speech_frames += 1

            now = time.monotonic()
            # Log every 2 seconds
            if now - self._last_log >= 2.0:
                avg_rms = (
                    self._window_sum_rms / self._window_frames if self._window_frames > 0 else 0.0
                )
                max_peak = self._window_max_peak
                has_speech = self._window_speech_frames > 2 or max_peak >= 0.03
                status = "SPEECH DETECTED" if has_speech else "SILENCE/LINE NOISE"
                logger.info(
                    f"[AUDIO_DEBUG] frames={self._frame_count}, "
                    f"window_peak={max_peak:.4f}, avg_rms={avg_rms:.4f}, "
                    f"speech_frames={self._window_speech_frames}/{self._window_frames} "
                    f"-> [{status}]"
                )
                self._last_log = now
                self._window_max_peak = 0.0
                self._window_sum_rms = 0.0
                self._window_frames = 0
                self._window_speech_frames = 0

        await self.push_frame(frame, direction)
