// =====================================================================
// MCP bridge: lets the NBS_MCP server edit the open song live.
// Protocol: one JSON object per line over local TCP (port MCP_PORT).
// Commands must never reach a blocking dialog (message, question, file picker).
// =====================================================================

#macro MCP_PORT 30011
#macro MCP_MAX_LINE_BYTES 8388608


/// @function mcp_bridge_init()
function mcp_bridge_init() {
	mcp_server = -1
	mcp_client = -1
	mcp_rx = buffer_create(4096, buffer_fixed, 1)
	mcp_rx_len = 0
	global.mcp_silent = false
	global.mcp_missing_sounds = ""
	if (!isplayer) mcp_server = network_create_server_raw(network_socket_tcp, MCP_PORT, 1)
}


/// @function mcp_bridge_event()
/// @description Handles the current async networking event. Returns true if it belonged to the bridge.
function mcp_bridge_event() {
	var id_ = async_load[? "id"]
	if (mcp_server < 0) return false
	var type = async_load[? "type"]

	if (id_ == mcp_server) {
		var sock = async_load[? "socket"]
		if (type == network_type_connect) {
			// GameMaker can't choose a bind address, so refuse anything that isn't this PC
			if (async_load[? "ip"] != "127.0.0.1" || mcp_client >= 0) {
				network_destroy(sock)
			} else {
				mcp_client = sock
				mcp_rx_len = 0
			}
		} else if (type == network_type_disconnect && sock == mcp_client) {
			mcp_client = -1
			mcp_rx_len = 0
		}
		return true
	}

	if (id_ == mcp_client && type == network_type_data) {
		mcp_bridge_receive(async_load[? "buffer"], async_load[? "size"])
		return true
	}
	return false
}


/// @function mcp_bridge_receive(buf, size)
/// @description Appends raw bytes and runs every complete line. TCP may split or merge messages.
function mcp_bridge_receive(buf, size) {
	var scan = mcp_rx_len
	if (mcp_rx_len + size + 1 > MCP_MAX_LINE_BYTES) {
		mcp_rx_len = 0
		mcp_bridge_send({ id: -1, ok: false, error: "message too large" })
		return 0
	}
	if (mcp_rx_len + size + 1 > buffer_get_size(mcp_rx)) buffer_resize(mcp_rx, (mcp_rx_len + size + 1) * 2)
	buffer_copy(buf, 0, size, mcp_rx, mcp_rx_len)
	mcp_rx_len += size

	var line_start = 0
	for (var i = scan; i < mcp_rx_len; i++) {
		if (buffer_peek(mcp_rx, i, buffer_u8) == 10) {
			buffer_poke(mcp_rx, i, buffer_u8, 0)
			buffer_seek(mcp_rx, buffer_seek_start, line_start)
			mcp_bridge_handle(buffer_read(mcp_rx, buffer_string))
			line_start = i + 1
		}
	}

	// Keep the unfinished line for the next packet
	if (line_start > 0) {
		var rest = mcp_rx_len - line_start
		if (rest > 0) {
			var tmp = buffer_create(rest, buffer_fixed, 1)
			buffer_copy(mcp_rx, line_start, rest, tmp, 0)
			buffer_copy(tmp, 0, rest, mcp_rx, 0)
			buffer_delete(tmp)
		}
		mcp_rx_len = rest
	}
	return 1
}


/// @function mcp_bridge_handle(line)
/// @description Runs one request. Every request gets exactly one reply, even when it fails.
function mcp_bridge_handle(line) {
	var req_id = -1
	var reply
	try {
		var req = json_parse(line)
		if (!is_struct(req)) throw "request must be a JSON object"
		if (variable_struct_exists(req, "id")) req_id = req.id
		reply = mcp_bridge_command(req)
		reply.ok = true
	} catch (e) {
		reply = { ok: false, error: is_struct(e) ? string(e.message) : string(e) }
	}
	reply.id = req_id
	mcp_bridge_send(reply)
}


/// @function mcp_bridge_send(reply)
function mcp_bridge_send(reply) {
	if (mcp_client < 0) return 0
	var text = json_stringify(reply) + "\n"
	var n = string_byte_length(text)
	var b = buffer_create(n, buffer_fixed, 1)
	buffer_write(b, buffer_text, text)
	network_send_raw(mcp_client, b, n)
	buffer_delete(b)
	return 1
}


