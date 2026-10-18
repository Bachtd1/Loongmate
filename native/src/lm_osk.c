/* LoongMate native — bàn phím trên màn hình (ASCII), dùng chung: ui_osk(). Màn tuỳ biến (draw + key_fn) đẩy lên ngăn xếp.
 * D-pad di chuyển (vòng quanh), A nhấn phím, B xoá lùi (giữ để lặp; text rỗng thì KHÔNG thoát), X dấu cách, Y đổi bàn phím
 * (a-z / A-Z / ký hiệu URL), L1/R1 dời con trỏ, SELECT nhảy tới XONG. START đã bị đổi thành A ở vòng lặp chính nên không dùng. */
#define _GNU_SOURCE
#include "lm_ui.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define OSK_MAX 2000
#define NCHR 4      /* số hàng ký tự mỗi bàn phím; sau đó là hàng phím tắt rồi hàng dưới cùng */
#define NROWS (NCHR + 2)
#define NSHORT 7

static const char *const CHR_ROWS[3][NCHR] = {
    { "abcdefghij", "klmnopqrst", "uvwxyz0123", "456789" },
    { "ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ0123", "456789" },
    { "./:-_?=&%#", "@~+!,;()[]", "'*$\"{}|\\<>", "0123456789" },
};
static const char *const LAYOUT_NAME[3] = { "abc", "ABC", "#.:/" };
static const char *const SHORTCUT[NSHORT] = { "https://", "www.", ".com", ".net", ".org", ".vn", "/" };
static const char *const BOTTOM[4] = { "HUỶ", "Dấu cách", "Xoá", "XONG" };

typedef struct {
    char *text;
    int len, cur, maxlen;
    void (*cb)(const char *text, const char *arg);
    char arg[UI_ARGLEN];
    int layout, row, col;
} osk_t;

static int row_n(const osk_t *o, int r) { return r < NCHR ? (int)strlen(CHR_ROWS[o->layout][r]) : r == NCHR ? NSHORT : 4; }

/* vị trí/độ rộng phím theo đơn vị 1/1000 bề ngang: hàng ký tự luôn rộng 1/10 mỗi phím, hai hàng dưới chia đều */
static void key_geom(const osk_t *o, int r, int c, int *x, int *w) {
    if (r < NCHR) { *x = c * 100; *w = 100; }
    else { int n = row_n(o, r); *x = c * 1000 / n; *w = 1000 / n; }
}

static int nearest_col(const osk_t *o, int r, int cx) {
    int best = 0, bd = 1 << 30, n = row_n(o, r);
    for (int c = 0; c < n; c++) {
        int x, w;
        key_geom(o, r, c, &x, &w);
        int d = abs(x + w / 2 - cx);
        if (d < bd) { bd = d; best = c; }
    }
    return best;
}

static void move_v(osk_t *o, int d) {
    int x, w;
    key_geom(o, o->row, o->col, &x, &w);
    o->row = (o->row + d + NROWS) % NROWS;
    o->col = nearest_col(o, o->row, x + w / 2);
}
static void move_h(osk_t *o, int d) { int n = row_n(o, o->row); o->col = (o->col + d + n) % n; }

static void insert_str(osk_t *o, const char *s) {
    int n = (int)strlen(s);
    if (o->len + n > o->maxlen) return;
    memmove(o->text + o->cur + n, o->text + o->cur, (size_t)(o->len - o->cur + 1));
    memcpy(o->text + o->cur, s, (size_t)n);
    o->len += n; o->cur += n;
}
static void backspace(osk_t *o) {
    if (o->cur <= 0) return;      /* rỗng/đầu dòng: không làm gì, TUYỆT ĐỐI không thoát */
    memmove(o->text + o->cur - 1, o->text + o->cur, (size_t)(o->len - o->cur + 1));
    o->len--; o->cur--;
}

static void finish(ui_screen *s, osk_t *o) {
    void (*cb)(const char *, const char *) = o->cb;
    char arg[UI_ARGLEN];
    snprintf(arg, sizeof arg, "%s", o->arg);
    char *t = strdup(o->text);
    ui_pop();                      /* giải phóng s + o: chỉ dùng bản sao từ đây */
    (void)s;
    if (!t) return;
    char *b = t;
    while (*b == ' ') b++;
    size_t n = strlen(b);
    while (n && b[n - 1] == ' ') b[--n] = 0;
    if (n && cb) cb(b, arg);
    free(t);
}

