"""
Sustained-note ("stretched note") audio rendering for Note Block Studio.

Note Block Studio stores how long each note is held in an extra chunk at the
end of the .nbs file (see scripts/note_durations in the GameMaker project):

    after the custom instruments section:
        4 bytes   magic  b"DURS"
        u8        version (1)
        s32       count
        count x { s32 tick, u16 layer, s32 duration_in_ticks }   (little-endian)

Every other NBS reader stops after the custom instruments, so the chunk is
invisible to them.

This module renders a song with nbswave, but stretches every note that has a
duration: the attack of the sound is played normally, then a small part of the
sound is looped seamlessly (pitch-period aligned, cross-faded and level
compensated) until the note is released, and finally the sound's natural tail
plays out with a short fade.

Notes are only ever *extended*: if a sound is naturally longer than the note's
duration it plays exactly like it does in vanilla Note Block Studio.

Written for Python 3.8 + nbswave 0.4 (the versions bundled with NBS).
"""

import math
import struct
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import pydub
import pynbs

from nbswave import audio, nbs
from nbswave.main import MissingInstrumentException, SongRenderer

__all__ = [
    "read_durations",
    "parse_duration_chunk",
    "sustain_samples",
    "sustain_segment",
    "SustainSongRenderer",
    "render_audio",
]

DURATION_MAGIC = b"DURS"

# Tunables (all in milliseconds unless noted)
DEFAULT_RELEASE_MS = 180.0  # Fade applied to the natural tail after a held note is released
ATTACK_SKIP_MS = 25.0  # How far past the loudest point the loop may start
MIN_LOOP_MS = 30.0  # Shortest loop we aim for on pitched sounds
MAX_LOOP_MS = 90.0  # Longest loop we aim for on pitched sounds
NOISE_SOURCE_MS = 150.0  # Longest stretch of an unpitched sound used as grain material
NOISE_GRAIN_MS = 40.0  # Grain length for unpitched sounds
MIN_PERIODICITY = 0.6  # Normalized autocorrelation needed to treat a sound as pitched


# --------------------------------------------------------------------------
# Reading durations from .nbs files
# --------------------------------------------------------------------------


def parse_duration_chunk(data: bytes) -> Dict[Tuple[int, int], int]:
    """Parse the bytes that follow the custom instruments section.

    Returns {(tick, layer): duration_in_ticks}. Unknown or missing data yields {}.
    """
    durations = {}  # type: Dict[Tuple[int, int], int]
    if len(data) < 9 or data[:4] != DURATION_MAGIC:
        return durations
    version = data[4]
    if version < 1:
        return durations
    (count,) = struct.unpack_from("<i", data, 5)
    offset = 9
    entry = struct.Struct("<iHi")
    for _ in range(max(0, count)):
        if offset + entry.size > len(data):
            break
        tick, layer, duration = entry.unpack_from(data, offset)
        offset += entry.size
        if duration > 0 and tick >= 0:
            durations[(tick, layer)] = duration
    return durations


def read_durations(song_path) -> Dict[Tuple[int, int], int]:
    """Read the note durations stored in an .nbs file ({} if it has none)."""
    with open(song_path, "rb") as f:
        pynbs.Parser(f).read_file()  # Skips past everything vanilla NBS knows about
        rest = f.read()
    return parse_duration_chunk(rest)


# --------------------------------------------------------------------------
# DSP: making a sound last longer
# --------------------------------------------------------------------------


def _envelope(mono: np.ndarray, frame_rate: int) -> np.ndarray:
    """RMS envelope with a ~10 ms window."""
    win = max(1, int(frame_rate * 0.01))
    kernel = np.ones(win) / win
    return np.sqrt(np.convolve(mono * mono, kernel, mode="same"))