/// @function mcp_int(struct, name, default, lo, hi)
/// @description Reads a whole number field. Pass undefined as default to make it required.
function mcp_int(s, name, def, lo, hi) {
	if (!variable_struct_exists(s, name)) {
		if (is_undefined(def)) throw "missing field: " + name
		return def
	}
	var v = variable_struct_get(s, name)
	if (!is_numeric(v) || v != floor(v) || v < lo || v > hi) {
		throw name + " must be a whole number from " + string(lo) + " to " + string(hi)
	}
	return v
}


/// @function mcp_ensure_layer(layer)
/// @description Same setup the editor does for new rows. Must run before add_block: it clears rowamount.
function mcp_ensure_layer(layer) {
	for (var b = endb2; b <= layer; b++) {
		layername[b] = ""
		layerlock[b] = 0
		layervol[b] = 100
		layerstereo[b] = 100
		rowamount[b] = 0
	}
	if (layer >= endb2) endb2 = layer + 1
}


/// @function mcp_note_exists(tick, layer)
function mcp_note_exists(a, b) {
	return (a < arraylength && b < arrayheight && song_exists[a, b])
}


/// @function mcp_add_note(tick, layer, ins, key, vel, pan, pit, length)
/// @description Like add_block_manual, without its dialogs and per-note sound.
function mcp_add_note(a, b, ins, key, vel, pan, pit, len) {
	mcp_ensure_layer(b)
	if (!add_block(a, b, ins, key, vel, pan, pit)) return 0
	song_added[a, b] = current_time
	changed = 1
	work_add += 1
	history_set(h_addblock, a, b, ins, key, vel, pan, pit)
	if (len > 0) {
		note_dur_set(a, b, len)
		history_set(h_changedur, a, b, len, 0)
	}
	return 1
}


/// @function mcp_cmd_add_notes(req)
/// @description Validates the whole batch before applying any of it.
function mcp_cmd_add_notes(req) {
	if (!variable_struct_exists(req, "notes") || !is_array(req.notes)) throw "missing field: notes"
	var replace = variable_struct_exists(req, "replace") && req.replace
	var count = array_length(req.notes)
	var seen = ds_map_create()
	var parsed = array_create(count)
	try {
		for (var i = 0; i < count; i++) {
			var n = req.notes[i]
			if (!is_struct(n)) throw "not an object"
			var a = mcp_int(n, "tick", undefined, 0, 31999)
			var b = mcp_int(n, "layer", undefined, 0, 2000)
			var cell = string(a) + ":" + string(b)
			if (ds_map_exists(seen, cell)) throw "two notes in the same cell"
			seen[? cell] = 1
			if (!replace && mcp_note_exists(a, b)) throw "cell is occupied (pass replace to overwrite)"
			parsed[i] = [
				a, b,
				instrument_list[| mcp_int(n, "instrument", 0, 0, ds_list_size(instrument_list) - 1)],
				mcp_int(n, "key", undefined, 0, 87),
				mcp_int(n, "velocity", 100, 0, 100),
				mcp_int(n, "panning", 100, 0, 200),
				mcp_int(n, "pitch", 0, -1200, 1200),
				mcp_int(n, "length", 0, 0, DUR_MAX)
			]
		}
	} catch (e) {
		ds_map_destroy(seen)
		throw "note " + string(i) + ": " + (is_struct(e) ? string(e.message) : string(e))
	}
	ds_map_destroy(seen)

	for (var j = 0; j < count; j++) {
		var p = parsed[j]
		if (replace && mcp_note_exists(p[0], p[1])) remove_block_manual(p[0], p[1])
		mcp_add_note(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7])
	}
	return { added: count, total_notes: totalblocks }
}


/// @function mcp_bool(struct, name)
function mcp_bool(s, name) {
	return variable_struct_exists(s, name) && variable_struct_get(s, name) == true
}


/// @function mcp_path(struct, name)
/// @description Reads an absolute path. Relative ones would resolve against the app's working folder.
function mcp_path(s, name) {
	var p = variable_struct_exists(s, name) ? variable_struct_get(s, name) : ""
	if (!is_string(p) || string_char_at(p, 2) != ":") throw name + " must be an absolute path"
	if (string_lower(filename_ext(p)) != ".nbs") throw name + " must end in .nbs"
	return p
}


/// @function mcp_guard_unsaved(req)
/// @description Clears the unsaved flag only on explicit discard, so confirm() never opens its dialog.
function mcp_guard_unsaved(req) {
	if (changed && !mcp_bool(req, "discard")) throw "unsaved_changes: save first, or ask the user and pass discard"
	changed = 0
}


