function remove_emitters() {
	
	// Stop sustained (looping) notes once their duration is over, or when playback stops.
	// Notes previewed while stopped (s[8] false) just run out their time.
	for (var si = ds_list_size(sustained_sounds) - 1; si >= 0; si--) {
		var s = sustained_sounds[| si]
		var stop_now = !playing && (array_length(s) < 9 || s[8])
		if (stop_now || get_timer() >= s[1]) {
			if (!s[2] && !stop_now) {
				// Fade out over a short release, then stop
				audio_sound_gain(s[0], 0, 120)
				sustained_sounds[| si] = [s[0], get_timer() + 130000, true]
			} else {
				audio_stop_sound(s[0])
				ds_list_delete(sustained_sounds, si)
			}
		} else if (!s[2] && array_length(s) > 4 && s[4] != "") {
			// Stretched sound while held: vibrato for steady sounds, slow fade for the rest
			var t = (get_timer() - s[3]) / 1000000
			if (s[4] == "steady") {
				var vib = HELD_VIBRATO_DEPTH * min(1, t / HELD_VIBRATO_RAMP)
				// Full pitch on the instance: in this runtime an instance's pitch replaces the emitter's,
				// and changing the emitter's pitch mid-sound squares it
				audio_sound_pitch(s[0], s[7] * (1 + vib * sin(2 * pi * HELD_VIBRATO_RATE * t)))
			} else {
				audio_sound_gain(s[0], power(s[5], min(1, t / s[6])), 0)
			}
		}
	}
	
	if (os_type != os_macosx) {
	
	//Called from control_draw every frame
	//Removes emitters from memory to prevent memory leaking
	var pos, arr, emitterid, timetoremove

	for(pos = 0; pos < ds_list_size(emitters_to_remove); pos++){
		arr = ds_list_find_value(emitters_to_remove,pos)
	
		emitterid = ds_list_find_value(arr,0)
		timetoremove = ds_list_find_value(arr,1)
	
		if(get_timer() >= timetoremove){
			ds_list_delete(emitters_to_remove,pos)
			audio_emitter_free(emitterid)
			//log("freeing emitter with id " + string(emitterid))
			sounds--
		}
	}

	} else {
	
	//Called from control_draw every frame
	//Removes emitters from memory to prevent memory leaking
	var pos, arr, emitterid, timetoremove

	for(pos = 0; pos < ds_list_size(emitters_to_remove); pos++){
		arr = ds_list_find_value(emitters_to_remove,pos)
	
		emitterid = ds_list_find_value(arr,0)
		timetoremove = ds_list_find_value(arr,1)
	
		if(get_timer() >= timetoremove){
			ds_list_delete(emitters_to_remove,pos)
			//audio_emitter_free(emitterid)
			audio_stop_sound(emitterid)
			//log("freeing emitter with id " + string(emitterid))
			sounds--
		}
	}
	
	}

}

