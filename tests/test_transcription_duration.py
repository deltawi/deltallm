import struct

import pytest

from src.audio.transcription_duration import (
    MIN_AUDIO_BITS_PER_SECOND,
    MIN_BILLABLE_SECONDS,
    transcription_seconds_ceiling,
)


def wav(
    seconds,
    *,
    rate=16_000,
    channels=1,
    bits=16,
    tag=1,
    byte_rate=None,
    data_size=None,
    extra_chunks=b"",
    trailing=b"",
    fmt_extension=b"",
):
    block_align = channels * bits // 8
    audio = b"\0" * int(seconds * rate * block_align)
    fmt = (
        struct.pack(
            "<HHIIHH",
            tag,
            channels,
            rate,
            byte_rate if byte_rate is not None else rate * block_align,
            block_align,
            bits,
        )
        + fmt_extension
    )
    chunks = (
        b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + extra_chunks
        + b"data"
        + struct.pack("<I", len(audio) if data_size is None else data_size)
        + audio
        + trailing
    )
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def size_ceiling(content):
    return -(-len(content) * 8 // MIN_AUDIO_BITS_PER_SECOND)


def test_pcm_wav_uses_the_exact_header_duration():
    assert transcription_seconds_ceiling(wav(30)) == 30
    assert transcription_seconds_ceiling(wav(30.2)) == 31


def test_multichannel_wav_covers_every_channel():
    assert transcription_seconds_ceiling(wav(30, channels=2)) == 60


def test_fixed_rate_variants_are_exact():
    assert transcription_seconds_ceiling(wav(20, bits=32, tag=3)) == 20
    assert transcription_seconds_ceiling(wav(20, bits=8, tag=7, rate=8_000)) == 20
    extensible = b"\x16\x00\x10\x00\x00\x00\x00\x00" + b"\x01\x00" + b"\0" * 14
    assert transcription_seconds_ceiling(wav(20, tag=0xFFFE, fmt_extension=extensible)) == 20


def test_chunks_before_and_after_audio_keep_the_ceiling_safe():
    listed = b"LIST" + struct.pack("<I", 5) + b"INFO\0" + b"\0"
    assert transcription_seconds_ceiling(wav(30, extra_chunks=listed)) == 30
    # Trailing metadata is counted as audio: never less than the real length.
    assert transcription_seconds_ceiling(wav(30, trailing=b"\0" * 64_000)) == 32


@pytest.mark.parametrize("placeholder", [0, 0xFFFFFFFF])
def test_streaming_wav_placeholder_sizes_count_the_bytes_present(placeholder):
    assert transcription_seconds_ceiling(wav(30, data_size=placeholder)) == 30


def test_short_audio_reserves_the_provider_minimum():
    assert transcription_seconds_ceiling(wav(1)) == MIN_BILLABLE_SECONDS
    assert transcription_seconds_ceiling(b"") == MIN_BILLABLE_SECONDS


@pytest.mark.parametrize(
    "content",
    [
        b"ID3" + b"\0" * 400_000,
        wav(5, tag=0x55),
        wav(5, byte_rate=1_000_000),
        wav(5, rate=2_000_000, bits=8),
        wav(5)[:30],
        wav(5).replace(b"data", b"dat2"),
    ],
    ids=["mp3", "compressed-wav", "bad-byte-rate", "bad-rate", "truncated", "no-data"],
)
def test_other_files_use_the_size_at_the_minimum_bitrate(content):
    assert transcription_seconds_ceiling(content) == max(
        size_ceiling(content), MIN_BILLABLE_SECONDS
    )


def test_a_second_format_chunk_is_not_trusted():
    fmt = wav(5)[12:36]
    content = wav(5, extra_chunks=fmt)
    assert transcription_seconds_ceiling(content) == size_ceiling(content)
