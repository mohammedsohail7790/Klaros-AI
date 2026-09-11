"""app/services/audio_codec.py — real G.711 μ-law <-> PCM16 conversion.
Verified via structural/round-trip properties (monotonicity, near-zero
silence, bounded round-trip error) rather than exact byte-for-byte
comparison against a specific provider's real encoder output, which this
sandbox has no reference sample to check against — see the module's own
docstring for why."""

import struct

from app.services.audio_codec import (
    mulaw_byte_to_pcm16,
    mulaw_to_pcm16_bytes,
    pcm16_bytes_to_mulaw,
    pcm16_to_mulaw_byte,
    rms_energy,
)


def test_silence_byte_decodes_near_zero() -> None:
    # 0xFF is the "positive zero" silence byte in G.711 μ-law.
    assert abs(mulaw_byte_to_pcm16(0xFF)) < 40


def test_decode_is_monotonic_across_the_positive_range() -> None:
    # Bytes 0x80..0xFF are the (encoded, sign-positive) half; 0xFF is the
    # silence byte (near zero) so magnitude descends across this range —
    # confirmed by test_silence_byte_decodes_near_zero. The codec is only
    # meaningful if that descent is monotonic, not just "trending".
    values = [mulaw_byte_to_pcm16(b) for b in range(0x80, 0x100)]
    assert values == sorted(values, reverse=True)


def test_encode_decode_round_trip_stays_close_for_a_range_of_samples() -> None:
    for original in [0, 100, -100, 1000, -1000, 8000, -8000, 20000, -20000]:
        encoded = pcm16_to_mulaw_byte(original)
        decoded = mulaw_byte_to_pcm16(encoded)
        # Mu-law is lossy (8-bit encoding of a 16-bit range) — bound the
        # relative error rather than expecting exact recovery.
        allowed_error = max(200, abs(original) * 0.15)
        assert abs(decoded - original) <= allowed_error, (original, decoded)


def test_byte_string_round_trip_preserves_length_relationship() -> None:
    pcm = struct.pack("<10h", *range(0, 1000, 100))
    mulaw = pcm16_bytes_to_mulaw(pcm)
    assert len(mulaw) == 10
    back = mulaw_to_pcm16_bytes(mulaw)
    assert len(back) == 20


def test_odd_length_pcm_buffer_never_crashes() -> None:
    pcm = b"\x01\x02\x03"  # 1.5 samples — malformed/truncated input
    result = pcm16_bytes_to_mulaw(pcm)
    assert len(result) == 1  # trailing incomplete byte dropped, not crashed


def test_empty_buffers_never_crash() -> None:
    assert mulaw_to_pcm16_bytes(b"") == b""
    assert pcm16_bytes_to_mulaw(b"") == b""
    assert rms_energy(b"") == 0.0


def test_rms_energy_is_zero_for_silence_and_positive_for_signal() -> None:
    silence = struct.pack("<100h", *([0] * 100))
    assert rms_energy(silence) == 0.0

    loud = struct.pack("<100h", *([10000] * 50 + [-10000] * 50))
    assert rms_energy(loud) > 5000


def test_rms_energy_increases_with_amplitude() -> None:
    quiet = struct.pack("<10h", *([500] * 10))
    loud = struct.pack("<10h", *([15000] * 10))
    assert rms_energy(loud) > rms_energy(quiet)