static void press(ui_screen *s, osk_t *o) {
    if (o->row < NCHR) { char ch[2] = { CHR_ROWS[o->layout][o->row][o->col], 0 }; insert_str(o, ch); }
    else if (o->row == NCHR) insert_str(o, SHORTCUT[o->col]);
    else switch (o->col) {
        case 0: ui_pop(); break;                 /* HUỶ: không gọi cb */
        case 1: insert_str(o, " "); break;
        case 2: backspace(o); break;
        default: finish(s, o); break;
    }
}

static int osk_key(ui_screen *s, cat_input_event *ev) {
    osk_t *o = s->priv;
    switch (ev->button) {
    case CAT_BTN_UP:    move_v(o, -1); return 1;
    case CAT_BTN_DOWN:  move_v(o, 1); return 1;
    case CAT_BTN_LEFT:  move_h(o, -1); return 1;
    case CAT_BTN_RIGHT: move_h(o, 1); return 1;
    case CAT_BTN_B:     backspace(o); return 1;                  /* giữ B = xoá lặp */
    case CAT_BTN_L1:    if (o->cur > 0) o->cur--; return 1;
    case CAT_BTN_R1:    if (o->cur < o->len) o->cur++; return 1;
    default: break;
    }
    if (ev->repeated) return 1;
    switch (ev->button) {
    case CAT_BTN_A: press(s, o); break;
    case CAT_BTN_X: insert_str(o, " "); break;
    case CAT_BTN_Y: {
        int x, w;
        key_geom(o, o->row, o->col, &x, &w);
        o->layout = (o->layout + 1) % 3;
        o->col = nearest_col(o, o->row, x + w / 2);
        break;
    }
    case CAT_BTN_SELECT: o->row = NROWS - 1; o->col = 3; break;
    default: break;
    }
    return 1;
}

static uint8_t clamp8(int v) { return (uint8_t)(v > 255 ? 255 : v < 0 ? 0 : v); }
static cat_draw_color lift(cat_draw_color c, int d) { return (cat_draw_color){ clamp8(c.r + d), clamp8(c.g + d), clamp8(c.b + d), 255 }; }

/* số byte đầu của text (tới `to`) lập thành chuỗi "…text[from..to)" đo được độ rộng */
static int span_w(TTF_Font *f, const char *text, int from, int to, int dots, char *buf) {
    snprintf(buf, OSK_MAX + 8, "%s%.*s", dots ? "..." : "", to - from, text + from);
    return cat_measure_text(f, buf);
}

