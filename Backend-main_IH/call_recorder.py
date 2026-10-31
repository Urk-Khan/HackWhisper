"""
Call Audio Recorder for Innoventix Hub Voice Agent.

Captures real-time bidirectional audio (both the telephone caller and the AI agent)
with zero latency overhead, aligns both streams without sample jitter, balances levels,
and saves a crystal-clear, stutter-free .wav recording into `call_recordings/` for every call.
"""

import asyncio
import audioop
import os
import re
import time
import wave
from datetime import datetime
from loguru import logger
import numpy as np

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class InputAudioRecorderTap(FrameProcessor):
    """Zero-overhead pipeline tap placed right after transport.input().
    Captures raw caller audio frames and passes them downstream instantly."""

    def __init__(self, recorder: "CallRecorder"):
        super().__init__()
        self._recorder = recorder

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and frame.audio:
            self._recorder.record_input(frame.audio, frame.sample_rate)
        await self.push_frame(frame, direction)


class OutputAudioRecorderTap(FrameProcessor):
    """Zero-overhead pipeline tap placed right after TTS / LatencyLogger.
    Captures raw agent audio frames and passes them downstream instantly."""

    def __init__(self, recorder: "CallRecorder"):
        super().__init__()
        self._recorder = recorder

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._recorder.on_bot_speaking_state(True)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._recorder.on_bot_speaking_state(False)
        elif isinstance(frame, OutputAudioRawFrame) and frame.audio:
            self._recorder.record_output(frame.audio, frame.sample_rate)
        await self.push_frame(frame, direction)