/// @function mcp_cmd_open_song(req)
/// @description Reports what open_song_nbs would ask in a dialog. A too-new file leaves the current song untouched.
function mcp_cmd_open_song(req) {
	var path = mcp_path(req, "path")
	if (!file_exists_lib(path)) throw "file not found: " + path
	var b = buffer_import(path)
	var size = buffer_get_size(b)
	var ver = 0
	if (size >= 3 && buffer_peek(b, 0, buffer_u8) == 0 && buffer_peek(b, 1, buffer_u8) == 0) ver = buffer_peek(b, 2, buffer_u8)
	buffer_delete(b)
	if (size < 3) throw "not a valid song: " + path
	if (ver > nbs_version) throw "song was saved by a newer Note Block Studio (version " + string(ver) + ")"
	mcp_guard_unsaved(req)

	var warnings = []
	if (ver < nbs_version) warnings[array_length(warnings)] = "older file format (version " + string(ver) + "); saving upgrades it"
	var old_warning = show_oldwarning
	show_oldwarning = 0
	global.mcp_silent = true
	global.mcp_missing_sounds = ""
	try {
		load_song(path)
	} catch (e) {
		// load_song already cleared the song, and the guard above covered any unsaved work
		changed = 0
		window = 0
		throw "could not read " + path + ": " + (is_struct(e) ? string(e.message) : string(e))
	} finally {
		show_oldwarning = old_warning
		global.mcp_silent = false
	}
	window = 0
	if (global.mcp_missing_sounds != "") warnings[array_length(warnings)] = "missing custom sounds: " + global.mcp_missing_sounds
	return { filename: filename, notes: totalblocks, warnings: warnings }
}


/// @function mcp_cmd_save_song(req)
function mcp_cmd_save_song(req) {
	var path = filename
	if (variable_struct_exists(req, "path")) {
		path = mcp_path(req, "path")
	} else if (string_lower(filename_ext(filename)) != ".nbs") {
		throw "song has no .nbs file yet: give a path"
	}
	if (file_exists_lib(path) && string_lower(path) != string_lower(filename) && !mcp_bool(req, "overwrite")) {
		throw "file_exists: pass overwrite to replace " + path
	}
	// save_song opens a file dialog when the current filename isn't .nbs, even with a path given
	var previous = filename
	filename = path
	if (!save_song(path)) {
		filename = previous
		throw "could not save " + path
	}
	return { filename: filename }
}


/// @function mcp_cmd_update_notes(req)
/// @description Validates the whole batch first. Fields left out keep their current value.
function mcp_cmd_update_notes(req) {
	if (!variable_struct_exists(req, "notes") || !is_array(req.notes)) throw "missing field: notes"
	var count = array_length(req.notes)
	var parsed = array_create(count)
	try {
		for (var i = 0; i < count; i++) {
			var n = req.notes[i]
			if (!is_struct(n)) throw "not an object"
			var a = mcp_int(n, "tick", undefined, 0, 31999)
			var b = mcp_int(n, "layer", undefined, 0, 2000)
			if (!mcp_note_exists(a, b)) throw "no note in this cell"
			parsed[i] = [
				a, b,
				instrument_list[| mcp_int(n, "instrument", ds_list_find_index(instrument_list, song_ins[a, b]), 0, ds_list_size(instrument_list) - 1)],
				mcp_int(n, "key", song_key[a, b], 0, 87),
				mcp_int(n, "velocity", song_vel[a, b], 0, 100),
				mcp_int(n, "panning", song_pan[a, b], 0, 200),
				mcp_int(n, "pitch", song_pit[a, b], -1200, 1200),
				mcp_int(n, "length", note_dur_get(a, b), 0, DUR_MAX)
			]
		}
	} catch (e) {
		throw "note " + string(i) + ": " + (is_struct(e) ? string(e.message) : string(e))
	}
	for (var j = 0; j < count; j++) {
		var p = parsed[j]
		mcp_change_note(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7])
	}
	return { updated: count }
}


