"""Upper bound on the billable seconds of an uploaded transcription file.

Accounting v2 reserves money before the provider call, so a model priced per
second needs a duration ceiling. The file itself is the only bound the gateway
has: an uncompressed WAV header gives the exact length, and any other file
cannot hold more audio than its size allows at a very low bitrate.
"""

from __future__ import annotations

import math
import struct

# Lowest bitrate at which a common speech codec still produces audio: MP3 and
# AAC stop near 8 kbit/s, Opus at 6 kbit/s and AMR-NB at 4.75 kbit/s. A file of
# N bytes therefore holds at most N * 8 / 4,000 seconds of audio.
MIN_AUDIO_BITS_PER_SECOND = 4_000

# Some providers bill a minimum per request (Groq bills at least 10 seconds).
MIN_BILLABLE_SECONDS = 10

_MAX_SECONDS = 2**31 - 1
_MAX_CHUNKS = 64
_MAX_SAMPLE_RATE = 768_000
_MAX_CHANNELS = 64
# PCM, IEEE float, A-law and mu-law store a fixed number of bytes per sample.
_FIXED_RATE_FORMATS = {0x0001, 0x0003, 0x0006, 0x0007}
_EXTENSIBLE_FORMAT = 0xFFFE


def transcription_seconds_ceiling(content: bytes) -> int:
    """Return whole seconds that cover any provider's billed duration for this file.

    For a multichannel WAV the ceiling covers every channel, because some
    providers bill each channel separately.
    """
    seconds = _wav_channel_seconds(content)
    if seconds is None:
        seconds = len(content) * 8 / MIN_AUDIO_BITS_PER_SECOND
    return min(max(math.ceil(seconds), MIN_BILLABLE_SECONDS), _MAX_SECONDS)


def _wav_channel_seconds(content: bytes) -> float | None:
    """Exact duration times channel count of a fixed-rate WAV, or None."""
    if len(content) < 12 or content[:4] not in (b"RIFF", b"RF64") or content[8:12] != b"WAVE":
        return None
    offset = 12
    stream_bytes_per_second: int | None = None
    channels = 0
    for _ in range(_MAX_CHUNKS):
        if offset + 8 > len(content):
            return None
        chunk_id = content[offset : offset + 4]
        (size,) = struct.unpack_from("<I", content, offset + 4)
        body = offset + 8
        if chunk_id == b"fmt ":
            if stream_bytes_per_second is not None:
                return None
            parsed = _fixed_rate_format(content[body : body + size])
            if parsed is None:
                return None
            stream_bytes_per_second, channels = parsed
        elif chunk_id == b"data":
            if stream_bytes_per_second is None:
                return None
            # Streaming and RF64 files carry a placeholder size, and a provider
            # decodes to the end of the file, so count every byte after the
            # header. Trailing metadata only makes the ceiling a little larger.
            audio_bytes = len(content) - body
            return audio_bytes * channels / stream_bytes_per_second
        offset = body + size + (size & 1)
    return None


def _fixed_rate_format(fmt: bytes) -> tuple[int, int] | None:
    if len(fmt) < 16:
        return None
    tag, channels, sample_rate, byte_rate, block_align, _bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == _EXTENSIBLE_FORMAT:
        if len(fmt) < 26:
            return None
        (tag,) = struct.unpack_from("<H", fmt, 24)
    if (
        tag not in _FIXED_RATE_FORMATS
        or not 0 < channels <= _MAX_CHANNELS
        or not 0 < sample_rate <= _MAX_SAMPLE_RATE
        or block_align <= 0
        or block_align % channels
        or byte_rate != sample_rate * block_align
    ):
        return None
    return byte_rate, channels
