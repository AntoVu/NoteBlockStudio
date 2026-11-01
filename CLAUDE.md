# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fork of Open Note Block Studio (OpenNBS), a GameMaker (GML) editor for Minecraft note block songs (`.nbs`).
This fork adds **note lengths / stretched notes**; `NOTE_LENGTHS.md` is the spec and the list of touched
scripts, so read it before changing anything related to durations.

Upstream considers the GML app feature-complete (a Qt rewrite is in progress), so upstream `main` only takes
minor bug fixes, and upstream development happens on the `development` branch.

## Building and running

- No CLI build, linter or test suite. The project (`Minecraft Note Block Studio.yyp`) is opened and built in the
  GameMaker IDE. The target runtime is **2022.6** (`gm_runtime_version` in `scripts/macros/macros.gml`, and the
  `.yyp` IDEVersion is 2022.6.1.x). The README's "2022.0.3 LTS" is out of date. 32-bit DLLs.
- Git LFS is required: `ffmpeg.exe`, `ffprobe.exe` and the `extensions/pygml` DLLs/zips are LFS files.
- Debug output from a built exe: `datafiles/Launch Debug.bat` runs it with `-debugoutput debug.txt`.
- Python export module (`datafiles/Data/Python`): the project lists 869 generated files under `Lib/`
  (gitignored), and the build fails without them. Generate them with Python 3.8.10 32-bit (exact version
  required) by running this from `datafiles/Data/Python`. uv works instead of poetry:
  ```
  uv venv .venv --python "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"
  uv pip install --python .venv/Scripts/python.exe nbswave==0.4.0 numpy==1.24.4 pydub==0.25.1 pynbs==1.1.0
  .venv/Scripts/python.exe package.py
  ```
  Then delete `.venv`. The pins match `poetry.lock`. See `datafiles/Data/Python/README.md`.
- The only check you can run without GameMaker is the sustain exporter (Python 3.8–3.11):
  ```
  pip install nbswave==0.4.0
  python datafiles/Data/Python/nbs_sustain.py song.nbs out.wav --sounds "<NBS folder>/Data/Sounds" [--legato]
  ```

## Project file hygiene

Opening or saving the project in a newer GameMaker rewrites the `.yyp` and many `sprites/*/*.yy` files
(`resourceVersion`, field order, removed shader entries). That churn is not a real change: don't commit it
unless the user asks to. When adding a script or object, its `.yy` and its `.yyp` resource entry must both
exist. Upstream's rule for included Python files is the same: register them by saving in the IDE, and don't
commit the project file changes that come with it.

## Architecture

- **One room, one controller.** `rooms/rm_app` holds `obj_controller`, whose events each call a single script:
  `control_create` (all global state), `control_step`, `control_draw` (~2500 lines: input handling, menus,
  shortcuts and drawing all live here), `control_end`, `control_http`, `control_networking`. Nearly all logic is
  in `scripts/` (about 400 scripts, one function per script folder), which runs in the controller's scope and
  reads and writes its instance variables directly.
- **Song data** lives in parallel 2D arrays on the controller, indexed `[tick, layer]`: `song_exists`, `song_ins`,
  `song_key`, `song_vel`, `song_pan`, `song_pit`, `song_played`, `song_added`. The fork's lengths are stored in
  `song_dur` (helpers in `scripts/note_durations`). Always mutate through `add_block*` / `remove_block*` /
  `change_block*`, which keep per-column and per-row counts (`colamount`, `rowamount`, `colfirst`, `collast`,
  `enda`, `endb`) consistent.
- **Selection** is a separate set of arrays (`selection_*` scripts). Clipboard, undo and `.nbp` patterns
  serialize selections to text "codes" (`selection_code_*`, `region_code_*`).
- **Undo/redo:** `history_set(type, args...)` pushes onto the `history` 2D array. Types are `h_*` macros in
  `macros.gml` and are replayed by `action_undo` / `action_redo`. Any new editable operation needs an `h_*`
  entry and handling in both of those.
- **Windows/dialogs:** `window` holds a `w_*` macro (`macros.gml`). `draw_windows` switches on
  `window mod w_menu` and calls the matching `draw_window_*` script. New dialogs need a `w_*` macro, a case
  there and a `draw_window_*` script.
- **File I/O:** `.nbs` is read by `open_song_nbs` and written by `save_song` through the `buffer_*` helpers.
  `nbs_version` is in `macros.gml`. The fork's `DURS` chunk goes *after* the custom-instruments section so
  vanilla readers ignore it, and a song with no held notes must stay byte-identical to vanilla output. MIDI:
  `open_midi` / `import_midi`. Minecraft exports: `schematic_*`, `nbt_*`/`TAG_*`, datapack. All of these ignore
  lengths.
- **Audio export** goes from GML to Python through the `pygml` extension: `audio_export.gml` calls
  `python_call_function("audio_export", "main", ...)`, and `audio_export.py` uses `nbs_sustain.py` (and falls
  back to plain nbswave).
- Settings go to `settings.ini` in `game_save_id` (`load_settings` / `save_settings`). Paths are the
  `*_directory` macros in `macros.gml`, written Windows-style (`\\`).
