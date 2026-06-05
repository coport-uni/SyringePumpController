#include "ui.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "sdkconfig.h"

#include "lvgl.h"

#include "pump_task.h"

static const char *TAG = "ui";

/* Custom Montserrat-14 font with ASCII (0x20-0x7E) + micro sign (0xB5)
 * + a few arrows / em-dash / ellipsis. Generated via the LVGL Font
 * Converter from Montserrat-Regular.ttf and checked in as
 * ``firmware/main/montserrat_14_ext.c``. Needed because the built-in
 * lv_font_montserrat_14 lacks U+00B5 (µ) and friends, while the custom
 * subset can't economically cover every glyph we might ever want.
 *
 * ``s_font_main`` below is a mutable runtime wrapper around the
 * generated const font with ``fallback = &lv_font_montserrat_14`` set:
 * every label / theme reference uses ``&s_font_main``, so any glyph
 * missing from our subset (e.g. FontAwesome icons like LV_SYMBOL_DOWN
 * for the dropdown caret) is rendered cleanly from LVGL's built-in
 * Montserrat-14 instead of becoming a missing-glyph box. */
LV_FONT_DECLARE(montserrat_14_ext);
LV_FONT_DECLARE(lv_font_montserrat_14);
static lv_font_t s_font_main;

#define STATUS_ROW_COUNT 7
/* Fallback used only before the first diagnose response lands; the
 * slider gets re-ranged from app_status_t.syringe_uL as soon as
 * /v1/diagnose succeeds. Matches the existing bench syringe so a
 * boot-time aspirate (theoretically possible before diagnose, though
 * the FSM gates motion behind READY) doesn't over-request. */
#define DEFAULT_SYRINGE_UL 125

/* Banner / tabview */
static lv_obj_t *s_banner;
static lv_obj_t *s_tabview;

/* Valve tab */
static lv_obj_t *s_valve_buttons[4];
static int s_active_valve_port = -1;
/* Rotor diagram in the middle of the four port buttons. Two coloured
 * chords show the currently connected port pairs:
 *   BLUE   = Path 1 (syringe-active, C-connected)
 *   ORANGE = Path 2 (passive bypass)
 * The MCC-4 manual (§2.2.3) defines only two physical states; the
 * pump firmware exposes them as 4 positions where each button maps
 * Port × Path. Mapping used by ``valve_diagram_render``:
 *   pos 1 (Port 1 → Path 1) = state1  ⇒ BLUE C↔1, ORANGE 2↔3
 *   pos 2 (Port 3 → Path 1) = state2  ⇒ BLUE C↔3, ORANGE 1↔2
 *   pos 3 (Port 1 → Path 2) = state2  (Port 1 sits on bypass)
 *   pos 4 (Port 3 → Path 2) = state1  (Port 3 sits on bypass)
 */
static lv_obj_t *s_valve_diagram;
static lv_obj_t *s_valve_line_c_p1;  /* BLUE state1 (C↔Port 1) */
static lv_obj_t *s_valve_line_c_p3;  /* BLUE state2 (C↔Port 3) */
static lv_obj_t *s_valve_line_p2_p3; /* ORANGE state1 (Port 2↔Port 3) */
static lv_obj_t *s_valve_line_p1_p2; /* ORANGE state2 (Port 1↔Port 2) */

/* Move tab */
static lv_obj_t *s_move_slider;
static lv_obj_t *s_move_target_label;
static lv_obj_t *s_move_valve_label;   /* "Connected: Port N" */
static lv_obj_t *s_move_actuate_btn;   /* unified "C Actuation" button */
static lv_obj_t *s_move_history_label; /* "Last: ..." */

/* Status tab — reconnect button to retry diagnose after a server outage. */
static lv_obj_t *s_reconnect_btn;

/* Prime tab — operator-configurable cycles / source / sink (replaces
 * the previously hard-coded {cycles=1, source=3, sink=1}). Source and
 * sink are surfaced as Port 1 / Port 3 dropdowns since MCC-4 has only
 * those two physically meaningful states (CLAUDE.md §"Hardware"). */
static lv_obj_t *s_prime_btn;
static lv_obj_t *s_prime_btn_label;
static lv_obj_t *s_prime_spinner;
static lv_obj_t *s_prime_label;
static lv_obj_t *s_prime_cycles_label;
static lv_obj_t *s_prime_source_dd;
static lv_obj_t *s_prime_sink_dd;
static int s_prime_cycles = 1;
static int s_prime_source = 1; /* default flipped from 3 → 1 so that the
                                  default direction is "fill port 1's
                                  tube via port 3 as waste" (operator's
                                  typical workflow). */
static int s_prime_sink = 3;

/* Status tab */
static lv_obj_t *s_status_table;
static const char *STATUS_ROW_NAMES[STATUS_ROW_COUNT] = {
    "Supply V",   "Valve port", "Plunger steps", "Pump busy",
    "Pump error", "Firmware",   "WiFi",
};

/* Active error modal (NULL when none). */
static lv_obj_t *s_modal;

/* ----------------------------------------------------------------- Helpers */
static void enqueue_or_toast(const pump_cmd_t *cmd, const char *busy_msg)
{
    if (!pump_task_enqueue(cmd)) {
        ui_show_toast(busy_msg);
    }
}

/* ----------------------------------------------------------------- Valve */
static void valve_btn_event_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) {
        return;
    }
    int port = (int)(intptr_t)lv_event_get_user_data(e);
    /* ccw=false → pump sends ``I<port>R`` (clockwise rotation per
     * SY01BE manual §4.5.1). ccw=true would be O<port>R (CCW). Back to
     * CW per bench preference. */
    pump_cmd_t cmd = {
        .kind = PUMP_CMD_VALVE,
        .payload.valve = {.port = port, .ccw = false},
    };
    enqueue_or_toast(&cmd, "Pump busy");
}

