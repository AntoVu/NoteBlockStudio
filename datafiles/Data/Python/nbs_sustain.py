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
duration: the attack of the sound plays normally, the middle is time-stretched
(phase vocoder, so the pitch stays put and nothing repeats) until the note is
released, and then the sound's natural tail plays.

How the held part sounds depends on the instrument (see sound_style):
wind-like sounds hold a steady level with a gentle vibrato, plucked and struck
sounds slowly ring out, and drums are never stretched.

Notes are only ever *extended*: if a sound is naturally longer than the note's
duration it plays exactly like it does in vanilla Note Block Studio. The exception
is the bundled long sustained recordings (STYLE_SUSTAINED): their length always
follows the note's (at least one tick), cut short with a release when needed.

Written for Python 3.8 + nbswave 0.4 (the versions bundled with NBS).
"""

import math
import struct
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import pydub
import pynbs

from nbswave import audio, nbs
from nbswave.main import DEFAULT_INSTRUMENTS, MissingInstrumentException, SongRenderer

__all__ = [
    "read_durations",
    "parse_duration_chunk",
    "sustain_samples",
    "sustain_segment",
    "sound_style",
    "render_held_raw",
    "SustainSongRenderer",
    "render_audio",
]

DURATION_MAGIC = b"DURS"

# Tunables (milliseconds unless noted)
XFADE_MS = 15.0  # Joins between attack, stretched middle and tail
ATTACK_SKIP_MS = 25.0  # How far past the loudest point the stretched part may start
STEADY_DROP_MS = 150.0  # Steady style: glide down to the tail's level at the end of the hold
VIBRATO_RATE_HZ = 5.5
VIBRATO_CENTS = 12.0
VIBRATO_RAMP_MS = 350.0  # Vibrato fades in over this long after the attack

# How each sound behaves while held (matched on file name, case ignored)
STYLE_FADE = "fade"  # Rings out slowly: the sound's own decay, stretched
STYLE_STEADY = "steady"  # Holds its level, with vibrato
STYLE_DRUM = "drum"  # Never stretched
STYLE_SUSTAINED = "sustained"  # Held like steady, but the note's length also cuts it short
STEADY_SOUNDS = {"flute.ogg", "didgeridoo.ogg", "bit.ogg"}
# Long real-instrument recordings (VSCO-2 CE)
SUSTAINED_SOUNDS = {
    "trumpet_sus.ogg", "horn_sus.ogg", "trombone_sus.ogg", "clarinet_sus.ogg", "violin_sus.ogg", "cello_sus.ogg",
    "organ.ogg",
}
RELEASE_MS = 120  # Fade when a sustained sound is cut short (same as the editor)
DRUM_SOUNDS = {"bdrum.ogg", "sdrum.ogg", "click.ogg", "snare_marching.ogg", "cymbal_crash.ogg"}


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


def sound_style(filename: str) -> str:
    """STYLE_STEADY, STYLE_DRUM or STYLE_FADE for a sound file (path or name)."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name in STEADY_SOUNDS:
        return STYLE_STEADY
    if name in SUSTAINED_SOUNDS:
        return STYLE_SUSTAINED
    if name in DRUM_SOUNDS:
        return STYLE_DRUM
    return STYLE_FADE


def _envelope(mono: np.ndarray, frame_rate: int, ms: float = 10.0) -> np.ndarray:
    """RMS envelope with a `ms` window."""
    win = max(1, int(frame_rate * ms / 1000.0))
    # Moving average via a running sum (same result as np.convolve(..., mode="same"), much faster)
    n = len(mono)
    c = np.concatenate([np.zeros(1), np.cumsum(np.concatenate([mono * mono, np.zeros(win)]))])
    hi = np.arange(n) + (win - 1) // 2 + 1
    lo = np.maximum(hi - win, 0)
    return np.sqrt(np.maximum(c[np.minimum(hi, n)] - c[np.minimum(lo, n)], 0.0) / win)


