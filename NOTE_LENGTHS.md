# Note lengths (stretched notes) — fork notes

This fork of Open Note Block Studio lets a note be **held for several ticks** instead of
only playing for an instant. Held notes ring out for their full length when you export
audio (WAV/MP3/OGG/FLAC). Minecraft exports (schematics, data packs, etc.) ignore lengths,
since note blocks can't hold notes in-game.

## Using it

| What | How |
|---|---|
| Stretch one note | Hover a note and hold **Alt + scroll** (Alt + Shift + scroll = 4 ticks at a time) |
| Set length of a selection | Select notes → **Ctrl+Shift+L** (or Edit menu / right-click menu → *Set note length...*) |
| Legato | In *Set note length*, tick **Until next note** — each note is held until the next note in its layer |
| Clear lengths | Set length to 0, or use *Reset all properties* (Ctrl+Shift+U) |
| MIDI import | New **Keep note lengths** option (on by default). Percussion is never stretched |

A held note shows a blue bar after it (yellow when selected or hovered). Length 0 (or 1)
means a normal note. Lengths only ever *extend* a note: if the sound is naturally longer,
it plays exactly as before.

Copy/paste, undo/redo, moving selections, expand/compress (lengths double/halve),
patterns and the clip editor all keep lengths.

**How a held note sounds:** the attack plays once, the middle of the sound is time-stretched
(a phase vocoder, so the pitch stays put and nothing repeats), then the natural tail plays.
It depends on the instrument:
- Flute, didgeridoo and bit hold a steady level with a gentle vibrato (12 cents, 5.5 Hz).
- The bundled sustained real-instrument sounds (`*_sus.ogg`, `organ.ogg`) hold the same way, and always last
  exactly the note's length: shorter notes are cut with a 120 ms fade, and a note with no length lasts one tick.
- Drums (bass drum, snare, click) are never stretched.
- Everything else, including custom sounds, rings out slowly (its own decay, stretched).

**Preview vs. export:** the export stretches each held note to its exact length. The editor stretches
each instrument's sound once (16 s at its base pitch, made by `nbs_sustain.render_held_raw` the first
time it's needed, or when playback starts) and adds the vibrato or the fade while playing, so the end of
a note can sound a little different. Stretched sounds are cached in the `Held sounds` folder next to
`settings.ini`, keyed by the sound's content, so each sound is only stretched once. Bump
`HELD_CACHE_VERSION` in `scripts/note_durations` whenever the stretch changes. If the stretched sound can't be made, the editor loops the plain
sound as before.

## File format (backward compatible)

Lengths are stored in an extra chunk **after** the custom instruments section of the `.nbs` file:

```
4 bytes   "DURS"
u8        chunk version (1)
s32       count
count x { s32 tick, u16 layer, s32 length_in_ticks }      (little-endian)
```

Vanilla Note Block Studio, pynbs, nbswave and other tools stop reading after the custom
instruments, so they open these files normally (they just don't see the lengths). If a song has
no held notes, the file is byte-for-byte identical to a vanilla one. Saving the song in a vanilla
build drops the lengths.

In selection codes (clipboard, undo, `.nbp` patterns) lengths are appended after a `~` as
`a,b,length;` entries. Codes without held notes are unchanged.

## What changed

GameMaker (GML):
- `scripts/note_durations` *(new)* — storage helpers, `.nbs` chunk read/write, selection-code
  suffix, MIDI note-off helper, tail drawing.
- `scripts/draw_window_macro_setduration` *(new)* — *Set note length* window and `selection_setdur()`.
- Song/selection plumbing: `add_block*`, `remove_block*`, `selection_*`, `select_*`, `region_code_*`,
  `macro_reset`, `reset`, `control_create`, `action_undo/redo` (new `h_changedur` history entry).
- Editor: `control_draw` (Alt+scroll, tails, Ctrl+Shift+L, menus), `selection_draw`, `menu_click`,
  `draw_windows`, `macros` (`w_setduration`, `h_changedur`).
- Playback preview: `play_sound` (optional `sustain` seconds), `remove_emitters` (stop, vibrato, fade),
  `toggle_playing` (makes held sounds before playing), `instrument_free`, `control_draw` (no catch-up
  after making a held sound).
- MIDI import: `open_midi`, `midi_add_note`, `import_midi`, `reset_midi`, `draw_window_midi_import`,
  settings load/save (`note_lengths` under `[midi_import]`).
- `save_song` / `open_song_nbs` write/read the chunk.

Python (audio export):
- `datafiles/Data/Python/nbs_sustain.py` *(new)* — reads lengths and renders held notes on top of
  nbswave 0.4. Also usable from the command line (see below). `tests/test_stretch.py` checks the
  stretch.
- `datafiles/Data/Python/audio_export.py` — uses `nbs_sustain` (falls back to plain nbswave).
- Registered as an included file in the `.yyp`.

## Building

Same as upstream (see `README.md` and `datafiles/Data/Python/README.md`): GameMaker with the
**2022.6 runtime** (32-bit DLLs), Git LFS, and Python 3.8.10 32-bit + poetry for the export module.
`nbs_sustain.py` only uses packages that are already bundled (nbswave, pynbs, pydub, numpy).

## Testing the exporter without building

With Python 3.8–3.11:

```
pip install nbswave==0.4.0
python datafiles/Data/Python/nbs_sustain.py song.nbs out.wav --sounds "<NBS folder>/Data/Sounds" --legato
```

`--legato` holds every note until the next one in its layer, so you can hear the effect on any
existing song. Without it, the lengths stored in the file are used.

Stretch checks (on the 32-bit Python 3.8 with numpy): `python tests/test_stretch.py`.