static void osk_draw(ui_screen *s, SDL_Rect a) {
    osk_t *o = s->priv;
    cat_theme *th = cat_get_theme();
    TTF_Font *fs = cat_get_font(CAT_FONT_SMALL), *ft = cat_get_font(CAT_FONT_TINY);
    int pad = cat_device_scale(8), gap = cat_device_scale(4), y = a.y;

    /* ô nhập: hiện ĐUÔI khi dài hơn bề ngang, con trỏ đúng chỗ chèn */
    int fh = TTF_FontHeight(fs) + 2 * pad;
    cat_draw_rounded_rect(a.x, y, a.w, fh, cat_device_scale(6), th->hint);
    cat_draw_rounded_rect(a.x + 2, y + 2, a.w - 4, fh - 4, cat_device_scale(6), lift(th->background, 16));
    int fw = a.w - 2 * pad - 2;
    static char buf[OSK_MAX + 8];
    int start = 0;
    if (span_w(fs, o->text, 0, o->cur, 0, buf) > fw) {        /* start nhỏ nhất để [start, cur) vừa chỗ */
        int lo = 1, hi = o->cur;
        while (lo < hi) { int mid = (lo + hi) / 2; if (span_w(fs, o->text, mid, o->cur, 1, buf) <= fw) hi = mid; else lo = mid + 1; }
        start = lo;
    }
    int end = o->len;
    if (span_w(fs, o->text, start, end, start > 0, buf) > fw) {   /* end lớn nhất >= cur để [start, end) vừa chỗ */
        int lo = o->cur, hi = o->len;
        while (lo < hi) { int mid = (lo + hi + 1) / 2; if (span_w(fs, o->text, start, mid, start > 0, buf) <= fw) lo = mid; else hi = mid - 1; }
        end = lo;
    }
    int cw = span_w(fs, o->text, start, o->cur, start > 0, buf);
    span_w(fs, o->text, start, end, start > 0, buf);
    int ty = y + (fh - TTF_FontHeight(fs)) / 2;
    cat_draw_text(fs, buf, a.x + pad, ty, th->text);
    cat_draw_rect(a.x + pad + cw, ty, 2, TTF_FontHeight(fs), th->highlight);
    y += fh + gap;

    char hint[160];
    snprintf(hint, sizeof hint, "Bàn phím: %s · %d/%d ký tự · L1/R1 dời con trỏ · SELECT tới XONG", LAYOUT_NAME[o->layout], o->len, o->maxlen);
    cat_draw_text_ellipsized(ft, hint, a.x, y, th->hint, a.w);
    y += TTF_FontHeight(ft) + gap * 2;

    int rh = (a.y + a.h - y) / NROWS;
    int cap = cat_device_scale(72);
    if (rh > cap) rh = cap;
    for (int r = 0; r < NROWS; r++) {
        for (int c = 0; c < row_n(o, r); c++) {
            int kx, kw;
            key_geom(o, r, c, &kx, &kw);
            int px = a.x + kx * a.w / 1000, pw = kw * a.w / 1000 - gap, py = y + r * rh, ph = rh - gap;
            int sel = r == o->row && c == o->col;
            const char *lab;
            char ch[2] = { 0, 0 };
            if (r < NCHR) { ch[0] = CHR_ROWS[o->layout][r][c]; lab = ch; }
            else lab = r == NCHR ? SHORTCUT[c] : BOTTOM[c];
            cat_draw_rounded_rect(px, py, pw, ph, cat_device_scale(6), sel ? th->highlight : lift(th->background, 24));
            cat_draw_color tc = sel ? th->highlighted_text : th->text;
            if (!sel && r == NROWS - 1 && c == 3) tc = ui_color(ST_GREEN);
            if (!sel && r == NROWS - 1 && c == 0) tc = ui_color(ST_RED);
            int lx = px + (pw - cat_measure_text(fs, lab)) / 2;
            cat_draw_text_ellipsized(fs, lab, lx > px ? lx : px, py + (ph - TTF_FontHeight(fs)) / 2, tc, pw);
        }
    }
}

static void osk_free(ui_screen *s) {
    osk_t *o = s->priv;
    if (o) { free(o->text); free(o); }
}

void ui_osk(const char *title, const char *initial, int maxlen, void (*cb)(const char *text, const char *arg), const char *arg) {
    if (maxlen < 1) maxlen = 1;
    if (maxlen > OSK_MAX) maxlen = OSK_MAX;
    osk_t *o = calloc(1, sizeof *o);
    ui_screen *s = ui_screen_new(title, NULL);
    if (!o || !s) { free(o); free(s); return; }
    o->text = calloc((size_t)maxlen + 1, 1);
    if (!o->text) { free(o); free(s); return; }
    snprintf(o->text, (size_t)maxlen + 1, "%s", initial ? initial : "");
    o->len = o->cur = (int)strlen(o->text);
    o->maxlen = maxlen;
    o->cb = cb;
    snprintf(o->arg, sizeof o->arg, "%s", arg ? arg : "");
    o->col = 0; o->row = 0;
    s->priv = o;
    s->draw = osk_draw;
    s->key_fn = osk_key;
    s->on_free = osk_free;
    s->footer[0] = (cat_footer_item){ .button = CAT_BTN_B, .label = "XOÁ" };
    s->footer[1] = (cat_footer_item){ .button = CAT_BTN_X, .label = "DẤU CÁCH" };
    s->footer[2] = (cat_footer_item){ .button = CAT_BTN_Y, .label = "ĐỔI BÀN PHÍM" };
    s->footer[3] = (cat_footer_item){ .button = CAT_BTN_A, .label = "CHỌN", .is_confirm = true };
    s->nfooter = 4;
    ui_push(s);
}
