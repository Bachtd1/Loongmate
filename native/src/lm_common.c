#define _GNU_SOURCE
#include "lm_common.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define VI_PROBE_GLYPH 0x1EC5u   /* "ễ" — chữ Việt có dấu chồng, hay thiếu trong font rút gọn */
#define SETTLE_FRAMES 4
#define SHOT_KEY CAT_BTN_COUNT
#define BURST_KEY (CAT_BTN_COUNT + 2)    /* hậu tố của "AA"/"XX"...: nhắc lại phím liền trước NGAY LẬP TỨC (thử chống bấm đúp) */
#define SHOTB_KEY (CAT_BTN_COUNT + 1)   /* "SHOTB": chụp khi tác vụ nền đang chạy >= 1,5 giây (để thấy "Đang ... (Ns)") */   /* "SHOT": chụp ảnh giữa chừng thành <base>.<n>.ppm */
#define GATE_KEY (CAT_BTN_COUNT + 3)    /* "GATE": chờ tới khi file <ảnh>.gate xuất hiện (d4_watch.py tạo SAU khi đổi sandbox) rồi mới bấm tiếp */
#define BUSYB_KEY (CAT_BTN_COUNT + 4)   /* "BUSYB": bấm B khi tác vụ nền đã chạy >= LM_SHOTB_MS (thử "B = tạm dừng" của tải file) */
#define HARNESS_TIMEOUT_MS 270000u

static int font_has_vietnamese(void) {
    TTF_Font *f = cat_get_font(CAT_FONT_LARGE);
    return f && TTF_GlyphIsProvided32(f, VI_PROBE_GLYPH) != 0;
}

const char *lm_ensure_vietnamese_font(void) {
    static char used[600];
    if (font_has_vietnamese()) {
        cat_log("lm: font cua theme co glyph tieng Viet");
        return "theme";
    }
    const char *launcher = getenv("UMRK_LAUNCHER_PATH");
    const char *sd = getenv("SDCARD_PATH");
    char cand[2][560]; int n = 0;
    if (launcher && *launcher)
        snprintf(cand[n++], sizeof cand[0], "%s/res/fonts/Nunito/Nunito-Bold.ttf", launcher);
    snprintf(cand[n++], sizeof cand[0], "%s/.system/leaf/platforms/mlp1/launcher/res/fonts/Nunito/Nunito-Bold.ttf",
             (sd && *sd) ? sd : "/mnt/sdcard");
    for (int i = 0; i < n; i++) {
        if (access(cand[i], R_OK) != 0) continue;
        if (cat_reload_fonts(cand[i]) == CAT_OK && font_has_vietnamese()) {
            snprintf(used, sizeof used, "%s", cand[i]);
            cat_log("lm: font theme thieu glyph tieng Viet, doi sang %s", used);
            return used;
        }
    }
    cat_log("lm: CANH BAO khong tim duoc font co glyph tieng Viet");
    return NULL;
}

int lm_write_ppm(const char *path) {
    SDL_Renderer *r = cat_get_renderer();
    int w = 0, h = 0;
    if (!r || SDL_GetRendererOutputSize(r, &w, &h) != 0 || w <= 0 || h <= 0) {
        fprintf(stderr, "lm_write_ppm: khong lay duoc kich thuoc renderer: %s\n", SDL_GetError());
        return -1;
    }
    unsigned char *buf = malloc((size_t)w * h * 3);
    if (!buf) return -1;
    if (SDL_RenderReadPixels(r, NULL, SDL_PIXELFORMAT_RGB24, buf, w * 3) != 0) {
        fprintf(stderr, "lm_write_ppm: SDL_RenderReadPixels loi: %s\n", SDL_GetError());
        free(buf);
        return -1;
    }
    FILE *fp = fopen(path, "wb");
    if (!fp) { perror(path); free(buf); return -1; }
    int ok = fprintf(fp, "P6\n%d %d\n255\n", w, h) > 0 && fwrite(buf, 3, (size_t)w * h, fp) == (size_t)w * h;
    ok = (fclose(fp) == 0) && ok;
    free(buf);
    if (!ok) { fprintf(stderr, "lm_write_ppm: ghi %s that bai\n", path); return -1; }
    fprintf(stderr, "lm: da ghi %s (%dx%d)\n", path, w, h);
    return 0;
}

static cat_button parse_key(const char *s) {
    static const struct { const char *n; cat_button b; } t[] = {
        {"UP", CAT_BTN_UP}, {"DOWN", CAT_BTN_DOWN}, {"LEFT", CAT_BTN_LEFT}, {"RIGHT", CAT_BTN_RIGHT},
        {"A", CAT_BTN_A}, {"B", CAT_BTN_B}, {"X", CAT_BTN_X}, {"Y", CAT_BTN_Y},
        {"L1", CAT_BTN_L1}, {"R1", CAT_BTN_R1}, {"SHOT", SHOT_KEY}, {"SHOTB", SHOTB_KEY}, {"BUSYB", BUSYB_KEY}, {"GATE", GATE_KEY},
    };
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++)
        if (strcmp(s, t[i].n) == 0) return t[i].b;
    return CAT_BTN_NONE;
}