def _find_period(mono: np.ndarray, frame_rate: int) -> Tuple[Optional[float], float]:
    """Estimate the fundamental period (in samples) with a normalized autocorrelation.

    Returns (period or None, periodicity 0..1).
    """
    n = len(mono)
    if n < 64:
        return None, 0.0
    x = mono - mono.mean()
    energy = float(np.dot(x, x))
    if energy <= 1e-9:
        return None, 0.0
    size = 1 << int(math.ceil(math.log2(2 * n)))
    spectrum = np.fft.rfft(x, size)
    ac = np.fft.irfft(spectrum * np.conj(spectrum), size)[:n]
    # Normalize by the overlap so long lags aren't penalised
    ac = ac / (energy * (n - np.arange(n)) / n)

    min_lag = max(3, int(frame_rate / 8000.0))  # up to ~8 kHz (very high notes)
    max_lag = min(n // 2, int(frame_rate / 30.0))  # down to ~30 Hz
    if max_lag <= min_lag + 2:
        return None, 0.0
    window = ac[min_lag:max_lag]

    # Pick the first strong peak (avoids octave errors from picking a multiple)
    best = int(np.argmax(window))
    threshold = 0.9 * window[best]
    lag = best
    for i in range(1, len(window) - 1):
        if window[i] >= threshold and window[i] >= window[i - 1] and window[i] >= window[i + 1]:
            lag = i
            break
    lag += min_lag
    strength = float(ac[lag])

    # Parabolic interpolation for a sub-sample period
    if 1 <= lag < n - 1:
        y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = y0 - 2 * y1 + y2
        shift = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
        period = lag + float(np.clip(shift, -0.5, 0.5))
    else:
        period = float(lag)
    return period, max(0.0, min(1.0, strength))


def _choose_loop_length(period: float, frame_rate: int) -> int:
    """Pick a whole number of periods (30-90 ms) whose length is closest to an integer sample count."""
    lo = max(1, int(math.ceil(MIN_LOOP_MS / 1000.0 * frame_rate / period)))
    hi = max(lo, int(MAX_LOOP_MS / 1000.0 * frame_rate / period))
    best_k, best_err = lo, 1e9
    for k in range(lo, hi + 1):
        err = abs(k * period - round(k * period))
        if err < best_err - 1e-6:
            best_k, best_err = k, err
    return int(round(best_k * period))


def _release_tail(tail: np.ndarray, frame_rate: int, release_ms: float) -> np.ndarray:
    """The natural tail of the sound, shortened to `release_ms` with a smooth fade."""
    rel = int(max(0.0, release_ms) / 1000.0 * frame_rate)
    tail = tail.copy()
    if rel <= 0:
        return tail[:0]
    if tail.shape[0] > rel:
        tail = tail[:rel]
    if tail.shape[0] > 0:
        fade = np.cos(np.linspace(0.0, math.pi / 2, tail.shape[0])) ** 2
        tail = tail * fade[:, None]
    return tail


def _sustain_noise(
    x: np.ndarray,
    mono: np.ndarray,
    frame_rate: int,
    start: int,
    target: int,
    release_ms: float,
) -> np.ndarray:
    """Sustain an unpitched sound by overlap-adding random grains of it at a constant level."""
    n, channels = x.shape
    src_len = min(n - start, int(NOISE_SOURCE_MS / 1000.0 * frame_rate))
    grain = min(int(NOISE_GRAIN_MS / 1000.0 * frame_rate), src_len)
    grain -= grain % 2
    if grain < 32:
        return x
    hop = grain // 2
    release_point = start + src_len

    # Flatten the source so every grain has the same loudness, then match the level at the release point
    src = x[start:release_point]
    src_env = _envelope(mono[start:release_point], frame_rate)
    src = src / np.maximum(src_env, src_env.max() * 0.02 + 1e-9)[:, None]
    edge = max(16, int(0.01 * frame_rate))
    level = float(np.sqrt(np.mean(mono[release_point - edge: release_point] ** 2)))

    # Overlap-add with a sine window (constant power at 50% overlap for uncorrelated grains)
    xfade = min(hop, int(0.015 * frame_rate))
    n_held = max(0, target - release_point) + 2 * xfade
    window = np.sin(np.pi * (np.arange(grain) + 0.5) / grain)[:, None]
    rng = np.random.default_rng(n_held + 7 * grain)
    out = np.zeros((n_held + 2 * grain, channels))
    max_offset = src.shape[0] - grain
    pos = 0
    while pos < out.shape[0] - grain:
        offset = int(rng.integers(0, max_offset + 1)) if max_offset > 0 else 0
        out[pos: pos + grain] += src[offset: offset + grain] * window
        pos += hop
    held = out[hop: hop + n_held] * level  # Skip the first half-grain, where only one grain sounds

    # Equal-power cross-fades: natural start -> grains -> natural tail
    up = np.sin(np.linspace(0.0, math.pi / 2, xfade))[:, None]
    down = np.cos(np.linspace(0.0, math.pi / 2, xfade))[:, None]
    head = x[:release_point].copy()
    held[:xfade] = head[-xfade:] * down + held[:xfade] * up
    head = head[:-xfade]
    tail = _release_tail(x[release_point:], frame_rate, release_ms)
    m = min(xfade, tail.shape[0])
    if m > 0:
        held[-m:] = held[-m:] * down[-m:] + tail[:m] * up[:m]
        tail = tail[m:]
    else:
        held[-xfade:] *= down
    return np.concatenate([head, held, tail], axis=0)


def sustain_samples(
    x: np.ndarray,
    frame_rate: int,
    hold_ms: float,
    release_ms: float = DEFAULT_RELEASE_MS,
) -> np.ndarray:
    """Extend a sound so it is held for `hold_ms` milliseconds.

    `x` is a float array of shape (frames, channels). Returns a new float array.
    If the sound is already at least `hold_ms` long, it is returned unchanged.
    """
    n = x.shape[0]
    target = int(round(hold_ms * frame_rate / 1000.0))
    if target <= n or n < 32:
        return x

    mono = x.mean(axis=1)
    env = _envelope(mono, frame_rate)
    peak_level = float(env.max())
    if peak_level <= 1e-9:
        return x  # Silent sound: nothing to sustain

    peak = int(np.argmax(env))
    # Loop start: just after the attack, but never so late that the sound has died out.
    # Short (e.g. high-pitched) sounds get a proportionally shorter skip.
    audible = np.nonzero(env > peak_level * 0.05)[0]
    last_audible = int(audible[-1]) if len(audible) else n - 1
    skip = min(int(ATTACK_SKIP_MS / 1000.0 * frame_rate), max(0, (last_audible - peak) // 6))
    loop_start = min(peak + skip, max(peak, last_audible // 2))

    available = n - loop_start
    if available < int(0.01 * frame_rate):
        loop_start = max(0, n - int(0.04 * frame_rate))
        available = n - loop_start

    # Pitched or noisy?
    analysis = mono[loop_start: loop_start + min(available, int(0.1 * frame_rate))]
    period, periodicity = _find_period(analysis, frame_rate)

    if period is None or periodicity < MIN_PERIODICITY:
        # Unpitched sound (noise, drums...): a short loop would buzz, so use grains instead
        return _sustain_noise(x, mono, frame_rate, loop_start, target, release_ms)

    loop_len = _choose_loop_length(period, frame_rate)
    xfade = max(8, min(loop_len // 3, int(0.006 * frame_rate)))

    # Make sure the loop (plus the cross-fade tail) fits inside the sound
    if loop_start + loop_len + xfade > n:
        loop_len = max(16, n - loop_start - xfade)
        if loop_len > period:
            loop_len = int(round(int(loop_len / period) * period)) or loop_len
        xfade = max(4, min(xfade, loop_len // 3))
    if loop_start + loop_len + xfade > n or loop_len < 16:
        # Too little material to loop: hold the last part by stretching what we have
        return x

    loop_end = loop_start + loop_len

    # Level compensation: a decaying sound would "pump" when looped, so ramp the
    # loop so its start matches the level at its end.
    edge = max(4, loop_len // 5)
    rms_start = float(np.sqrt(np.mean(mono[loop_start: loop_start + edge] ** 2)) + 1e-12)
    rms_end = float(np.sqrt(np.mean(mono[loop_end - edge: loop_end] ** 2)) + 1e-12)
    ratio = float(np.clip(rms_end / rms_start, 0.25, 4.0))
    t = np.linspace(0.0, 1.0, loop_len)
    gain = np.power(ratio, 1.0 - t)[:, None]  # ratio at the start of the loop -> 1 at the end

    body = x[loop_start:loop_end] * gain
    # Seamless wrap: the start of the loop fades in from what *follows* the loop end
    ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, xfade)))[:, None]
    body[:xfade] = x[loop_end: loop_end + xfade] * (1.0 - ramp) + body[:xfade] * ramp

    # Assemble: natural start, as many loops as needed, then the natural tail with a release fade
    head = x[:loop_end]
    remaining = max(0, target - head.shape[0])
    repeats = int(math.ceil(remaining / float(loop_len)))
    held = np.tile(body, (repeats, 1)) if repeats > 0 else np.zeros((0, x.shape[1]))

    tail = _release_tail(x[loop_end:], frame_rate, release_ms)

    return np.concatenate([head, held, tail], axis=0)


def sustain_segment(
    sound: pydub.AudioSegment, hold_ms: float, release_ms: float = DEFAULT_RELEASE_MS
) -> pydub.AudioSegment:
    """pydub wrapper around sustain_samples (16-bit sounds)."""
    if hold_ms <= len(sound):
        return sound
    channels = sound.channels
    samples = np.array(sound.get_array_of_samples(), dtype=np.float64)
    samples = samples.reshape(-1, channels)
    out = sustain_samples(samples, sound.frame_rate, hold_ms, release_ms)
    if out is samples:
        return sound
    limit = float(2 ** (8 * sound.sample_width - 1) - 1)
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[sound.sample_width]
    data = np.clip(np.rint(out), -limit - 1, limit).astype(dtype).tobytes()
    return sound._spawn(data)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


class SustainSongRenderer(SongRenderer):
    """nbswave's SongRenderer, with support for held notes."""

    def __init__(
        self,
        song,
        durations: Dict[Tuple[int, int], int],
        default_sound_path=None,
        release_ms: float = DEFAULT_RELEASE_MS,
    ):
        if default_sound_path is None:
            super().__init__(song)
        else:
            super().__init__(song, default_sound_path)
        self._durations = durations
        self._release_ms = release_ms

    def _hold_ms(self, tick: int, duration: int, tempo_segments: Sequence[float]) -> float:
        """How long (ms) a note starting at `tick` is held, honouring tempo changes."""
        last = len(tempo_segments) - 1
        start = tempo_segments[min(tick, last)]
        end_tick = tick + duration
        if end_tick <= last:
            return tempo_segments[end_tick] - start
        # Past the end of the song: continue at the last tempo
        if last >= 1:
            tick_ms = tempo_segments[last] - tempo_segments[last - 1]
        else:
            tick_ms = 1000.0 / self._song.header.tempo
        return (tempo_segments[last] - start) + (end_tick - last) * tick_ms

    def _mix(
        self,
        notes,
        ignore_missing_instruments: bool = False,
        sample_rate: int = 44100,
        channels: int = 2,
        bit_depth: int = 16,
    ) -> audio.Track:
        # Same as nbswave 0.4.0's SongRenderer._mix, plus the sustain step.
        tempo_segments = self._song.tempo_segments
        track_length = self.get_length(self._song.weighted_notes(), tempo_segments)

        mixer = audio.Mixer(
            sample_width=bit_depth // 8,
            frame_rate=sample_rate,
            channels=channels,
            length=track_length,
        )

        sorted_notes = nbs.sorted_notes(notes)

        last_ins = None
        last_key = None
        last_vol = None
        last_pan = None
        sound = None
        sustained_cache = {}

        for note in sorted_notes:
            ins = note.instrument
            key = note.key
            vol = note.velocity
            pan = note.panning

            if ins != last_ins:
                last_key = None
                last_vol = None
                last_pan = None

                try:
                    sound1 = self._instruments[note.instrument]
                except KeyError:  # Sound file missing
                    if not ignore_missing_instruments:
                        custom_ins_id = ins - self._song.header.default_instruments
                        instrument_data = self._song.instruments[custom_ins_id]
                        raise MissingInstrumentException(
                            "The sound file for instrument {} was not found: {}".format(
                                instrument_data.name, instrument_data.file
                            )
                        )
                    else:
                        continue

                if sound1 is None:  # Sound file not assigned
                    continue

                sound1 = audio.sync(sound1)

            if key != last_key:
                last_vol = None
                last_pan = None
                pitch = audio.key_to_pitch(key)
                sound2 = audio.change_speed(sound1, pitch)

            if vol != last_vol:
                last_pan = None
                gain = audio.vol_to_gain(vol)
                sound3 = sound2.apply_gain(gain)

            if pan != last_pan:
                sound4 = sound3.pan(pan)
                sound = sound4
                sustained_cache = {}

            last_ins = ins
            last_key = key
            last_vol = vol
            last_pan = pan

            pos = tempo_segments[note.tick]

            to_play = sound
            duration = self._durations.get((note.tick, note.layer), 0)
            if duration > 1:
                hold_ms = self._hold_ms(note.tick, duration, tempo_segments)
                if hold_ms > len(sound):
                    cache_key = int(round(hold_ms))
                    to_play = sustained_cache.get(cache_key)
                    if to_play is None:
                        to_play = sustain_segment(sound, hold_ms, self._release_ms)
                        sustained_cache[cache_key] = to_play

            mixer.overlay(to_play, position=pos)

        return mixer.to_audio_segment()


def render_audio(
    song_path,
    output_path,
    default_sound_path="sounds",
    custom_sound_path="sounds",
    format: str = "wav",
    sample_rate: int = 44100,
    channels: int = 2,
    bit_depth: int = 16,
    target_bitrate: int = 320,
    target_size: Optional[int] = None,
    ignore_missing_instruments: bool = False,
    exclude_locked_layers: bool = False,
    release_ms: float = DEFAULT_RELEASE_MS,
    **unused_kwargs
) -> int:
    """Drop-in replacement for nbswave.render_audio that honours note durations.

    Returns the number of held notes found in the song.
    """
    durations = read_durations(song_path)
    song = pynbs.read(song_path)
    renderer = SustainSongRenderer(song, durations, default_sound_path, release_ms)
    renderer.load_instruments(custom_sound_path)
    renderer.mix_song(
        ignore_missing_instruments,
        exclude_locked_layers,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        channels=channels,
    ).save(
        output_path,
        format,
        bit_depth // 8,
        sample_rate,
        channels,
        target_bitrate,
        target_size,
    )
    return len(durations)


# --------------------------------------------------------------------------
# Command line (handy for testing the exporter outside Note Block Studio)
# --------------------------------------------------------------------------


def legato_durations(song_path, max_hold: int = 64) -> Dict[Tuple[int, int], int]:
    """Durations that hold every note until the next note in its layer (for testing on any song)."""
    song = pynbs.read(song_path)
    by_layer = {}  # type: Dict[int, list]
    for note in song.notes:
        by_layer.setdefault(note.layer, []).append(note.tick)
    durations = {}
    for layer, ticks in by_layer.items():
        ticks = sorted(set(ticks))
        for current, following in zip(ticks, ticks[1:]):
            gap = min(following - current, max_hold)
            if gap > 1:
                durations[(current, layer)] = gap
    return durations


def _main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="Render a .nbs song to audio with held notes.")
    ap.add_argument("song", help="input .nbs file")
    ap.add_argument("output", help="output audio file (.wav, .mp3, .ogg, .flac)")
    ap.add_argument("--sounds", required=True, help="Note Block Studio's sound folder (Data/Sounds)")
    ap.add_argument("--custom-sounds", help="folder with custom instrument sounds (default: same as --sounds)")
    ap.add_argument("--legato", action="store_true", help="ignore stored lengths and hold every note until the next one in its layer")
    ap.add_argument("--release", type=float, default=DEFAULT_RELEASE_MS, help="release fade in ms (default %(default)s)")
    args = ap.parse_args(argv)

    fmt = args.output.rsplit(".", 1)[-1].lower()
    durations = legato_durations(args.song) if args.legato else read_durations(args.song)
    song = pynbs.read(args.song)
    renderer = SustainSongRenderer(song, durations, args.sounds, args.release)
    renderer.load_instruments(args.custom_sounds or args.sounds)
    renderer.mix_song(ignore_missing_instruments=True).save(args.output, fmt)
    print("Rendered {} ({} held notes)".format(args.output, len(durations)))


if __name__ == "__main__":
    _main()
