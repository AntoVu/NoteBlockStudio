// =====================================================================
// Note durations ("stretched" / sustained notes)
// ---------------------------------------------------------------------
// Every note can have a duration, measured in ticks. 0 (the default)
// means "play the sound once, like a normal note block". A duration of
// N means "hold the note for N ticks" — the audio exporter keeps the
// sound ringing for that long and the editor draws a tail after it.
//
// Durations only ever *extend* a note: if the sound is naturally longer
// than the duration, it plays normally.
//
// Storage:
//   song_dur[tick, layer]       durations of notes in the song grid
//   selection_dur[a, b]         durations of notes inside the selection
//
// Both arrays are sparse, so always read them with dur_get() which
// returns 0 for anything that was never set.
//
// .nbs files: durations are written as an extra chunk after the custom
// instruments section. Note Block Studio, pynbs and nbswave all stop
// reading after the custom instruments, so files stay fully readable by
// every other tool (they just ignore the durations).
//
// Selection codes (clipboard, undo, patterns): durations are appended
// after a "~" as "a,b,d;" entries. Codes without durations are
// byte-for-byte identical to vanilla Note Block Studio.
// =====================================================================

#macro DUR_CHUNK_VERSION 1
#macro DUR_MAX 32000


/// @function dur_get(arr, a, b)
/// @description Safely reads a duration from a sparse 2D array. Returns 0 if unset.
function dur_get(arr, a, b) {
	if (!is_array(arr) || a < 0 || a >= array_length(arr)) return 0
	var row = arr[a]
	if (!is_array(row) || b < 0 || b >= array_length(row)) return 0
	var v = row[b]
	if (!is_real(v)) return 0
	return v
}


/// @function note_dur_get(tick, layer)
function note_dur_get(a, b) {
	return dur_get(song_dur, a, b)
}


/// @function note_dur_set(tick, layer, duration)
function note_dur_set(a, b, d) {
	d = median(0, floor(d), DUR_MAX)
	if (d == 0 && dur_get(song_dur, a, b) == 0) return 0 // Nothing to do, keep the array sparse
	song_dur[a, b] = d
	if (d > song_max_dur) song_max_dur = d
	return 1
}


/// @function sel_dur_get(a, b)
function sel_dur_get(a, b) {
	return dur_get(selection_dur, a, b)
}


/// @function sel_dur_set(a, b, duration)
function sel_dur_set(a, b, d) {
	d = median(0, floor(d), DUR_MAX)
	if (d == 0 && dur_get(selection_dur, a, b) == 0) return 0
	selection_dur[a, b] = d
	if (d > song_max_dur) song_max_dur = d
	return 1
}


/// @function song_durations_write()
/// @description Appends the duration chunk to the song buffer (global `buffer`). Called by save_song().
function song_durations_write() {
	var a, b, count = 0
	for (a = 0; a <= enda; a++) {
		if (a < arraylength && colamount[a] > 0) {
			for (b = 0; b <= collast[a]; b++) {
				if (song_exists[a, b] && note_dur_get(a, b) > 0) count++
			}
		}
	}
	if (count == 0) return 0 // No sustained notes: file is identical to vanilla NBS

	// Magic "DURS"
	buffer_write(buffer, buffer_u8, 68)
	buffer_write(buffer, buffer_u8, 85)
	buffer_write(buffer, buffer_u8, 82)
	buffer_write(buffer, buffer_u8, 83)
	buffer_write(buffer, buffer_u8, DUR_CHUNK_VERSION)
	buffer_write(buffer, buffer_s32, count)
	for (a = 0; a <= enda; a++) {
		if (a < arraylength && colamount[a] > 0) {
			for (b = 0; b <= collast[a]; b++) {
				if (song_exists[a, b]) {
					var d = note_dur_get(a, b)
					if (d > 0) {
						buffer_write(buffer, buffer_s32, a) // tick
						buffer_write(buffer, buffer_u16, b) // layer
						buffer_write(buffer, buffer_s32, d) // duration in ticks
					}
				}
			}
		}
	}
	return count
}


/// @function song_durations_read()
/// @description Reads the duration chunk if present. Called by open_song_nbs() after the custom instruments.
function song_durations_read() {
	var size = buffer_get_size(buffer)
	var pos = buffer_tell(buffer)
	if (pos + 9 > size) return 0
	if (buffer_read(buffer, buffer_u8) != 68) return 0
	if (buffer_read(buffer, buffer_u8) != 85) return 0
	if (buffer_read(buffer, buffer_u8) != 82) return 0
	if (buffer_read(buffer, buffer_u8) != 83) return 0
	var ver = buffer_read(buffer, buffer_u8)
	if (ver < 1) return 0
	var count = buffer_read(buffer, buffer_s32)
	if (count <= 0) return 0
	var loaded = 0
	for (var i = 0; i < count; i++) {
		if (buffer_tell(buffer) + 10 > size) break
		var a = buffer_read(buffer, buffer_s32)
		var b = buffer_read(buffer, buffer_u16)
		var d = buffer_read(buffer, buffer_s32)
		if (a >= 0 && a < arraylength && b < arrayheight) {
			if (song_exists[a, b]) {
				note_dur_set(a, b, d)
				loaded++
			}
		}
	}
	return loaded
}