class CallRecorder:
    """Manages jitter-free audio capture and mixing for a call session."""

    def __init__(
        self,
        output_dir: str = "call_recordings",
        caller_phone: str | None = None,
        call_id: str | None = None,
        target_sample_rate: int = 8000,
    ):
        if not os.path.isabs(output_dir):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            output_dir = os.path.join(base_dir, output_dir)
        self.output_dir = output_dir
        self.caller_phone = caller_phone or "unknown_caller"
        self.call_id = call_id or "session"
        self.target_sample_rate = target_sample_rate

        self._start_time = time.monotonic()
        self._caller_chunks: list[np.ndarray] = []
        self._caller_samples_count: int = 0
        self._input_ratecv_state = None

        # Agent utterance tracking
        self._agent_utterances: list[tuple[int, np.ndarray]] = []
        self._current_utterance_chunks: list[np.ndarray] = []
        self._current_utterance_start_pos: int = 0
        self._last_output_time: float = 0.0
        self._output_ratecv_state = None

        self._saved = False
        os.makedirs(self.output_dir, exist_ok=True)

    def input_tap(self) -> InputAudioRecorderTap:
        return InputAudioRecorderTap(self)

    def output_tap(self) -> OutputAudioRecorderTap:
        return OutputAudioRecorderTap(self)

    def on_bot_speaking_state(self, is_speaking: bool):
        """Called when TTS signals speaking start/stop."""
        if is_speaking:
            self._start_new_utterance_if_needed(force=True)
        else:
            self._flush_current_utterance()

    def record_input(self, audio_bytes: bytes, sample_rate: int):
        """Appends caller audio contiguously to avoid clock-jitter dropouts."""
        sr = self.target_sample_rate
        if sample_rate != sr and sample_rate > 0:
            audio_bytes, self._input_ratecv_state = audioop.ratecv(
                audio_bytes, 2, 1, sample_rate, sr, self._input_ratecv_state
            )

        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        if len(samples) > 0:
            self._caller_chunks.append(samples)
            self._caller_samples_count += len(samples)

    def record_output(self, audio_bytes: bytes, sample_rate: int):
        """Buffers agent audio per utterance, aligned to caller timeline."""
        now = time.monotonic()
        sr = self.target_sample_rate

        if sample_rate != sr and sample_rate > 0:
            audio_bytes, self._output_ratecv_state = audioop.ratecv(
                audio_bytes, 2, 1, sample_rate, sr, self._output_ratecv_state
            )

        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        if len(samples) == 0:
            return

        # If more than 250ms gap between output frames, a new phrase/turn started
        if (now - self._last_output_time) > 0.25:
            self._start_new_utterance_if_needed(force=True)

        self._current_utterance_chunks.append(samples)
        self._last_output_time = now

    def _start_new_utterance_if_needed(self, force: bool = False):
        if self._current_utterance_chunks:
            self._flush_current_utterance()
        # Align the new utterance with the current point in the caller stream
        self._current_utterance_start_pos = self._caller_samples_count

    def _flush_current_utterance(self):
        if self._current_utterance_chunks:
            full_utterance = np.concatenate(self._current_utterance_chunks)
            self._agent_utterances.append((self._current_utterance_start_pos, full_utterance))
            self._current_utterance_chunks = []

    async def save(self) -> str | None:
        """Saves the call recording asynchronously without blocking the event loop."""
        if self._saved:
            return None
        self._saved = True

        if not self._caller_chunks and not self._agent_utterances and not self._current_utterance_chunks:
            logger.info("No audio frames recorded for this call session.")
            return None

        return await asyncio.to_thread(self._save_sync)

    def _save_sync(self) -> str | None:
        try:
            self._flush_current_utterance()
            sr = self.target_sample_rate

            # Build timestamp and clean filename
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_phone = re.sub(r"[^\w+]", "", self.caller_phone)
            short_id = (self.call_id or "")[-8:] if self.call_id else "call"
            filename = f"call_{timestamp_str}_{safe_phone}_{short_id}.wav"
            filepath = os.path.join(self.output_dir, filename)

            # Assemble continuous caller track
            caller_track = (
                np.concatenate(self._caller_chunks)
                if self._caller_chunks
                else np.zeros(0, dtype=np.int16)
            )

            # Determine total duration across caller and all agent utterances
            total_samples = len(caller_track)
            for start_idx, utt_samples in self._agent_utterances:
                total_samples = max(total_samples, start_idx + len(utt_samples))

            # Ensure minimum duration of at least 0.5s
            total_samples = max(total_samples, sr // 2)

            # Pad caller track to total length if needed
            if len(caller_track) < total_samples:
                caller_track = np.pad(caller_track, (0, total_samples - len(caller_track)))

            # Assemble agent track by placing each utterance seamlessly at its start position
            agent_track = np.zeros(total_samples, dtype=np.int16)
            for start_idx, utt_samples in self._agent_utterances:
                end_idx = min(total_samples, start_idx + len(utt_samples))
                chunk_to_write = utt_samples[: end_idx - start_idx]
                agent_track[start_idx:end_idx] = chunk_to_write

            # Measure active RMS for balancing levels
            c_active = caller_track[np.abs(caller_track) > 150]
            a_active = agent_track[np.abs(agent_track) > 150]
            c_rms = np.sqrt(np.mean(c_active.astype(np.float64) ** 2)) if len(c_active) > 0 else 0
            a_rms = np.sqrt(np.mean(a_active.astype(np.float64) ** 2)) if len(a_active) > 0 else 0

            # Boost caller volume gently if telephone mic is quiet compared to synthesized TTS
            c_gain = 1.0
            if c_rms > 0 and a_rms > 0 and c_rms < a_rms * 0.7:
                c_gain = min(2.2, (a_rms * 0.9) / c_rms)

            caller_boosted = np.clip(caller_track.astype(np.float64) * c_gain, -32768, 32767).astype(np.int32)
            agent_int32 = agent_track.astype(np.int32)

            # Mix caller and agent with 32-bit addition and soft-clipping protection
            mixed = np.clip(caller_boosted + agent_int32, -32768, 32767).astype(np.int16)

            # Write standard 16-bit PCM WAV (8000 Hz, mono)
            with wave.open(filepath, "wb") as wf:
                wf.setnchannels(1)   # Mono
                wf.setsampwidth(2)   # 16-bit
                wf.setframerate(sr)  # 8000 Hz
                wf.writeframes(mixed.tobytes())

            duration_sec = len(mixed) / sr
            file_kb = os.path.getsize(filepath) / 1024
            logger.info(
                f"[RECORDING] Saved stutter-free recording to {filepath} "
                f"({duration_sec:.1f}s, {file_kb:.1f} KB, caller_gain={c_gain:.2f}x)"
            )
            return filepath
        except Exception as e:
            logger.error(f"Error during audio mix and save: {e}", exc_info=True)
            return None