/// @function mcp_change_note(tick, layer, ins, key, vel, pan, pit, length)
/// @description Like change_block_manual, without a sound per note. Also keeps instrument counts right.
function mcp_change_note(a, b, ins, key, vel, pan, pit, len) {
	var pins = song_ins[a, b], pkey = song_key[a, b], pvel = song_vel[a, b], ppan = song_pan[a, b], ppit = song_pit[a, b]
	if (ins != pins || key != pkey || vel != pvel || pan != ppan || pit != ppit) {
		song_ins[a, b] = ins
		song_key[a, b] = key
		song_vel[a, b] = vel
		song_pan[a, b] = pan
		song_pit[a, b] = pit
		pins.num_blocks--
		ins.num_blocks++
		if (!pins.user && ins.user) block_custom += 1
		if (pins.user && !ins.user) block_custom -= 1
		if (pkey >= 33 && pkey <= 57 && (key < 33 || key > 57)) block_outside += 1
		if (key >= 33 && key <= 57 && (pkey < 33 || pkey > 57)) block_outside -= 1
		if (ppit == 0 && pit != 0) block_pitched += 1
		if (ppit != 0 && pit == 0) block_pitched -= 1
		history_set(h_changeblock, a, b, ins, key, vel, pan, pit, pins, pkey, pvel, ppan, ppit)
		changed = 1
	}
	var plen = note_dur_get(a, b)
	if (len != plen) {
		note_dur_set(a, b, len)
		history_set(h_changedur, a, b, len, plen)
		changed = 1
	}
}


/// @function mcp_cmd_remove_notes(req)
function mcp_cmd_remove_notes(req) {
	if (!variable_struct_exists(req, "cells") || !is_array(req.cells)) throw "missing field: cells"
	var count = array_length(req.cells)
	var parsed = array_create(count)
	try {
		for (var i = 0; i < count; i++) {
			var n = req.cells[i]
			if (!is_struct(n)) throw "not an object"
			var a = mcp_int(n, "tick", undefined, 0, 31999)
			var b = mcp_int(n, "layer", undefined, 0, 2000)
			if (!mcp_note_exists(a, b)) throw "no note in this cell"
			parsed[i] = [a, b]
		}
	} catch (e) {
		throw "cell " + string(i) + ": " + (is_struct(e) ? string(e.message) : string(e))
	}
	for (var j = 0; j < count; j++) {
		var c = parsed[j]
		remove_block_manual(c[0], c[1])
	}
	return { removed: count, total_notes: totalblocks }
}


/// @function mcp_cmd_get_notes(req)
/// @description Returns whole ticks, up to about 2000 notes per reply. next_tick says where to continue.
function mcp_cmd_get_notes(req) {
	var start = mcp_int(req, "start", 0, 0, 31999)
	var last = min(mcp_int(req, "end", 31999, 0, 31999), enda, arraylength - 1)
	var out = [], n = 0
	var reply = {}
	for (var a = start; a <= last; a++) {
		if (colamount[a] == 0) continue
		if (n >= 2000) {
			reply.next_tick = a
			break
		}
		for (var b = colfirst[a]; b <= collast[a]; b++) {
			if (song_exists[a, b]) {
				out[n++] = {
					tick: a, layer: b,
					instrument: ds_list_find_index(instrument_list, song_ins[a, b]),
					key: song_key[a, b], velocity: song_vel[a, b], panning: song_pan[a, b],
					pitch: song_pit[a, b], length: note_dur_get(a, b)
				}
			}
		}
	}
	reply.notes = out
	return reply
}


/// @function mcp_cmd_add_instrument(req)
/// @description Adds a custom instrument from a sound already in the Sounds folder, without the file picker.
/// Reuses an existing custom instrument with the same file and key.
function mcp_cmd_add_instrument(req) {
	var file = variable_struct_exists(req, "file") ? req.file : ""
	if (!is_string(file) || file == "") throw "missing field: file"
	file = string_replace_all(file, "\\", "/")
	if (string_pos("..", file) > 0 || string_char_at(file, 2) == ":") throw "file must be a path inside the Sounds folder"
	var ext = string_lower(filename_ext(file))
	if (ext != ".ogg" && ext != ".wav" && ext != ".mp3") throw "file must be .ogg, .wav or .mp3"
	if (!file_exists_lib(sounds_directory + string_replace_all(file, "/", "\\"))) throw "not in the Sounds folder (" + sounds_directory + "): " + file
	var key = mcp_int(req, "key", 45, 0, 87)
	var press = !variable_struct_exists(req, "press") || req.press == true
	var name = variable_struct_exists(req, "name") ? string(req.name) : filename_change_ext(filename_name(file), "")
	for (var i = first_custom_index; i < ds_list_size(instrument_list); i++) {
		var e = instrument_list[| i]
		if (e.user && string_lower(e.filename) == string_lower(file) && e.key == key) return { index: i, name: e.name, existing: true }
	}
	if (user_instruments >= 240) throw "too many custom instruments (240)"
	var ins = new_instrument(name, file, true, press, key)
	var ok = false
	global.mcp_silent = true
	with (ins) ok = instrument_load()
	global.mcp_silent = false
	if (!ok) {
		user_instruments--
		instance_destroy(ins)
		throw "could not load " + file
	}
	ds_list_add(instrument_list, ins)
	if (save_version < 5 && user_instruments > 18) save_version = nbs_version
	changed = 1
	return { index: ds_list_size(instrument_list) - 1, name: name, existing: false }
}