def _split_points(mono: np.ndarray, frame_rate: int) -> Tuple[int, int]:
    """(attack end, body end) from the loudness envelope. The body is what gets stretched."""
    env = _envelope(mono, frame_rate)
    peak = int(np.argmax(env))
    audible = np.nonzero(env > env.max() * 0.1)[0]  # -20 dB
    last = int(audible[-1]) if len(audible) else len(mono) - 1
    a = peak + min(int(ATTACK_SKIP_MS / 1000.0 * frame_rate), max(0, (last - peak) // 6))
    return a, max(last, a + 1)


def _spectral_peaks(mag: np.ndarray) -> np.ndarray:
    mid = mag[2:-2]
    is_peak = (mid > mag[:-4]) & (mid > mag[1:-3]) & (mid >= mag[3:-1]) & (mid >= mag[4:])
    return np.nonzero(is_peak)[0] + 2


def _phase_vocoder(body: np.ndarray, n_out: int, want: int = 2048) -> np.ndarray:
    """Read slowly through `body` to make `n_out` samples at the same pitch.

    Phases are locked to the nearest spectral peak, which keeps the tone clear.
    """
    win = want
    while win > len(body) and win > 256:
        win //= 2
    hop = win // 4
    src = np.concatenate([body, np.zeros(max(0, win + hop - len(body)))])
    w = np.hanning(win)
    frames = int(math.ceil(n_out / float(hop))) + 2
    pos = np.linspace(0, len(src) - win - hop, frames).astype(int)
    out = np.zeros(frames * hop + win)
    norm = np.zeros_like(out)
    bins = np.arange(win // 2 + 1)
    phase = None
    for i, p in enumerate(pos):
        x0 = np.fft.rfft(src[p:p + win] * w)
        x1 = np.fft.rfft(src[p + hop:p + hop + win] * w)
        mag = np.abs(x1)
        ang0, ang1 = np.angle(x0), np.angle(x1)
        if phase is None:
            phase = ang1
        else:
            peaks = _spectral_peaks(mag)
            if len(peaks):
                idx = np.searchsorted(peaks, bins)
                after = peaks[np.clip(idx, 0, len(peaks) - 1)]
                before = peaks[np.clip(idx - 1, 0, len(peaks) - 1)]
                near = np.where(np.abs(bins - before) < np.abs(after - bins), before, after)
                phase = phase[near] + ang1[near] - ang0[near] + ang1 - ang1[near]
            else:
                phase = phase + ang1 - ang0
        o = i * hop
        out[o:o + win] += np.fft.irfft(mag * np.exp(1j * phase), win) * w
        norm[o:o + win] += w * w
    return (out / np.maximum(norm, 1e-6))[:n_out]


def _vibrato(x: np.ndarray, frame_rate: int, start: int) -> np.ndarray:
    """Gentle pitch wobble from `start` on, fading in over VIBRATO_RAMP_MS."""
    n = x.shape[0] - start
    if n <= 1:
        return x
    t = np.arange(n) / float(frame_rate)
    depth = (2 ** (VIBRATO_CENTS / 1200.0) - 1) * np.minimum(1.0, t / (VIBRATO_RAMP_MS / 1000.0))
    speed = 1 + depth * np.sin(2 * math.pi * VIBRATO_RATE_HZ * t)
    pos = np.minimum(np.cumsum(speed) - speed[0], n - 1)
    y = x.copy()
    for c in range(x.shape[1]):
        y[start:, c] = np.interp(pos, np.arange(n), x[start:, c])
    return y


def _stretch(x: np.ndarray, frame_rate: int, target: int, style: str):
    """Attack + stretched body (`target` samples in total) + natural tail.

    Returns (samples, attack end, fade ratio), or None if the sound can't be stretched.
    The fade ratio is the body's level at its end relative to its start (the editor uses it).
    """
    if x.shape[0] < 32:
        return None
    mono = x.mean(axis=1)
    if float(np.max(np.abs(mono))) <= 1e-9:
        return None
    a, b = _split_points(mono, frame_rate)
    xf = max(16, min(int(XFADE_MS / 1000.0 * frame_rate), (b - a) // 3))
    n_body = max(xf * 2, target - a)
    raw = _phase_vocoder(mono[a:b], n_body)

    # Put the stretched body on the level curve the style wants (levels in dB)
    env = _envelope(mono, frame_rate, 20.0)
    got = _envelope(raw, frame_rate, 30.0)
    db_a, db_b = [20 * math.log10(max(float(env[i]), 1e-9)) for i in (a, b - 1)]
    if style == STYLE_STEADY:
        drop = int(STEADY_DROP_MS / 1000.0 * frame_rate)
        want_db = np.full(n_body, db_a)
        if n_body > drop:
            want_db[-drop:] = np.linspace(db_a, db_b, drop)
    else:
        idx = np.linspace(a, b - 1, 200).astype(int)
        curve = 20 * np.log10(np.maximum(env[idx], 1e-9))
        want_db = np.interp(np.linspace(0.0, 1.0, n_body), np.linspace(0.0, 1.0, 200), curve)
    mono_held = raw * (10 ** (want_db / 20) / np.maximum(got, got.max() * 0.02 + 1e-9))

    # Stretch in mono, then restore each channel's share (keeps panning)
    mono_rms = math.sqrt(float(np.mean(mono[a:b] ** 2))) + 1e-12
    shares = np.sqrt(np.mean(x[a:b] ** 2, axis=0)) / mono_rms
    held = mono_held[:, None] * shares[None, :]

    # Equal-power joins: attack -> body -> natural tail
    up = np.sin(np.linspace(0.0, math.pi / 2, xf))[:, None]
    held[:xf] = x[a:a + xf] * up[::-1] + held[:xf] * up
    tail = x[b:]
    m = min(xf, tail.shape[0])
    if m:
        down = np.cos(np.linspace(0.0, math.pi / 2, m))[:, None]
        held[-m:] = held[-m:] * down + tail[:m] * down[::-1]
        tail = tail[m:]
    fade = 10 ** ((db_b - db_a) / 20)
    return np.concatenate([x[:a], held, tail], axis=0), a, fade


def sustain_samples(
    x: np.ndarray,
    frame_rate: int,
    hold_ms: float,
    style: str = STYLE_FADE,
    vibrato: bool = False,
) -> np.ndarray:
    """Extend a sound so it is held for `hold_ms` milliseconds.

    `x` is a float array of shape (frames, channels). Returns a new float array,
    or `x` itself if the sound is already long enough, silent, or a drum.
    """
    target = int(round(hold_ms * frame_rate / 1000.0))
    if style == STYLE_DRUM or target <= x.shape[0]:
        return x
    result = _stretch(x, frame_rate, target, style)
    if result is None:
        return x
    out, attack_end, _ = result
    return _vibrato(out, frame_rate, attack_end) if vibrato else out


def sustain_segment(sound: pydub.AudioSegment, hold_ms: float, style: str = STYLE_FADE) -> pydub.AudioSegment:
    """pydub wrapper around sustain_samples. Steady sounds get vibrato."""
    if hold_ms <= len(sound):
        return sound
    channels = sound.channels
    samples = np.array(sound.get_array_of_samples(), dtype=np.float64)
    samples = samples.reshape(-1, channels)
    out = sustain_samples(samples, sound.frame_rate, hold_ms, style, vibrato=(style == STYLE_STEADY))
    if out is samples:
        return sound
    limit = float(2 ** (8 * sound.sample_width - 1) - 1)
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[sound.sample_width]
    data = np.clip(np.rint(out), -limit - 1, limit).astype(dtype).tobytes()
    return sound._spawn(data)


def cut_segment(sound: pydub.AudioSegment, hold_ms: float) -> pydub.AudioSegment:
    """Play `sound` for `hold_ms`, then fade out over RELEASE_MS. Returns `sound` if it isn't longer than that."""
    end = int(round(hold_ms)) + RELEASE_MS
    if end >= len(sound):
        return sound
    return sound[:end].fade_out(RELEASE_MS)


def trim_silence(seg: pydub.AudioSegment, floor_db: float = -60.0, keep_ms: int = 100) -> pydub.AudioSegment:
    """Cut the silence at the end (below `floor_db` dBFS), keeping `keep_ms` after the last sound.

    The mix is sized by each sound's full length, so cut-short and faded notes leave silence behind.
    """
    x = np.abs(np.array(seg.get_array_of_samples()))
    loud = np.nonzero(x > (2 ** (8 * seg.sample_width - 1)) * 10 ** (floor_db / 20))[0]
    if not len(loud):
        return seg
    end_ms = (loud[-1] // seg.channels) * 1000.0 / seg.frame_rate + keep_ms
    return seg[:int(math.ceil(end_ms))] if end_ms < len(seg) else seg


def render_held_raw(in_path, out_path, seconds: float = 16.0, frame_rate: int = 44100) -> dict:
    """Editor helper: stretch raw 16-bit stereo PCM to `seconds`, steady level, no vibrato.

    The editor adds vibrato and the fade itself while playing; returns {"fade": ratio}
    for that (body level at its end relative to its start).
    """
    x = np.fromfile(in_path, dtype="<i2").reshape(-1, 2).astype(np.float64)
    result = _stretch(x, frame_rate, int(seconds * frame_rate), STYLE_STEADY)
    if result is None:
        raise ValueError("sound can't be stretched")
    out, _, fade = result
    np.clip(np.rint(out), -32768, 32767).astype("<i2").tofile(out_path)
    return {"fade": fade}


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
    ):
        if default_sound_path is None:
            super().__init__(song)
        else:
            super().__init__(song, default_sound_path)
        self._durations = durations

    def _style(self, ins: int) -> str:
        """How instrument `ins` behaves when held (see sound_style)."""
        first_custom = self._song.header.default_instruments
        if ins < first_custom:
            return sound_style(DEFAULT_INSTRUMENTS[ins]) if ins < len(DEFAULT_INSTRUMENTS) else STYLE_FADE
        custom = self._song.instruments
        i = ins - first_custom
        return sound_style(custom[i].file) if i < len(custom) else STYLE_FADE

    def _hold_ms(self, tick: int, duration: int, tempo_segments: Sequence[float]) -> float:
        """How long (ms) a note starting at `tick` is held, including tempo changes."""
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
                style = self._style(ins)

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
            sustained = style == STYLE_SUSTAINED
            if duration > 1 or sustained:
                hold_ms = self._hold_ms(note.tick, max(duration, 1), tempo_segments)
                cache_key = int(round(hold_ms))
                to_play = sustained_cache.get(cache_key)
                if to_play is None:
                    if hold_ms > len(sound):
                        to_play = sustain_segment(sound, hold_ms, STYLE_STEADY if sustained else style)
                    else:
                        to_play = cut_segment(sound, hold_ms) if sustained else sound
                    sustained_cache[cache_key] = to_play

            mixer.overlay(to_play, position=pos)

        return trim_silence(mixer.to_audio_segment())


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
    layers=None,
    **unused_kwargs
) -> int:
    """Drop-in replacement for nbswave.render_audio that plays note durations.

    `layers`: only render notes on these layers (for stems). Returns the number of held notes found in the song.
    """
    durations = read_durations(song_path)
    song = pynbs.read(song_path)
    if layers is not None:
        keep = set(layers)
        song.notes = [n for n in song.notes if n.layer in keep]
        durations = {k: v for k, v in durations.items() if k[1] in keep}
    renderer = SustainSongRenderer(song, durations, default_sound_path)
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
    args = ap.parse_args(argv)

    fmt = args.output.rsplit(".", 1)[-1].lower()
    durations = legato_durations(args.song) if args.legato else read_durations(args.song)
    song = pynbs.read(args.song)
    renderer = SustainSongRenderer(song, durations, args.sounds)
    renderer.load_instruments(args.custom_sounds or args.sounds)
    renderer.mix_song(ignore_missing_instruments=True).save(args.output, fmt)
    print("Rendered {} ({} held notes)".format(args.output, len(durations)))


if __name__ == "__main__":
    _main()