/// @function dur_code_suffix_selection()
/// @description Returns the "~a,b,d;..." suffix for the current selection, or "" if no note in it is sustained.
function dur_code_suffix_selection() {
	var a, b, d, str = ""
	for (a = 0; a < selection_l; a++) {
		if (selection_colfirst[a] > -1) {
			for (b = selection_colfirst[a]; b <= selection_collast[a]; b++) {
				if (selection_exists[a, b]) {
					d = sel_dur_get(a, b)
					if (d > 0) str += string(a) + "," + string(b) + "," + string(d) + ";"
				}
			}
		}
	}
	if (str == "") return ""
	return "~" + str
}


/// @function dur_code_suffix_region(x1, y1, x2, y2)
/// @description Same as above, for a region of the song grid (used by region_code_get for undo).
function dur_code_suffix_region(x1, y1, x2, y2) {
	var a, b, d, str = ""
	for (a = x1; a < x2; a++) {
		if (a <= enda && a < arraylength && colamount[a] > 0) {
			for (b = max(y1, colfirst[a]); b < y2 && b <= collast[a]; b++) {
				if (song_exists[a, b]) {
					d = note_dur_get(a, b)
					if (d > 0) str += string(a - x1) + "," + string(b - y1) + "," + string(d) + ";"
				}
			}
		}
	}
	if (str == "") return ""
	return "~" + str
}


/// @function dur_code_split(code)
/// @description Splits an (uncompressed) selection code into its main part and its duration entries.
/// Returns the main part. The entries are stored in global.dur_pending as [a, b, d] arrays.
function dur_code_split(str) {
	global.dur_pending = []
	var p = string_pos("~", str)
	if (p == 0) return str
	var main = string_copy(str, 1, p - 1)
	var rest = string_delete(str, 1, p)
	var n = 0
	while (string_length(rest) > 0) {
		var semi = string_pos(";", rest)
		var entry
		if (semi == 0) {
			entry = rest
			rest = ""
		} else {
			entry = string_copy(rest, 1, semi - 1)
			rest = string_delete(rest, 1, semi)
		}
		var c1 = string_pos(",", entry)
		if (c1 == 0) continue
		var rest2 = string_delete(entry, 1, c1)
		var c2 = string_pos(",", rest2)
		if (c2 == 0) continue
		var sa = string_digits(string_copy(entry, 1, c1 - 1))
		var sb = string_digits(string_copy(rest2, 1, c2 - 1))
		var sd = string_digits(string_delete(rest2, 1, c2))
		if (sa == "" || sb == "" || sd == "") continue
		global.dur_pending[n++] = [real(sa), real(sb), real(sd)]
	}
	return main
}


/// @function dur_pending_apply_selection()
/// @description Applies global.dur_pending to the selection arrays (relative coordinates).
function dur_pending_apply_selection() {
	var pend = global.dur_pending
	for (var i = 0; i < array_length(pend); i++) {
		var e = pend[i]
		var a = e[0], b = e[1]
		if (a < selection_l && selection_colfirst[a] > -1 && b >= selection_colfirst[a] && b <= selection_collast[a]) {
			if (selection_exists[a, b]) sel_dur_set(a, b, e[2])
		}
	}
	global.dur_pending = []
}


/// @function dur_pending_apply_song(x, y)
/// @description Applies global.dur_pending to the song grid, offset by (x, y).
function dur_pending_apply_song(xx, yy) {
	var pend = global.dur_pending
	for (var i = 0; i < array_length(pend); i++) {
		var e = pend[i]
		var a = xx + e[0], b = yy + e[1]
		if (a < arraylength && b < arrayheight) {
			if (song_exists[a, b]) note_dur_set(a, b, e[2])
		}
	}
	global.dur_pending = []
}


/// @function midi_note_release(open_notes, track, channel, note, pos)
/// @description MIDI import: a note-off (or re-trigger) closes the matching held note and stores its length.
function midi_note_release(map, track, channel, note, pos) {
	var key = string(channel) + ":" + string(note)
	if (ds_map_exists(map, key)) {
		var ev = map[? key]
		midi_eventlen[track, ev] = max(0, pos - midi_eventx[track, ev])
		ds_map_delete(map, key)
	}
}


/// @function ticks_to_seconds(ticks)
/// @description Converts a tick count to seconds at the current tempo (used for preview playback).
function ticks_to_seconds(t) {
	return t / max(0.25, tempo)
}


/// @function draw_note_tail(x, y, ticks, alpha, selected, clip_left, clip_right)
/// @description Draws the "held" bar after a note block. x, y = top-left of the note block.
/// The bar is clipped horizontally to [clip_left, clip_right] so it never covers other UI.
function draw_note_tail(xx, yy, ticks, alpha, sel, clip_l, clip_r) {
	if (ticks <= 1) return 0
	var bar_l = max(xx + 30, clip_l)
	var bar_r = min(xx + 32 * ticks - 4, clip_r)
	if (bar_r <= bar_l) return 0
	var col = make_color_rgb(70, 160, 255)
	if (sel) col = make_color_rgb(255, 200, 60)
	draw_set_color(col)
	draw_set_alpha(0.35 * alpha)
	draw_rectangle(bar_l, yy + 11, bar_r, yy + 20, false)
	var cap_x = xx + 32 * ticks - 6
	if (cap_x >= clip_l && cap_x + 2 <= clip_r) {
		draw_set_alpha(0.8 * alpha)
		draw_rectangle(cap_x, yy + 8, cap_x + 2, yy + 23, false) // End cap
	}
	draw_set_alpha(1)
	draw_theme_color()
	return 1
}