int lm_harness_arg(lm_harness *h, int argc, char **argv, int *i) {
    if (strcmp(argv[*i], "--screenshot") == 0 && *i + 1 < argc) {
        h->shot_path = argv[++*i];
        return 1;
    }
    if (strcmp(argv[*i], "--keys") == 0 && *i + 1 < argc) {
        char *list = strdup(argv[++*i]);
        for (char *tok = strtok(list, ","); tok; tok = strtok(NULL, ",")) {
            size_t tl = strlen(tok);
            int burst = tl == 2 && tok[0] == tok[1] && strchr("ABXY", tok[0]);   /* "AA" = A rồi A ngay lập tức */
            if (burst) tok[1] = 0;
            cat_button b = parse_key(tok);
            if (b == CAT_BTN_NONE) { fprintf(stderr, "--keys: phim la '%s'\n", tok); exit(2); }
            if (h->nkeys >= 256) { fprintf(stderr, "--keys: toi da 256 phim\n"); exit(2); }
            h->keys[h->nkeys++] = b;
            if (burst && h->nkeys < 256) h->keys[h->nkeys++] = (cat_button)BURST_KEY;
        }
        free(list);
        return 1;
    }
    return 0;
}

int lm_harness_active(const lm_harness *h) { return h->shot_path != NULL || h->nkeys > 0; }

bool lm_harness_poll(lm_harness *h, cat_input_event *ev) {
    if (h->pending_b) {
        h->pending_b = 0;
        ev->button = CAT_BTN_B;
        ev->pressed = true;
        ev->repeated = false;
        return true;
    }
    if (h->kpos < h->nkeys && h->keys[h->kpos] == GATE_KEY) {
        char gate[640];
        snprintf(gate, sizeof gate, "%s.gate", h->shot_path ? h->shot_path : "gate");
        if (access(gate, F_OK) != 0) return cat_poll_input(ev);     /* chưa tới giờ: giữ nguyên chờ */
        h->kpos++;
        h->settle_frames = 0;
    }
    if (h->kpos > 0 && h->kpos < h->nkeys && h->keys[h->kpos] == BURST_KEY) {
        ev->button = h->keys[h->kpos - 1];     /* nhắc lại phím vừa bấm, không chờ khung hình nào */
        h->kpos++;
        ev->pressed = true;
        ev->repeated = false;
        return true;
    }
    if (h->kpos < h->nkeys && h->keys[h->kpos] != SHOT_KEY && h->keys[h->kpos] != SHOTB_KEY && h->keys[h->kpos] != BUSYB_KEY && h->settle_frames >= SETTLE_FRAMES) {
        ev->button = h->keys[h->kpos++];
        ev->pressed = true;
        ev->repeated = false;
        h->settle_frames = 0;
        return true;
    }
    return cat_poll_input(ev);
}

/* SHOTB chụp sau bao lâu tác vụ nền đã chạy (mặc định 1500 ms; LM_SHOTB_MS đổi được: thử tải file lớn qua qemu cần chờ lâu hơn) */
static unsigned shotb_ms(void) {
    const char *e = getenv("LM_SHOTB_MS");
    long v = e ? strtol(e, NULL, 10) : 0;
    return v > 0 ? (unsigned)v : 1500u;
}

int lm_harness_frame_end(lm_harness *h, int busy) {
    if (!lm_harness_active(h)) return 0;
    if (!h->start_ms) h->start_ms = SDL_GetTicks();
    if (SDL_GetTicks() - h->start_ms > HARNESS_TIMEOUT_MS) {
        fprintf(stderr, "lm: harness het gio (%u ms) — job con chay hoac UI khong on dinh\n", HARNESS_TIMEOUT_MS);
        h->timed_out = 1;
        return 1;
    }
    if (busy) {
        if (!h->busy_since) h->busy_since = SDL_GetTicks();
        if (h->kpos < h->nkeys && h->keys[h->kpos] == SHOTB_KEY && SDL_GetTicks() - h->busy_since >= shotb_ms()) {
            if (h->shot_path) {
                char path[600], base[560];
                snprintf(base, sizeof base, "%s", h->shot_path);
                char *dot = strrchr(base, '.');
                if (dot) *dot = 0;
                snprintf(path, sizeof path, "%s.%d%s", base, ++h->shot_n, dot ? h->shot_path + (dot - base) : "");
                if (lm_write_ppm(path) != 0) h->timed_out = 1;
            }
            h->kpos++;
        }
        if (h->kpos < h->nkeys && h->keys[h->kpos] == BUSYB_KEY && SDL_GetTicks() - h->busy_since >= shotb_ms()) { h->pending_b = 1; h->kpos++; }
        h->settle_frames = 0;
        return 0;
    }
    h->busy_since = 0;
    while (h->kpos < h->nkeys && (h->keys[h->kpos] == SHOTB_KEY || h->keys[h->kpos] == BUSYB_KEY)) h->kpos++;   /* tác vụ đã xong: bỏ SHOTB không dùng tới */
    h->settle_frames++;
    while (h->kpos < h->nkeys && h->keys[h->kpos] == SHOT_KEY && h->settle_frames >= SETTLE_FRAMES) {
        if (h->shot_path) {
            char path[600], base[560];
            snprintf(base, sizeof base, "%s", h->shot_path);
            char *dot = strrchr(base, '.');
            if (dot) *dot = 0;
            snprintf(path, sizeof path, "%s.%d%s", base, ++h->shot_n, dot ? h->shot_path + (dot - base) : "");
            if (lm_write_ppm(path) != 0) h->timed_out = 1;
        }
        h->kpos++;
        h->settle_frames = 0;
    }
    if (h->kpos >= h->nkeys && h->settle_frames >= SETTLE_FRAMES) {
        if (h->shot_path && lm_write_ppm(h->shot_path) != 0) h->timed_out = 1;
        return 1;
    }
    return 0;
}
