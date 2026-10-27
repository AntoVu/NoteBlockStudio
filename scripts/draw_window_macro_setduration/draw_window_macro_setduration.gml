function draw_window_macro_setduration() {
	// draw_window_macro_setduration()
	// "Set note length" window: sets how many ticks the selected notes are held for.
	var x1, y1, a, b, str;
	if (selected == 0) {
		window = 0
		return 0
	}
	windowanim = 1
	if (theme = 3) draw_set_alpha(windowalpha)
	curs = cr_default
	text_exists[0] = 0
	x1 = floor(rw / 2 - 80)
	y1 = floor(rh / 2 - 80) + windowoffset
	draw_window(x1, y1, x1 + 140, y1 + 130)
	draw_theme_font(font_main_bold)
	if (language != 1) draw_text_dynamic(x1 + 8, y1 + 8, "Set note length")
	else draw_text_dynamic(x1 + 8, y1 + 8, "设置音符时长")
	draw_theme_font(font_main)
	if (theme = 0) {
	    draw_set_color(c_white)
	    draw_rectangle(x1 + 6, y1 + 26, x1 + 134, y1 + 92, 0)
	    draw_set_color(make_color_rgb(137, 140, 149))
	    draw_rectangle(x1 + 6, y1 + 26, x1 + 134, y1 + 92, 1)
	}
	if (language != 1) draw_areaheader(x1 + 10, y1 + 53, 120, 35, "Length (ticks)")
	else draw_areaheader(x1 + 10, y1 + 53, 120, 35, "时长（刻）")
	setdur = median(0, draw_dragvalue(21, x1 + 55, y1 + 65, setdur, 0.25), DUR_MAX)

	draw_theme_color()
	if (language != 1) {if (draw_checkbox(x1 + 15, y1 + 80, setdur_mode, "Until next note", "Hold each note until the next note\nin the same layer (legato).\nNotes with nothing after them\nuse the length above.", 0, 1)) setdur_mode = !setdur_mode}
	else {if (draw_checkbox(x1 + 15, y1 + 80, setdur_mode, "持续到下一音符", "将每个音符延长到同一层的下一个音符（连奏）。\n后面没有音符的使用上面的时长。", 0, 1)) setdur_mode = !setdur_mode}
	if (draw_button2(x1 + 10, y1 + 98, 60, condstr(language != 1, "OK", "确定"))) {
		windowalpha = 0
		windowclose = 0
		windowopen = 0
		window = 0
		selection_setdur(setdur, setdur_mode)
		if(!keyboard_check(vk_alt)) selection_place(false)
	}
	if (draw_button2(x1 + 70, y1 + 98, 60, condstr(language != 1, "Cancel", "取消")) && (windowopen = 1 || theme != 3)) {windowclose = 1}
	if (display_mouse_get_x() - window_get_x() >= 0 && display_mouse_get_y() - window_get_y() >= 0 && display_mouse_get_x() - window_get_x() < 0 + window_width && display_mouse_get_y() - window_get_y() < 0 + window_height) {
		window_set_cursor(curs)
		if (array_length(text_mouseover) = 0) window_set_cursor(cr_default)
	}
}


/// @function selection_setdur(ticks, until_next)
/// @description Sets the length of every selected note. If until_next is true, each note is held
/// until the next note in the same layer (searching the selection and the song), falling back to `ticks`.
function selection_setdur(ticks, until_next) {
	var a, b, n, d, str
	if (selected = 0) return 0
	str = selection_code
	ticks = median(0, floor(ticks), DUR_MAX)
	if (ticks = 1) ticks = 0 // 1 tick = a normal note
	for (a = 0; a < selection_l; a += 1) {
	    if (selection_colfirst[a] > -1) {
	        for (b = selection_colfirst[a]; b <= selection_collast[a]; b += 1) {
	            if (selection_exists[a, b]) {
					d = ticks
					if (until_next) {
						// Look for the next note in this layer, within 512 ticks
						for (n = 1; n <= 512; n++) {
							var sa = a + n
							var ga = selection_x + a + n
							var in_sel = (sa < selection_l && selection_colfirst[sa] > -1 && b >= selection_colfirst[sa] && b <= selection_collast[sa] && selection_exists[sa, b])
							var in_song = (ga < arraylength && selection_y + b < arrayheight && song_exists[ga, selection_y + b])
							if (in_sel || in_song) {
								d = n
								break
							}
							if (sa >= selection_l && ga > enda) break // Nothing left in the song
						}
						if (d = 1) d = 0
					}
					sel_dur_set(a, b, d)
	            }
	        }
	    }
	}
	selection_code_update()
	history_set(h_selectchange, selection_x, selection_y, selection_code, selection_x, selection_y, str)
	changed = 1
}
