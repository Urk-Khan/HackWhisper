"""Gemma on the Gemini API, tuned for live phone calls.

Two problems with running Gemma 4 through pipecat's stock GoogleLLMService:

1. Latency. Gemma 4 thinks before it answers and pipecat applies no thinking
   default to non-Gemini-3 models, so a one-sentence reply can take 15-20 s.
   Thinking is set to "minimal", which brings replies to about 1-2 s.
2. Reliability. Even with minimal thinking, roughly 1 call in 5 returns HTTP 500.
   Pipecat only retries when the first chunk stalls, not on server errors, so a
   500 would leave the caller in silence. This class retries a failed request
   before any text has been emitted.

Both are tied to the pinned pipecat-ai==1.8.1 internals (_stream_content,
_iter_stream), so re-check this file when upgrading pipecat.
"""

import asyncio
from collections.abc import AsyncIterator

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.google.llm import GoogleLLMService

MAX_ATTEMPTS = 3
RETRY_DELAY_SECS = 0.3
RETRYABLE_CODES = {500, 502, 503, 504}


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return True
    return getattr(error, "code", None) in RETRYABLE_CODES


class GemmaLLMService(GoogleLLMService):
    """GoogleLLMService with minimal thinking and retries before first output."""

    @staticmethod
    def minimal_thinking() -> "GoogleLLMService.ThinkingConfig":
        return GoogleLLMService.ThinkingConfig(thinking_level="minimal")

    async def _stream_response(self, context: LLMContext) -> AsyncIterator:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            stream = self._iter_stream(
                await self._stream_content(context),
                first_chunk_timeout=self._stream_idle_timeout_secs,
            )
            try:
                first_chunk = await stream.__anext__()
            except StopAsyncIteration:
                await stream.aclose()
                return
            except Exception as error:
                await stream.aclose()
                if attempt == MAX_ATTEMPTS or not _is_retryable(error):
                    raise
                logger.warning(
                    f"{self}: Gemma request failed ({error}), retry {attempt}/{MAX_ATTEMPTS - 1}"
                )
                await asyncio.sleep(RETRY_DELAY_SECS)
                continue

            # Once a chunk is out, its text is already downstream, so no more retries.
            yield first_chunk
            async for chunk in stream:
                yield chunk
            return