/// @function mcp_bridge_command(req)
/// @description Runs a command and returns its reply struct. Errors are thrown as strings.
function mcp_bridge_command(req) {
	if (!variable_struct_exists(req, "cmd")) throw "missing field: cmd"
	if (isplayer) throw "Note Block Studio is in player mode: editing is off"
	if (req.cmd != "status" && window == w_greeting) window = 0 // So the user can see the work

	switch (req.cmd) {
		case "status":
			return {
				filename: filename,
				unsaved: changed ? true : false,
				tempo: tempo,
				length: enda,
				layers: endb2,
				notes: totalblocks,
				playing: playing != 0,
				marker: floor(marker_pos),
				time_signature: timesignature,
				sounds_folder: sounds_directory
			}
		case "instruments":
			var list = []
			for (var i = 0; i < ds_list_size(instrument_list); i++) {
				var ins = instrument_list[| i]
				list[i] = { index: i, name: ins.name, file: ins.filename, key: ins.key, custom: ins.user ? true : false, loaded: ins.loaded ? true : false }
			}
			return { instruments: list }
		case "add_instrument":
			return mcp_cmd_add_instrument(req)
		case "add_notes":
			return mcp_cmd_add_notes(req)
		case "update_notes":
			return mcp_cmd_update_notes(req)
		case "remove_notes":
			return mcp_cmd_remove_notes(req)
		case "get_notes":
			return mcp_cmd_get_notes(req)
		case "new_song":
			mcp_guard_unsaved(req)
			new_song()
			changed = 0 // reset() leaves it set through selection_delete
			window = 0
			return {}
		case "open_song":
			return mcp_cmd_open_song(req)
		case "save_song":
			return mcp_cmd_save_song(req)
		case "snapshot":
			// Same as the audio export: leaves filename and the unsaved flag alone
			if (!save_song(temp_file, true)) throw "could not write snapshot"
			return { path: temp_file, sounds_folder: sounds_directory }
		case "set_song":
			var ts = mcp_int(req, "time_signature", timesignature, 2, 8)
			if (variable_struct_exists(req, "tempo")) {
				if (!is_numeric(req.tempo) || req.tempo < 0.25 || req.tempo > 60) throw "tempo must be from 0.25 to 60"
				tempo = req.tempo
			}
			timesignature = ts
			if (variable_struct_exists(req, "name")) song_name = string(req.name)
			if (variable_struct_exists(req, "author")) song_author = string(req.author)
			changed = 1
			return { tempo: tempo, name: song_name, author: song_author, time_signature: timesignature }
		case "set_layer":
			var l = mcp_int(req, "layer", undefined, 0, 2000)
			var vol = mcp_int(req, "volume", 0, 0, 100) // Range check; applied below only if given
			var ste = mcp_int(req, "stereo", 0, 0, 200)
			mcp_ensure_layer(l)
			if (variable_struct_exists(req, "name")) {
				layername[l] = string(req.name)
				text_str[l + 400] = layername[l]
			}
			if (variable_struct_exists(req, "volume")) layervol[l] = vol
			if (variable_struct_exists(req, "stereo")) layerstereo[l] = ste
			changed = 1
			return { layer: l, name: layername[l], volume: layervol[l], stereo: layerstereo[l] }
		case "play":
			var from = mcp_int(req, "from_tick", -1, -1, 31999)
			if (from >= 0) {
				marker_pos = from
				marker_prevpos = from
			}
			if (playing != 1) {
				playing = 0
				toggle_playing(floor((rw - 278) / 32)) // Visible columns; totalcols is local to control_draw
			}
			return { marker: floor(marker_pos) }
		case "stop":
			if (playing != 0) toggle_playing(floor((rw - 278) / 32)) // Visible columns; totalcols is local to control_draw
			return { marker: floor(marker_pos) }
		case "seek":
			marker_pos = mcp_int(req, "tick", undefined, 0, 31999)
			marker_prevpos = marker_pos
			return { marker: floor(marker_pos) }
		case "undo":
			var steps = mcp_int(req, "count", 1, 1, 10000)
			var before = historypos
			playing = 0
			repeat (steps) action_undo()
			return { undone: historypos - before, total_notes: totalblocks }
	}
	throw "unknown command: " + string(req.cmd)
}