/* Rotor diagram is an 84x84 lv_obj with border 2 → 80x80 content area.
 * Child coordinates (chord endpoints, label offsets) live in that
 * content-area space; the center is at (40, 40). Each port endpoint
 * sits 28 px from center along an axis (~70 % of the 40-px radius),
 * leaving a few pixels between chord tip and port label. */
static const lv_point_precise_t VALVE_PTS_C_P1[] = {{40, 68}, {12, 40}};
static const lv_point_precise_t VALVE_PTS_C_P3[] = {{40, 68}, {68, 40}};
static const lv_point_precise_t VALVE_PTS_P2_P3[] = {{40, 12}, {68, 40}};
static const lv_point_precise_t VALVE_PTS_P1_P2[] = {{12, 40}, {40, 12}};

static void valve_diagram_render(int position)
{
    if (s_valve_line_c_p1 == NULL) {
        return;
    }
    /* Unknown valve position (status hasn't landed, or pump reports "?")
     * → hide all chords. Rotor body + port labels stay visible. */
    if (position < 1 || position > 4) {
        lv_obj_add_flag(s_valve_line_c_p1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_valve_line_c_p3, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_valve_line_p2_p3, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_valve_line_p1_p2, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    bool state1 = (position == 1 || position == 4);
    if (state1) {
        lv_obj_clear_flag(s_valve_line_c_p1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_valve_line_p2_p3, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_valve_line_c_p3, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_valve_line_p1_p2, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_clear_flag(s_valve_line_c_p3, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_valve_line_p1_p2, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_valve_line_c_p1, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_valve_line_p2_p3, LV_OBJ_FLAG_HIDDEN);
    }
}

static void valve_row_setup(lv_obj_t *row)
{
    lv_obj_set_size(row, LV_PCT(100), 48);
    lv_obj_set_style_pad_all(row, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(row, 0, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_layout(row, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);
    lv_obj_set_scroll_dir(row, LV_DIR_NONE);
}

static void valve_add_button(lv_obj_t *row, const char *text, int port_index)
{
    lv_obj_t *btn = lv_btn_create(row);
    lv_obj_set_size(btn, 144, 44);
    lv_obj_t *label = lv_label_create(btn);
    lv_label_set_text(label, text);
    lv_obj_center(label);
    lv_obj_add_event_cb(btn, valve_btn_event_cb, LV_EVENT_CLICKED,
                        (void *)(intptr_t)(port_index + 1));
    s_valve_buttons[port_index] = btn;
}

static void valve_add_chord(lv_obj_t **out, lv_obj_t *parent,
                            const lv_point_precise_t pts[2],
                            lv_palette_t palette)
{
    *out = lv_line_create(parent);
    lv_line_set_points(*out, pts, 2);
    lv_obj_set_style_line_color(*out, lv_palette_main(palette), LV_PART_MAIN);
    lv_obj_set_style_line_width(*out, 5, LV_PART_MAIN);
    lv_obj_set_style_line_rounded(*out, true, LV_PART_MAIN);
}

static void create_valve_tab(lv_obj_t *parent)
{
    /* Per-port button labels. MCC-4 has 2 mechanical states (C-1+2-3 vs
     * C-3+1-2); the pump firmware exposes them as 4 positions. Each
     * label maps Port (1 or 3) × Path (1 = syringe-active, 2 = passive
     * bypass). */
    static const char *VALVE_BTN_LABELS[4] = {
        "Port 1 to Path 1", /* position 1 */
        "Port 3 to Path 1", /* position 2 */
        "Port 1 to Path 2", /* position 3 */
        "Port 3 to Path 2", /* position 4 */
    };

    lv_obj_set_layout(parent, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(parent, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(parent, 4, LV_PART_MAIN);
    lv_obj_set_style_pad_gap(parent, 4, LV_PART_MAIN);
    lv_obj_set_scroll_dir(parent, LV_DIR_NONE);

    /* Top row: positions 1 and 2 (both Path 1 — syringe-active). */
    lv_obj_t *top_row = lv_obj_create(parent);
    valve_row_setup(top_row);
    valve_add_button(top_row, VALVE_BTN_LABELS[0], 0);
    valve_add_button(top_row, VALVE_BTN_LABELS[1], 1);

    /* Middle: rotor diagram (84x84) centered. */
    lv_obj_t *diag_wrap = lv_obj_create(parent);
    lv_obj_set_size(diag_wrap, LV_PCT(100), 88);
    lv_obj_set_style_pad_all(diag_wrap, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(diag_wrap, 0, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(diag_wrap, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_scroll_dir(diag_wrap, LV_DIR_NONE);

    s_valve_diagram = lv_obj_create(diag_wrap);
    lv_obj_set_size(s_valve_diagram, 84, 84);
    lv_obj_align(s_valve_diagram, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_radius(s_valve_diagram, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_border_width(s_valve_diagram, 2, LV_PART_MAIN);
    lv_obj_set_style_border_color(s_valve_diagram,
                                  lv_palette_main(LV_PALETTE_GREY),
                                  LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_valve_diagram, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_pad_all(s_valve_diagram, 0, LV_PART_MAIN);
    lv_obj_set_scroll_dir(s_valve_diagram, LV_DIR_NONE);

    /* Port labels at compass positions just inside the rotor body. */
    static const struct {
        const char *txt;
        int x;
        int y;
    } LABELS[] = {
        {"1", -32, 0}, /* left */
        {"2", 0, -32}, /* top */
        {"3", 32, 0},  /* right */
        {"C", 0, 32},  /* bottom */
    };
    for (int i = 0; i < 4; ++i) {
        lv_obj_t *l = lv_label_create(s_valve_diagram);
        lv_label_set_text(l, LABELS[i].txt);
        lv_obj_align(l, LV_ALIGN_CENTER, LABELS[i].x, LABELS[i].y);
        lv_obj_set_style_text_color(l,
                                    lv_palette_darken(LV_PALETTE_GREY, 2),
                                    LV_PART_MAIN);
    }

    /* All four chords created upfront; valve_diagram_render toggles
     * visibility per state. BLUE = syringe-active path 1, ORANGE =
     * passive bypass path 2. */
    valve_add_chord(&s_valve_line_c_p1, s_valve_diagram, VALVE_PTS_C_P1,
                    LV_PALETTE_BLUE);
    valve_add_chord(&s_valve_line_c_p3, s_valve_diagram, VALVE_PTS_C_P3,
                    LV_PALETTE_BLUE);
    valve_add_chord(&s_valve_line_p2_p3, s_valve_diagram, VALVE_PTS_P2_P3,
                    LV_PALETTE_ORANGE);
    valve_add_chord(&s_valve_line_p1_p2, s_valve_diagram, VALVE_PTS_P1_P2,
                    LV_PALETTE_ORANGE);
    valve_diagram_render(-1); /* hide all until /v1/status lands */

    /* Bottom row: positions 3 and 4 (both Path 2 — passive). */
    lv_obj_t *bot_row = lv_obj_create(parent);
    valve_row_setup(bot_row);
    valve_add_button(bot_row, VALVE_BTN_LABELS[2], 2);
    valve_add_button(bot_row, VALVE_BTN_LABELS[3], 3);
}

static void valve_highlight_port(int port)
{
    s_active_valve_port = port;
    for (int i = 0; i < 4; ++i) {
        if (s_valve_buttons[i] == NULL) {
            continue;
        }
        lv_color_t bg = (i + 1 == port) ? lv_palette_main(LV_PALETTE_BLUE)
                                        : lv_palette_main(LV_PALETTE_GREY);
        lv_obj_set_style_bg_color(s_valve_buttons[i], bg, LV_PART_MAIN);
    }
    valve_diagram_render(port);
}

/* ----------------------------------------------------------------- Move */
static int slider_target_uL(void)
{
    if (s_move_slider == NULL) {
        return 0;
    }
    return (int)lv_slider_get_value(s_move_slider);
}

static void move_slider_event_cb(lv_event_t *e)
{
    (void)e;
    if (s_move_target_label != NULL) {
        lv_label_set_text_fmt(s_move_target_label, "Target: %d µL",
                              slider_target_uL());
    }
}

static void move_history_set(const char *text)
{
    if (s_move_history_label != NULL) {
        lv_label_set_text(s_move_history_label, text);
    }
}

/* "C Actuation" — single button that moves the plunger to the slider's
 * absolute target volume. Aspirate / Dispense were two buttons sending
 * different command kinds (PUMP_CMD_ASPIRATE vs PUMP_CMD_DISPENSE) but
 * both ultimately resolved to the same wire frame (A<n>R), so a single
 * button is more honest about what the call actually does. */
static void actuate_btn_event_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) {
        return;
    }
    int vol = slider_target_uL();
    pump_cmd_t cmd = {
        .kind = PUMP_CMD_ASPIRATE, /* aspirate/dispense share a wire frame */
        .payload.volume = {.target_uL = (float)vol},
    };
    enqueue_or_toast(&cmd, "Pump busy");
    char buf[80];
    if (s_active_valve_port > 0) {
        snprintf(buf, sizeof(buf), "Last: Moved to %d µL via Port %d", vol,
                 s_active_valve_port);
    } else {
        snprintf(buf, sizeof(buf), "Last: Moved to %d µL", vol);
    }
    move_history_set(buf);
}

static void create_move_tab(lv_obj_t *parent)
{
    lv_obj_set_layout(parent, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(parent, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(parent, 8, LV_PART_MAIN);
    lv_obj_set_style_pad_gap(parent, 6, LV_PART_MAIN);

    s_move_target_label = lv_label_create(parent);
    /* Per-label font override — the default theme sets text_font on
     * labels directly, which prevents inheritance from the screen-level
     * style we set in ui_create. Apply explicitly so µ renders. */
    lv_obj_set_style_text_font(s_move_target_label, &s_font_main,
                               LV_PART_MAIN);
    lv_label_set_text(s_move_target_label, "Target: 0 µL");

    s_move_slider = lv_slider_create(parent);
    lv_slider_set_range(s_move_slider, 0, DEFAULT_SYRINGE_UL);
    lv_slider_set_value(s_move_slider, 0, LV_ANIM_OFF);
    /* 92% width + 10 px left margin keeps the slider knob fully on-screen
     * at value 0 (the knob extends past the bar by half its size). */
    lv_obj_set_width(s_move_slider, LV_PCT(92));
    lv_obj_set_style_margin_left(s_move_slider, 10, LV_PART_MAIN);
    lv_obj_add_event_cb(s_move_slider, move_slider_event_cb,
                        LV_EVENT_VALUE_CHANGED, NULL);

    s_move_valve_label = lv_label_create(parent);
    lv_label_set_text(s_move_valve_label, "Connected: Port --");

    /* Single centered "C Actuation" button — positioned where the
     * Aspirate/Dispense pair used to be, sized to span their combined
     * width so the button is the visual center of the tab. */
    lv_obj_t *btn_row = lv_obj_create(parent);
    lv_obj_set_style_pad_all(btn_row, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(btn_row, 0, LV_PART_MAIN);
    lv_obj_set_size(btn_row, LV_PCT(100), 56);
    lv_obj_set_layout(btn_row, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(btn_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(btn_row, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    s_move_actuate_btn = lv_btn_create(btn_row);
    lv_obj_set_size(s_move_actuate_btn, 200, 48);
    lv_obj_t *al = lv_label_create(s_move_actuate_btn);
    lv_label_set_text(al, "C Actuation");
    lv_obj_center(al);
    lv_obj_add_event_cb(s_move_actuate_btn, actuate_btn_event_cb,
                        LV_EVENT_CLICKED, NULL);

    /* History line at the bottom — updated on every enqueue. */
    s_move_history_label = lv_label_create(parent);
    /* Same per-label font override as the Target label — µ otherwise renders
     * as a missing glyph because the theme's default Montserrat-14 has no
     * U+00B5. */
    lv_obj_set_style_text_font(s_move_history_label, &s_font_main,
                               LV_PART_MAIN);
    lv_label_set_text(s_move_history_label, "Last: --");
    lv_obj_set_style_text_color(s_move_history_label,
                                lv_palette_main(LV_PALETTE_GREY), LV_PART_MAIN);
}

/* ----------------------------------------------------------------- Prime
 *
 * LVGL 9.x dropped the all-in-one ``lv_msgbox_create(parent, title,
 * body, btns, close)`` signature and ``lv_msgbox_get_active_btn_text``;
 * we now build the modal piecewise and attach a click handler to each
 * footer button. The handler identifies which button was pressed by
 * reading its child label. ``user_data`` carries the modal pointer so
 * we can close it from the handler.
 */
/* Port option list used by both source and sink dropdowns. Index 0 is
 * the literal "Port 1" entry → physical port 1; index 1 → port 3.
 * Order matters and must match ``dropdown_index_to_port`` below. */
static const char PRIME_PORT_OPTIONS[] = "Port 1\nPort 3";

static int dropdown_index_to_port(uint16_t idx)
{
    return (idx == 0) ? 1 : 3;
}

static uint16_t port_to_dropdown_index(int port)
{
    return (port == 1) ? 0 : 1;
}

static void prime_button_label_refresh(void)
{
    if (s_prime_btn_label == NULL) {
        return;
    }
    lv_label_set_text_fmt(s_prime_btn_label, "PRIME\n%d cycle%s,  Port %d → Port %d",
                          s_prime_cycles, (s_prime_cycles == 1) ? "" : "s",
                          s_prime_source, s_prime_sink);
}

static void prime_cycles_refresh(void)
{
    if (s_prime_cycles_label != NULL) {
        lv_label_set_text_fmt(s_prime_cycles_label, "%d", s_prime_cycles);
    }
    prime_button_label_refresh();
}

/* Wrap-around: at 1 the - button jumps to 20, at 20 the + button jumps
 * to 1. Matches the operator's expectation that the counter is a small
 * cyclic range rather than a clamp. */
static void prime_cycles_dec_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) {
        return;
    }
    s_prime_cycles = (s_prime_cycles > 1) ? s_prime_cycles - 1 : 20;
    prime_cycles_refresh();
}

static void prime_cycles_inc_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) {
        return;
    }
    s_prime_cycles = (s_prime_cycles < 20) ? s_prime_cycles + 1 : 1;
    prime_cycles_refresh();
}

/* Guard to break the source↔sink auto-link recursion: changing one
 * dropdown updates the other, which would re-enter this handler. */
static bool s_prime_linking = false;

static void prime_source_changed_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_VALUE_CHANGED || s_prime_linking) {
        return;
    }
    s_prime_source = dropdown_index_to_port(lv_dropdown_get_selected(
        (lv_obj_t *)lv_event_get_target(e)));
    /* Auto-pair: only Port 1 and Port 3 are physically usable on MCC-4,
     * so source != sink is enforced by always flipping the other side. */
    int new_sink = (s_prime_source == 1) ? 3 : 1;
    if (new_sink != s_prime_sink && s_prime_sink_dd != NULL) {
        s_prime_linking = true;
        s_prime_sink = new_sink;
        lv_dropdown_set_selected(s_prime_sink_dd,
                                 port_to_dropdown_index(s_prime_sink));
        s_prime_linking = false;
    }
    prime_button_label_refresh();
}

static void prime_sink_changed_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_VALUE_CHANGED || s_prime_linking) {
        return;
    }
    s_prime_sink = dropdown_index_to_port(lv_dropdown_get_selected(
        (lv_obj_t *)lv_event_get_target(e)));
    int new_source = (s_prime_sink == 1) ? 3 : 1;
    if (new_source != s_prime_source && s_prime_source_dd != NULL) {
        s_prime_linking = true;
        s_prime_source = new_source;
        lv_dropdown_set_selected(s_prime_source_dd,
                                 port_to_dropdown_index(s_prime_source));
        s_prime_linking = false;
    }
    prime_button_label_refresh();
}

static void prime_button_cb(lv_event_t *e)
{
    lv_obj_t *btn = lv_event_get_target(e);
    lv_obj_t *modal = (lv_obj_t *)lv_event_get_user_data(e);
    lv_obj_t *label = lv_obj_get_child(btn, 0);
    const char *txt = label ? lv_label_get_text(label) : NULL;
    if (txt != NULL && strcmp(txt, "Start") == 0) {
        pump_cmd_t cmd = {
            .kind = PUMP_CMD_PRIME,
            .payload.prime = {.cycles = s_prime_cycles,
                              .source_port = s_prime_source,
                              .sink_port = s_prime_sink},
        };
        enqueue_or_toast(&cmd, "Pump busy");
    }
    lv_msgbox_close(modal);
}

static void prime_btn_event_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) {
        return;
    }
    /* Don't fire confirm if source == sink — same-port priming has no
     * physical meaning (it'd just dispense back into the source). */
    /* No same-port guard needed — the source/sink change handlers
     * auto-flip the other side, so they're never equal in steady
     * state. */
    lv_obj_t *modal = lv_msgbox_create(NULL);
    lv_obj_set_style_text_font(modal, &s_font_main, LV_PART_MAIN);
    lv_msgbox_add_title(modal, "Prime line");
    char body[160];
    snprintf(body, sizeof(body),
             "Run %d prime cycle%s:\n"
             "Port %d (source) → Port %d (sink)\n"
             "Each cycle = full-stroke aspirate + dispense.",
             s_prime_cycles, (s_prime_cycles == 1) ? "" : "s",
             s_prime_source, s_prime_sink);
    lv_msgbox_add_text(modal, body);
    lv_obj_t *start = lv_msgbox_add_footer_button(modal, "Start");
    lv_obj_t *cancel = lv_msgbox_add_footer_button(modal, "Cancel");
    lv_obj_add_event_cb(start, prime_button_cb, LV_EVENT_CLICKED, modal);
    lv_obj_add_event_cb(cancel, prime_button_cb, LV_EVENT_CLICKED, modal);
    lv_obj_center(modal);
}

static void create_prime_tab(lv_obj_t *parent)
{
    lv_obj_set_layout(parent, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(parent, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(parent, 6, LV_PART_MAIN);
    lv_obj_set_style_pad_gap(parent, 4, LV_PART_MAIN);
    lv_obj_set_scroll_dir(parent, LV_DIR_NONE);

    /* Row 1: Cycles [ - ] N [ + ] */
    lv_obj_t *row_cycles = lv_obj_create(parent);
    lv_obj_set_size(row_cycles, LV_PCT(100), 36);
    lv_obj_set_style_pad_all(row_cycles, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(row_cycles, 0, LV_PART_MAIN);
    lv_obj_set_layout(row_cycles, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(row_cycles, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row_cycles, LV_FLEX_ALIGN_START,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row_cycles, 6, LV_PART_MAIN);

    lv_obj_t *cycles_caption = lv_label_create(row_cycles);
    lv_label_set_text(cycles_caption, "Cycle (1~20):");
    lv_obj_set_width(cycles_caption, 110);

    lv_obj_t *dec_btn = lv_btn_create(row_cycles);
    lv_obj_set_size(dec_btn, 36, 32);
    lv_obj_t *dec_lbl = lv_label_create(dec_btn);
    lv_label_set_text(dec_lbl, "-");
    lv_obj_center(dec_lbl);
    lv_obj_add_event_cb(dec_btn, prime_cycles_dec_cb, LV_EVENT_CLICKED, NULL);

    s_prime_cycles_label = lv_label_create(row_cycles);
    lv_obj_set_width(s_prime_cycles_label, 32);
    lv_obj_set_style_text_align(s_prime_cycles_label, LV_TEXT_ALIGN_CENTER,
                                LV_PART_MAIN);
    lv_label_set_text(s_prime_cycles_label, "1");

    lv_obj_t *inc_btn = lv_btn_create(row_cycles);
    lv_obj_set_size(inc_btn, 36, 32);
    lv_obj_t *inc_lbl = lv_label_create(inc_btn);
    lv_label_set_text(inc_lbl, "+");
    lv_obj_center(inc_lbl);
    lv_obj_add_event_cb(inc_btn, prime_cycles_inc_cb, LV_EVENT_CLICKED, NULL);

    /* Row 2: Source dropdown */
    lv_obj_t *row_src = lv_obj_create(parent);
    lv_obj_set_size(row_src, LV_PCT(100), 36);
    lv_obj_set_style_pad_all(row_src, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(row_src, 0, LV_PART_MAIN);
    lv_obj_set_layout(row_src, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(row_src, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row_src, LV_FLEX_ALIGN_START,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row_src, 6, LV_PART_MAIN);

    lv_obj_t *src_caption = lv_label_create(row_src);
    lv_label_set_text(src_caption, "Source:");
    lv_obj_set_width(src_caption, 64);

    s_prime_source_dd = lv_dropdown_create(row_src);
    lv_dropdown_set_options(s_prime_source_dd, PRIME_PORT_OPTIONS);
    lv_dropdown_set_selected(s_prime_source_dd,
                             port_to_dropdown_index(s_prime_source));
    /* LV_SYMBOL_DOWN is a FontAwesome glyph (U+F078) carried by LVGL's
     * built-in lv_font_montserrat_14. Our custom subset doesn't include
     * it, but the font-fallback chain set in ``ui_create`` redirects
     * missing glyphs to that built-in font, so the caret renders. */
    lv_dropdown_set_symbol(s_prime_source_dd, LV_SYMBOL_DOWN);
    lv_obj_set_width(s_prime_source_dd, 120);
    lv_obj_add_event_cb(s_prime_source_dd, prime_source_changed_cb,
                        LV_EVENT_VALUE_CHANGED, NULL);

    /* Row 3: Sink dropdown */
    lv_obj_t *row_sink = lv_obj_create(parent);
    lv_obj_set_size(row_sink, LV_PCT(100), 36);
    lv_obj_set_style_pad_all(row_sink, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(row_sink, 0, LV_PART_MAIN);
    lv_obj_set_layout(row_sink, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(row_sink, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row_sink, LV_FLEX_ALIGN_START,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row_sink, 6, LV_PART_MAIN);

    lv_obj_t *sink_caption = lv_label_create(row_sink);
    lv_label_set_text(sink_caption, "Sink:");
    lv_obj_set_width(sink_caption, 64);

    s_prime_sink_dd = lv_dropdown_create(row_sink);
    lv_dropdown_set_options(s_prime_sink_dd, PRIME_PORT_OPTIONS);
    lv_dropdown_set_selected(s_prime_sink_dd,
                             port_to_dropdown_index(s_prime_sink));
    lv_dropdown_set_symbol(s_prime_sink_dd, LV_SYMBOL_DOWN);
    lv_obj_set_width(s_prime_sink_dd, 120);
    lv_obj_add_event_cb(s_prime_sink_dd, prime_sink_changed_cb,
                        LV_EVENT_VALUE_CHANGED, NULL);

    /* Row 4: PRIME button with dynamic label. */
    s_prime_btn = lv_btn_create(parent);
    lv_obj_set_size(s_prime_btn, LV_PCT(100), 64);
    s_prime_btn_label = lv_label_create(s_prime_btn);
    lv_obj_set_style_text_font(s_prime_btn_label, &s_font_main,
                               LV_PART_MAIN);
    lv_obj_set_style_text_align(s_prime_btn_label, LV_TEXT_ALIGN_CENTER,
                                LV_PART_MAIN);
    lv_obj_center(s_prime_btn_label);
    lv_obj_add_event_cb(s_prime_btn, prime_btn_event_cb, LV_EVENT_CLICKED,
                        NULL);
    prime_button_label_refresh();

    /* Spinner overlay during BUSY. */
    s_prime_spinner = lv_spinner_create(parent);
    lv_spinner_set_anim_params(s_prime_spinner, 1000, 60);
    lv_obj_set_size(s_prime_spinner, 60, 60);
    lv_obj_add_flag(s_prime_spinner, LV_OBJ_FLAG_HIDDEN);
    lv_obj_align(s_prime_spinner, LV_ALIGN_CENTER, 0, 0);

    s_prime_label = lv_label_create(parent);
    lv_label_set_text(s_prime_label, "");
}

static void prime_set_running(bool running)
{
    if (s_prime_btn == NULL) {
        return;
    }
    if (running) {
        lv_obj_add_flag(s_prime_btn, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_prime_spinner, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(s_prime_label, "Priming (~30 s) ...");
    } else {
        lv_obj_clear_flag(s_prime_btn, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_prime_spinner, LV_OBJ_FLAG_HIDDEN);
    }
}

/* ----------------------------------------------------------------- Status */
static void reconnect_btn_event_cb(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) {
        return;
    }
    pump_cmd_t cmd = {.kind = PUMP_CMD_DIAGNOSE};
    enqueue_or_toast(&cmd, "Pump busy");
}

static void create_status_tab(lv_obj_t *parent)
{
    /* Stack the status table over a Reconnect button in a flex column.
     * Horizontal scroll on the tab is disabled — there's nothing to
     * scroll left/right on a 2-column status table that already fits. */
    lv_obj_set_layout(parent, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(parent, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(parent, 4, LV_PART_MAIN);
    lv_obj_set_style_pad_gap(parent, 4, LV_PART_MAIN);
    lv_obj_set_scroll_dir(parent, LV_DIR_NONE);

    s_status_table = lv_table_create(parent);
    lv_obj_set_width(s_status_table, LV_PCT(100));
    lv_obj_set_flex_grow(s_status_table, 1); /* fill remaining height */
    /* Allow vertical scrolling only (table can grow past viewport),
     * never horizontal. */
    lv_obj_set_scroll_dir(s_status_table, LV_DIR_VER);
    lv_table_set_row_cnt(s_status_table, STATUS_ROW_COUNT);
    lv_table_set_col_cnt(s_status_table, 2);
    lv_table_set_col_width(s_status_table, 0, 130);
    lv_table_set_col_width(s_status_table, 1, 175);
    for (int i = 0; i < STATUS_ROW_COUNT; ++i) {
        lv_table_set_cell_value(s_status_table, (uint16_t)i, 0,
                                STATUS_ROW_NAMES[i]);
        lv_table_set_cell_value(s_status_table, (uint16_t)i, 1, "--");
    }

    s_reconnect_btn = lv_btn_create(parent);
    lv_obj_set_size(s_reconnect_btn, LV_PCT(100), 32);
    lv_obj_t *rl = lv_label_create(s_reconnect_btn);
    lv_label_set_text(rl, "Reconnect");
    lv_obj_center(rl);
    lv_obj_add_event_cb(s_reconnect_btn, reconnect_btn_event_cb,
                        LV_EVENT_CLICKED, NULL);
}

/* ----------------------------------------------------------------- Motion
 * enable */
static void set_disabled(lv_obj_t *obj, bool disabled)
{
    if (obj == NULL) {
        return;
    }
    if (disabled) {
        lv_obj_add_state(obj, LV_STATE_DISABLED);
    } else {
        lv_obj_clear_state(obj, LV_STATE_DISABLED);
    }
}

static void apply_motion_enabled(bool enabled)
{
    for (int i = 0; i < 4; ++i) {
        set_disabled(s_valve_buttons[i], !enabled);
    }
    set_disabled(s_move_slider, !enabled);
    set_disabled(s_move_actuate_btn, !enabled);
    set_disabled(s_prime_btn, !enabled);
}

/* ----------------------------------------------------------------- ui_create
 */
void ui_create(void)
{
    /* Build the runtime font wrapper: copy the const generated struct
     * into the mutable ``s_font_main`` and graft LVGL's built-in
     * Montserrat-14 (which carries FontAwesome icons) on as the
     * fallback chain. Every subsequent reference uses ``&s_font_main``
     * — see the file-scope comment for the rationale. */
    s_font_main = montserrat_14_ext;
    s_font_main.fallback = &lv_font_montserrat_14;

    /* Reinitialise the LVGL default theme so the Unicode-rich Montserrat
     * font is applied to every widget — labels inside buttons, tables,
     * msgboxes, toasts, all of them. The BSP set up the theme during
     * bsp_display_start with the built-in Montserrat-14, which has no
     * µ / em-dash / → glyphs. Replacing the theme once here makes the
     * per-label font set calls below redundant; we keep them as
     * belt-and-suspenders. */
    lv_display_t *disp = lv_display_get_default();
    if (disp != NULL) {
        lv_theme_t *theme = lv_theme_default_init(
            disp, lv_palette_main(LV_PALETTE_BLUE),
            lv_palette_main(LV_PALETTE_RED), false, &s_font_main);
        lv_display_set_theme(disp, theme);
    }
    lv_obj_set_style_text_font(lv_scr_act(), &s_font_main, LV_PART_MAIN);

    s_banner = lv_label_create(lv_scr_act());
    /* Banner shows error banners that contain — (em-dash) and → from
     * main.c (e.g. "diagnose failed — check server", "Needs Initialize
     * (press →)"); explicit font set for the same reason as move labels. */
    lv_obj_set_style_text_font(s_banner, &s_font_main, LV_PART_MAIN);
    lv_obj_set_size(s_banner, LV_PCT(100), 24);
    lv_obj_align(s_banner, LV_ALIGN_TOP_MID, 0, 0);
    lv_label_set_text(s_banner, "Boot");
    lv_obj_set_style_bg_color(s_banner, lv_palette_main(LV_PALETTE_BLUE_GREY),
                              LV_PART_MAIN);
    lv_obj_set_style_bg_opa(s_banner, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_text_color(s_banner, lv_color_white(), LV_PART_MAIN);

    s_tabview = lv_tabview_create(lv_scr_act());
    lv_tabview_set_tab_bar_size(s_tabview, 32);
    lv_obj_set_size(s_tabview, LV_PCT(100), 216);
    lv_obj_align(s_tabview, LV_ALIGN_BOTTOM_MID, 0, 0);

    /* Disable horizontal swipe on the tab content so tab switching only
     * happens via the top tab bar (touch-on-tab-name). */
    lv_obj_remove_flag(lv_tabview_get_content(s_tabview),
                       LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *valve_tab = lv_tabview_add_tab(s_tabview, "Valve");
    lv_obj_t *move_tab = lv_tabview_add_tab(s_tabview, "Move");
    lv_obj_t *prime_tab = lv_tabview_add_tab(s_tabview, "Prime");
    lv_obj_t *status_tab = lv_tabview_add_tab(s_tabview, "Status");

    create_valve_tab(valve_tab);
    create_move_tab(move_tab);
    create_prime_tab(prime_tab);
    create_status_tab(status_tab);

    lv_tabview_set_act(s_tabview, 3, LV_ANIM_OFF);
    apply_motion_enabled(false);

    ESP_LOGI(TAG, "UI created (full 4-tab motion UI)");
}

/* -----------------------------------------------------------------
 * ui_apply_state */
void ui_apply_state(app_state_t state, const char *banner_text,
                    bool requires_reinit)
{
    if (s_banner == NULL) {
        return;
    }
    lv_color_t color;
    bool motion_ok = false;
    switch (state) {
    case APP_STATE_BOOT:
    case APP_STATE_WIFI_CONNECTING:
    case APP_STATE_DIAGNOSING:
        color = lv_palette_main(LV_PALETTE_BLUE_GREY);
        break;
    case APP_STATE_NEEDS_INIT:
        color = lv_palette_main(LV_PALETTE_AMBER);
        break;
    case APP_STATE_READY:
        color = lv_palette_main(LV_PALETTE_GREEN);
        motion_ok = !requires_reinit;
        break;
    case APP_STATE_BUSY:
        color = lv_palette_main(LV_PALETTE_INDIGO);
        break;
    case APP_STATE_WIFI_LOST:
    case APP_STATE_ERROR_RECOVERABLE:
        color = lv_palette_main(LV_PALETTE_RED);
        break;
    case APP_STATE_ERROR_FATAL:
        color = lv_palette_darken(LV_PALETTE_RED, 2);
        break;
    default:
        color = lv_palette_main(LV_PALETTE_GREY);
        break;
    }
    lv_obj_set_style_bg_color(s_banner, color, LV_PART_MAIN);
    if (banner_text != NULL) {
        lv_label_set_text(s_banner, banner_text);
    }

    apply_motion_enabled(motion_ok);
    prime_set_running(state == APP_STATE_BUSY);
}

/* -----------------------------------------------------------------
 * ui_apply_status */
void ui_apply_status(const app_status_t *status)
{
    if (s_status_table == NULL || status == NULL) {
        return;
    }
    char buf[64];

    snprintf(buf, sizeof(buf), "%.1f V", status->supply_volts);
    lv_table_set_cell_value(s_status_table, 0, 1, buf);

    lv_table_set_cell_value(s_status_table, 1, 1, status->valve);

    snprintf(buf, sizeof(buf), "%d", status->plunger_steps);
    lv_table_set_cell_value(s_status_table, 2, 1, buf);

    lv_table_set_cell_value(s_status_table, 3, 1,
                            status->pump_busy ? "yes" : "no");

    snprintf(buf, sizeof(buf), "%s (%d)", status->pump_error_name,
             status->pump_error_code);
    lv_table_set_cell_value(s_status_table, 4, 1, buf);

    lv_table_set_cell_value(s_status_table, 5, 1, status->software_version);

    lv_table_set_cell_value(s_status_table, 6, 1,
                            status->wifi_connected ? "connected" : "lost");
}

void ui_apply_motion_snapshot(const app_status_t *status)
{
    if (status == NULL) {
        return;
    }
    /* Re-range the volume slider against the server-reported syringe
     * size. syringe_uL == 0 means diagnose hasn't landed yet; leave the
     * default range in that case. We also re-set the value so it
     * doesn't fall off the end of a now-smaller range. */
    if (s_move_slider != NULL && status->syringe_uL > 0.0f) {
        int max_uL = (int)status->syringe_uL;
        int cur = (int)lv_slider_get_value(s_move_slider);
        if (cur > max_uL) {
            cur = max_uL;
        }
        lv_slider_set_range(s_move_slider, 0, max_uL);
        lv_slider_set_value(s_move_slider, cur, LV_ANIM_OFF);
        if (s_move_target_label != NULL) {
            lv_label_set_text_fmt(s_move_target_label, "Target: %d µL", cur);
        }
    }
    /* Valve highlight from the cached server-reported port. */
    if (status->valve[0] >= '1' && status->valve[0] <= '4' &&
        status->valve[1] == '\0') {
        valve_highlight_port(status->valve[0] - '0');
    } else {
        valve_highlight_port(-1);
    }
    /* Move tab — "Connected: Port N to Path M" mirrors the Valve tab
     * button labels so the operator sees the same wording in both
     * tabs. Mapping per ``VALVE_BTN_LABELS`` in create_valve_tab. */
    if (s_move_valve_label != NULL) {
        static const char *VALVE_CONNECTED_LABELS[4] = {
            "Connected: Port 1 to Path 1", /* valve = "1" */
            "Connected: Port 3 to Path 1", /* valve = "2" */
            "Connected: Port 1 to Path 2", /* valve = "3" */
            "Connected: Port 3 to Path 2", /* valve = "4" */
        };
        if (status->valve[0] >= '1' && status->valve[0] <= '4' &&
            status->valve[1] == '\0') {
            int idx = status->valve[0] - '1';
            lv_label_set_text(s_move_valve_label,
                              VALVE_CONNECTED_LABELS[idx]);
        } else {
            lv_label_set_text(s_move_valve_label, "Connected: --");
        }
    }
}

/* ----------------------------------------------------------------- Error modal
 *
 * Same LVGL 9.x adaptation as the Prime modal: build piecewise, attach
 * a click handler per footer button, identify the button by its child
 * label text.
 */
static void modal_event_cb(lv_event_t *e)
{
    lv_obj_t *btn = lv_event_get_target(e);
    lv_obj_t *modal = (lv_obj_t *)lv_event_get_user_data(e);
    lv_obj_t *label = lv_obj_get_child(btn, 0);
    const char *txt = label ? lv_label_get_text(label) : NULL;
    if (txt == NULL) {
        lv_msgbox_close(modal);
        s_modal = NULL;
        return;
    }
    if (strcmp(txt, "Retry") == 0) {
        pump_cmd_t cmd = {.kind = PUMP_CMD_RETRY_LAST};
        enqueue_or_toast(&cmd, "Pump busy");
    } else if (strcmp(txt, "Re-initialize") == 0) {
        pump_cmd_t cmd = {
            .kind = PUMP_CMD_INITIALIZE,
            .payload.init = {.force = 2, .ccw = false},
        };
        enqueue_or_toast(&cmd, "Pump busy");
    }
    /* "Dismiss" and any other label: just close. */
    lv_msgbox_close(modal);
    s_modal = NULL;
}

void ui_show_error_modal(const pump_error_t *err)
{
    if (err == NULL || err->error_name[0] == '\0') {
        return;
    }
    if (s_modal != NULL) {
        /* Keep the existing modal — later errors get logged but the
         * first one is what the operator should address. */
        ESP_LOGW(TAG, "modal already open, dropping: %s", err->error_name);
        return;
    }

    char body[280];
    snprintf(body, sizeof(body), "%s (code %d, HTTP %d)\n%s\n%s%s%s",
             err->error_name, err->code, err->http_status, err->message,
             err->command[0] != '\0' ? "cmd " : "",
             err->command[0] != '\0' ? err->command : "",
             err->command[0] != '\0' ? "\n" : "");

    s_modal = lv_msgbox_create(NULL);
    /* err->message can contain — (em-dash) — set font on the modal so
     * the body text label inherits properly. */
    lv_obj_set_style_text_font(s_modal, &s_font_main, LV_PART_MAIN);
    lv_msgbox_add_title(s_modal, err->klass == PUMP_ERROR_FATAL
                                     ? "Fatal pump error"
                                     : "Pump error");
    lv_msgbox_add_text(s_modal, body);

    if (err->klass == PUMP_ERROR_FATAL) {
        lv_obj_t *reinit =
            lv_msgbox_add_footer_button(s_modal, "Re-initialize");
        lv_obj_add_event_cb(reinit, modal_event_cb, LV_EVENT_CLICKED, s_modal);
    } else {
        lv_obj_t *retry = lv_msgbox_add_footer_button(s_modal, "Retry");
        lv_obj_t *dismiss = lv_msgbox_add_footer_button(s_modal, "Dismiss");
        lv_obj_add_event_cb(retry, modal_event_cb, LV_EVENT_CLICKED, s_modal);
        lv_obj_add_event_cb(dismiss, modal_event_cb, LV_EVENT_CLICKED, s_modal);
    }
    lv_obj_center(s_modal);
}

/* ----------------------------------------------------------------- Toast */
static void toast_timer_cb(lv_timer_t *t)
{
    lv_obj_t *toast = (lv_obj_t *)lv_timer_get_user_data(t);
    if (toast != NULL) {
        lv_obj_del(toast);
    }
    lv_timer_del(t);
}

void ui_show_toast(const char *msg)
{
    if (msg == NULL) {
        return;
    }
    lv_obj_t *toast = lv_label_create(lv_scr_act());
    lv_label_set_text(toast, msg);
    lv_obj_set_style_bg_color(toast, lv_color_black(), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(toast, LV_OPA_70, LV_PART_MAIN);
    lv_obj_set_style_text_color(toast, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_pad_all(toast, 6, LV_PART_MAIN);
    lv_obj_set_style_radius(toast, 6, LV_PART_MAIN);
    lv_obj_align(toast, LV_ALIGN_BOTTOM_MID, 0, -32);
    lv_obj_move_foreground(toast);
    lv_timer_t *t = lv_timer_create(toast_timer_cb, 2000, toast);
    lv_timer_set_repeat_count(t, 1);
}

void ui_jump_to_status_tab(void)
{
    if (s_tabview != NULL) {
        lv_tabview_set_act(s_tabview, 3, LV_ANIM_OFF);
    }
}
