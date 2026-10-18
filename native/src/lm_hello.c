/* Hello Catastrophe: cửa sổ + chữ tiếng Việt + đọc tay cầm, thoát bằng B.
 *   lm-hello [--screenshot out.ppm] [--keys A,DOWN,...]   (hai cờ sau chỉ để kiểm thử offscreen) */
#include "catastrophe.h"
#include "lm_common.h"
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    lm_harness hn = {0};
    for (int i = 1; i < argc; i++) {
        if (!lm_harness_arg(&hn, argc, argv, &i)) {
            fprintf(stderr, "dung: %s [--screenshot out.ppm] [--keys UP,DOWN,A,B,X,Y]\n", argv[0]);
            return 2;
        }
    }

    cat_config cfg = { .window_title = "LoongMate native — Hello", .log_path = cat_resolve_log_path("loongmate-hello") };
    if (cat_init(&cfg) != CAT_OK) { fprintf(stderr, "cat_init that bai\n"); return 1; }
    lm_ensure_vietnamese_font();

    char last[128] = "(chưa có phím nào)";
    bool running = true;
    cat_status_bar_opts sb = { .show_clock = CAT_CLOCK_SHOW, .use_24h = true, .show_battery = true, .show_wifi = true };
    cat_footer_item footer[] = {
        { .button = CAT_BTN_B, .label = "THOÁT" },
        { .button = CAT_BTN_A, .label = "THỬ PHÍM", .is_confirm = true },
    };

    while (running) {
        cat_input_event ev;
        while (lm_harness_poll(&hn, &ev)) {
            if (!ev.pressed) continue;
            snprintf(last, sizeof last, "Vừa nhấn: %s%s", cat_button_name(ev.button), ev.repeated ? " (giữ)" : "");
            if (ev.button == CAT_BTN_B) running = false;
        }

        cat_clear_screen();
        cat_draw_screen_title("Xin chào, Catastrophe!", &sb);
        cat_draw_status_bar(&sb);
        SDL_Rect c = cat_get_content_rect(true, true, true);
        c.x += cat_device_scale(5); c.w -= cat_device_scale(10);
        cat_theme *th = cat_get_theme();
        TTF_Font *big = cat_get_font(CAT_FONT_LARGE), *small = cat_get_font(CAT_FONT_SMALL);
        int y = c.y + cat_device_scale(6);
        y += cat_draw_text_wrapped(big, "Tiếng Việt: Đặng Thị Bạch Dương, ắ ằ ẳ ẵ ặ ề ễ ộ ỡ ự ỹ", c.x, y, c.w, th->text, CAT_ALIGN_LEFT);
        y += cat_device_scale(10);
        y += cat_draw_text_wrapped(small, "Giao diện native: Catastrophe (UMRK) biên dịch chéo ARM64.", c.x, y, c.w, th->hint, CAT_ALIGN_LEFT);
        y += cat_device_scale(10);
        cat_draw_text_wrapped(big, last, c.x, y, c.w, th->highlight, CAT_ALIGN_LEFT);
        cat_draw_footer(footer, 2);

        cat_request_frame();
        if (lm_harness_frame_end(&hn, 0)) break;
        cat_present();
    }
    cat_quit();
    return hn.timed_out ? 1 : 0;
}
