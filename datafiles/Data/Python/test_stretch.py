"""Checks for the held-note stretch in nbs_sustain.py. Run: python test_stretch.py"""
import os
import struct
import tempfile

import numpy as np

import nbs_sustain as ns

SR = 44100


def pluck(freq=440.0, seconds=0.5, channels=2):
    """A plucked-string-like test sound: fast attack, exponential decay."""
    t = np.arange(int(SR * seconds)) / SR
    env = np.minimum(1.0, t / 0.005) * np.exp(-t * 6.0)
    mono = 10000.0 * env * np.sin(2 * np.pi * freq * t)
    return np.repeat(mono[:, None], channels, axis=1)


def strongest_freq(x):
    x = x * np.hanning(len(x))
    s = np.abs(np.fft.rfft(x, 1 << 17))
    return float(np.argmax(s)) * SR / (1 << 17)


def level_db(mono, ms=10):
    win = int(SR * ms / 1000)
    rms = np.sqrt(np.convolve(mono * mono, np.ones(win) / win, mode="valid"))[::win // 2]
    return 20 * np.log10(np.maximum(rms, 1e-6))


def test_length_and_attack():
    x = pluck()
    for style in (ns.STYLE_FADE, ns.STYLE_STEADY):
        out = ns.sustain_samples(x, SR, 2000, style)
        assert out.shape[0] >= 2 * SR, out.shape
        assert out.shape[0] < 2 * SR + x.shape[0], out.shape
        a, _ = ns._split_points(x.mean(axis=1), SR)
        assert np.array_equal(out[:a], x[:a]), "attack must be untouched"


def test_pitch_holds():
    x = pluck(440.0)
    out = ns.sustain_samples(x, SR, 2000, ns.STYLE_STEADY)
    mid = out[SR - 4096: SR + 4096, 0]
    f = strongest_freq(mid)
    assert abs(f - 440.0) / 440.0 < 0.01, f


def test_no_retrigger():
    x = pluck()
    for style in (ns.STYLE_FADE, ns.STYLE_STEADY):
        out = ns.sustain_samples(x, SR, 2000, style)
        a, _ = ns._split_points(x.mean(axis=1), SR)
        db = level_db(out[a + 2205: 2 * SR - 6615].mean(axis=1))
        # A re-pluck shows up as a sudden jump in loudness
        assert np.max(np.diff(db)) < 3.0, (style, float(np.max(np.diff(db))))


def test_steady_holds_level_fade_decays():
    x = pluck()
    steady = level_db(ns.sustain_samples(x, SR, 2000, ns.STYLE_STEADY).mean(axis=1))
    fade = level_db(ns.sustain_samples(x, SR, 2000, ns.STYLE_FADE).mean(axis=1))
    q1, q3 = len(steady) // 4, 3 * len(steady) // 4
    assert abs(steady[q1] - steady[q3]) < 2.0, (steady[q1], steady[q3])
    assert fade[q1] - fade[q3] > 3.0, (fade[q1], fade[q3])


def test_vibrato_moves_pitch_slightly():
    x = pluck(440.0)
    out = ns.sustain_samples(x, SR, 2000, ns.STYLE_STEADY, vibrato=True)
    freqs = [strongest_freq(out[i: i + 2048, 0]) for i in range(SR // 2, SR, 512)]
    spread = (max(freqs) - min(freqs)) / 440.0
    assert 0.003 < spread < 0.03, freqs


def test_drums_and_short_holds_unchanged():
    x = pluck()
    assert ns.sustain_samples(x, SR, 300, ns.STYLE_FADE) is x
    assert ns.sustain_samples(x, SR, 2000, ns.STYLE_DRUM) is x
    silent = np.zeros((SR // 2, 2))
    assert ns.sustain_samples(silent, SR, 2000, ns.STYLE_FADE) is silent


def test_style_lookup():
    assert ns.sound_style("flute.ogg") == ns.STYLE_STEADY
    assert ns.sound_style("Custom\\sub\\BIT.OGG") == ns.STYLE_STEADY
    assert ns.sound_style("sdrum.ogg") == ns.STYLE_DRUM
    assert ns.sound_style("harp.ogg") == ns.STYLE_FADE
    assert ns.sound_style("my_custom.wav") == ns.STYLE_FADE


def test_stereo_pan_kept():
    x = pluck()
    x[:, 1] *= 0.25  # panned left
    out = ns.sustain_samples(x, SR, 2000, ns.STYLE_STEADY)
    mid = out[SR - 4096: SR + 4096]
    ratio = np.sqrt(np.mean(mid[:, 1] ** 2) / np.mean(mid[:, 0] ** 2))
    assert abs(ratio - 0.25) < 0.02, ratio


def test_held_raw_roundtrip():
    x = np.rint(pluck()).astype("<i2")
    d = tempfile.mkdtemp()
    src, dst = os.path.join(d, "in.raw"), os.path.join(d, "out.raw")
    x.tofile(src)
    info = ns.render_held_raw(src, dst, 4.0)
    out = np.fromfile(dst, dtype="<i2").reshape(-1, 2)
    assert out.shape[0] >= 4 * SR, out.shape
    assert 0.0 < info["fade"] < 1.0, info


def test_duration_chunk():
    data = ns.DURATION_MAGIC + bytes([1]) + struct.pack("<i", 2)
    data += struct.pack("<iHi", 10, 3, 8) + struct.pack("<iHi", 20, 0, 0)
    assert ns.parse_duration_chunk(data) == {(10, 3): 8}
    assert ns.parse_duration_chunk(b"") == {}


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print("all {} passed".format(len(tests)))
