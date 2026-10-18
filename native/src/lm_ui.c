#define _GNU_SOURCE
#include "lm_ui.h"
#include "lm_i18n.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_STACK 36
#define SCROLL_STEP 3

int g_debounce_ms = 400;

/* ============================================================ lines_t */
void lines_add(lines_t *L, const char *fmt, ...) {
    char *s = NULL;
    va_list ap;
    va_start(ap, fmt);
    if (vasprintf(&s, tr(fmt), ap) < 0) s = NULL;
    va_end(ap);
    if (!s) return;
    if (L->n >= L->cap) {
        int nc = L->cap ? L->cap * 2 : 16;
        char **nv = realloc(L->v, (size_t)nc * sizeof *nv);
        if (!nv) { free(s); return; }
        L->v = nv; L->cap = nc;
    }
    L->v[L->n++] = s;
}

void lines_free(lines_t *L) {
    for (int i = 0; i < L->n; i++) free(L->v[i]);
    free(L->v);
    L->v = NULL; L->n = L->cap = 0;
}

/* ============================================================ tiện ích */
void ui_fmt_bytes(char *buf, size_t n, double bytes) {
    if (bytes >= 1048576.0) snprintf(buf, n, "%.1f MB", bytes / 1048576.0);
    else snprintf(buf, n, "%.1f KB", bytes / 1024.0);
}

void ui_fmt_mb(char *buf, size_t n, double mb) {
    if (mb >= 1024.0) snprintf(buf, n, "%.1f GB", mb / 1024.0);
    else snprintf(buf, n, "%.0f MB", mb);
}

const char *ui_status_vi(const char *s) {
    if (!s) return tr("Chưa đọc được");
    if (!strcmp(s, "green")) return tr("Ổn");
    if (!strcmp(s, "yellow")) return tr("Cần chú ý");
    if (!strcmp(s, "red")) return tr("Có vấn đề");
    return s;
}

status_t ui_parse_status(const char *s) {
    if (!s) return ST_UNKNOWN;
    if (!strcmp(s, "green")) return ST_GREEN;
    if (!strcmp(s, "yellow")) return ST_YELLOW;
    if (!strcmp(s, "red")) return ST_RED;
    return ST_UNKNOWN;
}
status_t ui_tagc(const char *s) { return ui_parse_status(s); }

static int bg_is_dark(void) {
    cat_draw_color b = cat_get_theme()->background;
    return (b.r * 299 + b.g * 587 + b.b * 114) / 1000 < 128;
}

cat_draw_color ui_color(status_t s) {
    int dark = bg_is_dark();
    switch (s) {
    case ST_GREEN:  return dark ? (cat_draw_color){ 76, 175, 80, 255 }  : (cat_draw_color){ 46, 125, 50, 255 };
    case ST_YELLOW: return dark ? (cat_draw_color){ 255, 193, 7, 255 }  : (cat_draw_color){ 176, 112, 0, 255 };
    case ST_RED:    return dark ? (cat_draw_color){ 239, 83, 80, 255 }  : (cat_draw_color){ 198, 40, 40, 255 };
    default:        return cat_get_theme()->hint;
    }
}

cat_draw_color ui_line_color(const char *l) {
    if (l[0] == '!' && l[1] == '!') return ui_color(ST_RED);
    if (l[0] == '!') return ui_color(ST_YELLOW);
    if (l[0] == '+') return ui_color(ST_GREEN);
    return cat_get_theme()->text;
}

void ui_tail(char *buf, size_t n, const char *s, size_t keep) {
    size_t len = strlen(s);
    if (len <= keep + 3) snprintf(buf, n, "%s", s);
    else snprintf(buf, n, "...%s", s + len - keep);
}

/* Nhãn dòng quá dài: cắt GIỮA ("Bomberman Ma...(Blue)") để giữ cả phần đuôi phân biệt các bản
 * (Disc 2, (Blue)/(Red), (USA)/(Japan)...). Cắt ở ranh giới ký tự UTF-8. Đuôi giữ tối đa 14 ký tự, nhưng co dần
 * (xuống 4) khi chỗ hẹp để phần đầu còn ít nhất 6 byte; không vừa nổi thì cắt cuối như cũ. */
static void draw_mid_ellipsized(TTF_Font *f, const char *s, int x, int y, cat_draw_color c, int max_w) {
    if (max_w <= 0 || cat_measure_text(f, s) <= max_w) { cat_draw_text(f, s, x, y, c); return; }
    size_t len = strlen(s);
    char buf[512];
    for (int keep = 14; keep >= 4; keep--) {
        size_t tail = len;
        for (int chars = 0; tail > 0 && chars < keep; chars++) {
            tail--;
            while (tail > 0 && ((unsigned char)s[tail] & 0xC0) == 0x80) tail--;
        }
        for (size_t head = tail > 400 ? 401 : tail; head > 0; ) {   /* buf 512: đầu <= 400 byte để đuôi (<= 56 byte) không bị snprintf cắt mất */
            head--;
            while (head > 0 && ((unsigned char)s[head] & 0xC0) == 0x80) head--;
            if (head < 6) break;
            snprintf(buf, sizeof buf, "%.*s...%s", (int)head, s, s + tail);
            if (cat_measure_text(f, buf) <= max_w) { cat_draw_text(f, buf, x, y, c); return; }
        }
    }
    cat_draw_text_ellipsized(f, s, x, y, c, max_w);
}

static uint8_t clamp8(int v) { return (uint8_t)(v > 255 ? 255 : v < 0 ? 0 : v); }
static cat_draw_color lift(cat_draw_color c, int d) {
    return (cat_draw_color){ clamp8(c.r + d), clamp8(c.g + d), clamp8(c.b + d), 255 };
}

/* ============================================================ xuống dòng */
static size_t utf8_len(unsigned char c) { return c < 0x80 ? 1 : (c >> 5) == 6 ? 2 : (c >> 4) == 14 ? 3 : (c >> 3) == 30 ? 4 : 1; }

/* thêm vào out các dòng đã bẻ; trả số dòng thêm */
int ui_wrap(TTF_Font *f, const char *text, int maxw, lines_t *out) {
    int before = out->n;
    if (!text || !text[0]) { lines_add(out, "%s", ""); return 1; }
    size_t cap = strlen(text) + 4;
    char *cur = calloc(1, cap), *tent = calloc(1, cap * 2 + 2);
    if (!cur || !tent) { free(cur); free(tent); lines_add(out, "%s", text); return 1; }
    const char *p = text;
    while (*p == ' ') p++;
    while (*p) {
        const char *e = p;
        while (*e && *e != ' ') e++;
        size_t wl = (size_t)(e - p);
        char *word = strndup(p, wl);
        if (!word) break;
        if (cur[0]) snprintf(tent, cap * 2, "%s %s", cur, word); else snprintf(tent, cap * 2, "%s", word);
        if (cat_measure_text(f, tent) <= maxw) {
            snprintf(cur, cap, "%s", tent);
        } else {
            if (cur[0]) { lines_add(out, "%s", cur); cur[0] = 0; }
            if (cat_measure_text(f, word) <= maxw) snprintf(cur, cap, "%s", word);
            else {   /* từ dài không có khoảng trắng (đường dẫn): bẻ theo ký tự UTF-8, tìm nhị phân điểm cắt */
                const char *w = word;
                while (*w) {
                    size_t total = strlen(w), nch = 0;
                    size_t *off = malloc((total + 2) * sizeof *off);
                    if (!off) break;
                    for (size_t o = 0; o < total; o += utf8_len((unsigned char)w[o])) off[nch++] = o;
                    off[nch] = total;
                    size_t lo = 1, hi = nch;     /* số ký tự giữ lại */
                    while (lo < hi) {
                        size_t mid = (lo + hi + 1) / 2;
                        char *t = strndup(w, off[mid]);
                        int ok = t && cat_measure_text(f, t) <= maxw;
                        free(t);
                        if (ok) lo = mid; else hi = mid - 1;
                    }
                    size_t cut = off[lo];
                    free(off);
                    if (w[cut] == 0) { snprintf(cur, cap, "%s", w); break; }
                    char *piece = strndup(w, cut);
                    if (piece) { lines_add(out, "%s", piece); free(piece); }
                    w += cut;
                }
            }
        }
        free(word);
        p = e;
        while (*p == ' ') p++;
    }
    if (cur[0] || out->n == before) lines_add(out, "%s", cur);
    free(cur); free(tent);
    return out->n - before;
}

/* ============================================================ màn hình & ngăn xếp */
static ui_screen *g_stack[MAX_STACK];
static int g_depth;
static int g_quit_req;

static void item_free(ui_item *it) {
    lines_free(&it->exp);
    free(it);
}

static void screen_clear(ui_screen *s) {
    for (int i = 0; i < s->n; i++) item_free(s->items[i]);
    free(s->items);
    s->items = NULL; s->n = s->cap = 0;
    lines_free(&s->hdr);
}

static void screen_free(ui_screen *s) {
    if (s->on_free) s->on_free(s);
    screen_clear(s);
    cJSON_Delete(s->d);
    free(s);
}

ui_screen *ui_screen_new(const char *title, void (*build)(ui_screen *)) {
    ui_screen *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    snprintf(s->title, sizeof s->title, "%s", title);
    s->build = build;
    return s;
}

static void step_sel(ui_screen *s, int d) {
    int i = s->sel;
    for (int k = 0; k < s->n; k++) {
        i += d;
        if (i < 0 || i >= s->n) return;
        if (!s->items[i]->head) { s->sel = i; s->exp_scroll = 0; return; }
    }
}

void ui_rebuild(ui_screen *s) {
    if (!s->build) return;
    screen_clear(s);
    s->build(s);
    if (s->sel >= s->n) s->sel = s->n ? s->n - 1 : 0;
    if (s->n && s->items[s->sel]->head) step_sel(s, 1);
    if (s->n && s->items[s->sel]->head) step_sel(s, -1);
}

ui_screen *ui_push(ui_screen *s) {
    if (!s) return NULL;
    if (g_depth >= MAX_STACK) { screen_free(s); return NULL; }
    g_stack[g_depth++] = s;
    ui_rebuild(s);
    return s;
}

void ui_pop(void) {
    if (g_depth <= 1) return;
    screen_free(g_stack[--g_depth]);
    g_stack[g_depth] = NULL;
}
void ui_pop_to(int depth) { while (g_depth > depth && g_depth > 1) ui_pop(); }
void ui_drop_at(int idx) {
    if (idx < 0 || idx >= g_depth - 1 || g_depth <= 1) return;
    screen_free(g_stack[idx]);
    for (int i = idx; i < g_depth - 1; i++) g_stack[i] = g_stack[i + 1];
    g_stack[--g_depth] = NULL;
}
ui_screen *ui_screen_at(int idx) {
    if (idx < 0 || idx >= g_depth) return NULL;
    return g_stack[idx];
}
int ui_depth(void) { return g_depth; }
ui_screen *ui_top(void) { return g_depth ? g_stack[g_depth - 1] : NULL; }
void ui_rebuild_all(void) {
    for (int i = 0; i < g_depth; i++)
        if (g_stack[i]->build) ui_rebuild(g_stack[i]);
}
void ui_quit(void) { g_quit_req = 1; }

void ui_hdr(ui_screen *s, const char *fmt, ...) {
    char *t = NULL;
    va_list ap;
    va_start(ap, fmt);
    if (vasprintf(&t, tr(fmt), ap) < 0) t = NULL;
    va_end(ap);
    if (t) { lines_add(&s->hdr, "%s", t); free(t); }
}

static ui_item *item_new(ui_screen *s) {
    ui_item *it = calloc(1, sizeof *it);
    if (!it) return NULL;
    if (s->n >= s->cap) {
        int nc = s->cap ? s->cap * 2 : 32;
        ui_item **ni = realloc(s->items, (size_t)nc * sizeof *ni);
        if (!ni) { free(it); return NULL; }
        s->items = ni; s->cap = nc;
    }
    s->items[s->n++] = it;
    it->tagc = ST_UNKNOWN;
    return it;
}

ui_item *ui_head(ui_screen *s, const char *fmt, ...) {
    ui_item *it = item_new(s);
    if (!it) return NULL;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(it->label, sizeof it->label, tr(fmt), ap);
    va_end(ap);
    it->head = 1;
    return it;
}

ui_item *ui_add(ui_screen *s, const char *label, const char *tag, int tagc) {
    ui_item *it = item_new(s);
    if (!it) return NULL;
    snprintf(it->label, sizeof it->label, "%s", label ? tr(label) : "");   /* tr: nhãn từ mảng tĩnh chưa dịch ở nơi gọi */
    snprintf(it->tag, sizeof it->tag, "%s", tag ? tr(tag) : "");
    it->tagc = tagc;
    return it;
}

ui_item *ui_add_raw(ui_screen *s, const char *label, const char *tag) {
    ui_item *it = item_new(s);
    if (!it) return NULL;
    snprintf(it->label, sizeof it->label, "%s", label ? label : "");
    snprintf(it->tag, sizeof it->tag, "%s", tag ? tag : "");
    return it;
}

void ui_exp(ui_item *it, const char *fmt, ...) {
    if (!it) return;
    char *t = NULL;
    va_list ap;
    va_start(ap, fmt);
    if (vasprintf(&t, tr(fmt), ap) < 0) t = NULL;
    va_end(ap);
    if (t) { lines_add(&it->exp, "%s", t); free(t); }
}

void ui_act(ui_item *it, int slot, const char *lbl, ui_fn fn) {
    if (!it || slot < 0 || slot > 2) return;
    it->fn[slot] = fn;
    it->lbl[slot] = lbl;
}
void ui_arg(ui_item *it, const char *arg) { if (it) snprintf(it->arg, sizeof it->arg, "%s", arg ? arg : ""); }

/* ============================================================ hộp thoại */
typedef struct { char *s; cat_draw_color col; } wl_t;
static struct {
    int open;
    char title[96];
    wl_t *w; int nw;
    int scroll, choice, confirm;
    char clabel[48];
    ui_confirm_cb cb;
    char arg[UI_ARGLEN];
    unsigned opened_ms;
} D;

static void dialog_close(void) {
    for (int i = 0; i < D.nw; i++) free(D.w[i].s);
    free(D.w);
    D.w = NULL; D.nw = 0; D.open = 0;
}

static int dlg_box_w(void) { return cat_get_screen_width() * 92 / 100; }
static int dlg_top(void) { return cat_device_scale(8); }
static int dlg_box_h(void) { return cat_get_screen_height() - dlg_top() - cat_get_footer_height() - cat_device_scale(22); }

static void dialog_open(const char *title, lines_t *L, int confirm, const char *clabel, ui_confirm_cb cb, const char *arg) {
    if (D.open) dialog_close();
    memset(&D, 0, sizeof D);
    snprintf(D.title, sizeof D.title, "%s", tr(title));
    D.confirm = confirm;
    snprintf(D.clabel, sizeof D.clabel, "%s", clabel ? clabel : tr("Xác nhận"));
    D.cb = cb;
    snprintf(D.arg, sizeof D.arg, "%s", arg ? arg : "");
    TTF_Font *f = cat_get_font(CAT_FONT_SMALL);
    int tw = dlg_box_w() - 2 * cat_device_scale(12);
    int cap = 0;
    for (int i = 0; i < L->n; i++) {
        lines_t tmp = {0};
        ui_wrap(f, L->v[i], tw, &tmp);
        for (int k = 0; k < tmp.n; k++) {
            if (D.nw >= cap) {
                cap = cap ? cap * 2 : 64;
                wl_t *nw = realloc(D.w, (size_t)cap * sizeof *nw);
                if (!nw) break;
                D.w = nw;
            }
            D.w[D.nw].s = tmp.v[k];     /* chuyển quyền sở hữu chuỗi */
            D.w[D.nw].col = ui_line_color(L->v[i]);
            D.nw++;
            tmp.v[k] = NULL;
        }
        lines_free(&tmp);
    }
    lines_free(L);
    D.open = 1;
    D.opened_ms = SDL_GetTicks();
    cat_request_frame();
}

void ui_info(const char *title, lines_t *L) { dialog_open(title, L, 0, NULL, NULL, NULL); }

void ui_info1(const char *title, const char *fmt, ...) {
    lines_t L = {0};
    char *t = NULL;
    va_list ap;
    va_start(ap, fmt);
    if (vasprintf(&t, tr(fmt), ap) < 0) t = NULL;
    va_end(ap);
    if (t) { lines_add(&L, "%s", t); free(t); }
    dialog_open(title, &L, 0, NULL, NULL, NULL);
}

void ui_confirm(const char *title, lines_t *L, const char *confirm_label, ui_confirm_cb cb, const char *arg) {
    dialog_open(title, L, 1, confirm_label, cb, arg);
}

/* ---- chọn thẻ đích: card = object "card" của kế hoạch engine (importer.choose_card) ---- */
typedef struct { ui_confirm_cb replan; } card_pick_t;

static void card_pick_act(ui_screen *s, ui_item *it) {
    ui_confirm_cb replan = ((card_pick_t *)s->priv)->replan;
    char pref[16];
    snprintf(pref, sizeof pref, "%s", it->arg);
    ui_pop();                          /* giải phóng s + it: chỉ dùng bản sao từ đây */
    replan(pref);
}

static void card_pick_free(ui_screen *s) { free(s->priv); }

static void card_pick_build(ui_screen *s) {
    char need[32], fr[32];
    ui_fmt_mb(need, sizeof need, jnumd(s->d, "needed_bytes", 0) / (1024.0 * 1024.0));
    ui_hdr(s, tr("Cả hai thẻ đều ghi được và đủ chỗ (cần ~%s). Lưu game vào thẻ nào?"), need);
    cJSON *o;
    cJSON_ArrayForEach(o, jarr(s->d, "options")) {
        if (!jbool_d(o, "usable", 0)) continue;
        int pri = !strcmp(jstrd(o, "card", ""), "primary");
        ui_fmt_mb(fr, sizeof fr, jnumd(o, "free_bytes", 0) / (1024.0 * 1024.0));
        ui_item *it = ui_add(s, pri ? tr("SD1 — thẻ Leaf") : tr("SD2 — thẻ phụ"), fr, pri ? ST_YELLOW : ST_GREEN);
        if (!it) continue;
        ui_exp(it, tr("Thư mục gốc: %s"), jstrd(o, "root", "?"));
        ui_exp(it, tr("Còn trống %s, cần ~%s (file + staging + dự phòng)."), fr, need);
        ui_exp(it, pri ? tr("Cùng thẻ với hệ thống Leaf: thẻ này đầy thì Leaf có thể lỗi khi ghi save/cấu hình.")
                       : tr("Thẻ chỉ chứa game: không làm đầy thẻ hệ thống Leaf."));
        ui_exp(it, tr("Game vào Roms/<hệ máy>/ trên thẻ này (thư mục hệ máy tự tạo nếu chưa có)."));
        ui_arg(it, pri ? "primary" : "secondary");
        ui_act(it, 0, tr("CHỌN"), card_pick_act);
    }
}

int ui_card_pick(const cJSON *card, const char *pref, ui_confirm_cb replan) {
    if (!card || (pref && pref[0] && strcmp(pref, "auto"))) return 0;      /* người dùng đã chọn thẻ */
    int usable = 0;
    cJSON *o;
    cJSON_ArrayForEach(o, jarr(card, "options")) usable += jbool_d(o, "usable", 0);
    if (usable < 2) return 0;
    ui_screen *s = ui_screen_new("CHỌN THẺ LƯU GAME", card_pick_build);
    card_pick_t *c = calloc(1, sizeof *c);
    if (!s || !c) { free(s); free(c); return 0; }
    c->replan = replan;
    s->priv = c;
    s->on_free = card_pick_free;
    s->d = cJSON_Duplicate(card, 1);
    return ui_push(s) != NULL;
}

static int dlg_rows(void) {
    TTF_Font *f = cat_get_font(CAT_FONT_SMALL);
    int box_h = dlg_box_h();
    int reserve = TTF_FontHeight(cat_get_font(CAT_FONT_LARGE)) + cat_device_scale(14)      /* tiêu đề */
                + TTF_FontHeight(cat_get_font(CAT_FONT_TINY)) + cat_device_scale(8)         /* dòng cuộn */
                + (D.confirm ? cat_device_scale(34) : cat_device_scale(6));                  /* nút */
    int r = (box_h - reserve) / (TTF_FontHeight(f) + cat_device_scale(2));
    return r < 1 ? 1 : r;
}

static void dialog_key(cat_input_event *ev) {
    int rows = dlg_rows();
    int maxscroll = D.nw > rows ? D.nw - rows : 0;
    switch (ev->button) {
    case CAT_BTN_UP:   D.scroll = D.scroll - SCROLL_STEP < 0 ? 0 : D.scroll - SCROLL_STEP; return;
    case CAT_BTN_DOWN: D.scroll = D.scroll + SCROLL_STEP > maxscroll ? maxscroll : D.scroll + SCROLL_STEP; return;
    default: break;
    }
    if (ev->repeated) return;
    unsigned age = SDL_GetTicks() - D.opened_ms;
    if ((ev->button == CAT_BTN_A || ev->button == CAT_BTN_B || ev->button == CAT_BTN_X || ev->button == CAT_BTN_Y) &&
        (int)age < g_debounce_ms)
        return;     /* chống bấm đúp: phím bấm dồn ngay lúc hộp vừa hiện không được xác nhận/đóng nhầm */
    if (D.confirm) {
        if (ev->button == CAT_BTN_LEFT) D.choice = 0;
        else if (ev->button == CAT_BTN_RIGHT) D.choice = 1;
        else if (ev->button == CAT_BTN_B) dialog_close();
        else if (ev->button == CAT_BTN_A) {
            ui_confirm_cb cb = D.cb;
            int ok = D.choice == 1;
            char arg[UI_ARGLEN];
            snprintf(arg, sizeof arg, "%s", D.arg);
            dialog_close();                 /* đóng TRƯỚC khi chạy: cb có thể mở hộp/tác vụ mới */
            if (ok && cb) cb(arg);
        }
    } else if (ev->button == CAT_BTN_A || ev->button == CAT_BTN_B || ev->button == CAT_BTN_X || ev->button == CAT_BTN_Y) {
        dialog_close();
    }
}

static void dialog_draw(void) {
    cat_theme *th = cat_get_theme();
    int sw = cat_get_screen_width(), sh = cat_get_screen_height();
    cat_draw_rect(0, 0, sw, sh, (cat_draw_color){ 0, 0, 0, 170 });
    int w = dlg_box_w(), h = dlg_box_h(), pad = cat_device_scale(12);
    int x = (sw - w) / 2, y = dlg_top();
    cat_draw_rounded_rect(x, y, w, h, cat_device_scale(8), th->highlight);
    cat_draw_rounded_rect(x + 3, y + 3, w - 6, h - 6, cat_device_scale(8), lift(th->background, 14));
    TTF_Font *ft = cat_get_font(CAT_FONT_LARGE), *fb = cat_get_font(CAT_FONT_SMALL), *fs = cat_get_font(CAT_FONT_TINY);
    int ty = y + cat_device_scale(8);
    cat_draw_text_ellipsized(ft, D.title, x + pad, ty, th->text, w - 2 * pad);
    ty += TTF_FontHeight(ft) + cat_device_scale(6);
    int rows = dlg_rows(), lh = TTF_FontHeight(fb) + cat_device_scale(2);
    int maxscroll = D.nw > rows ? D.nw - rows : 0;
    if (D.scroll > maxscroll) D.scroll = maxscroll;
    for (int i = 0; i < rows && D.scroll + i < D.nw; i++)
        cat_draw_text(fb, D.w[D.scroll + i].s, x + pad, ty + i * lh, D.w[D.scroll + i].col);
    int by = y + h - cat_device_scale(6);
    if (D.confirm) {
        int bh = cat_device_scale(30), bw = cat_device_scale(110);
        by -= bh;
        const char *lab[2] = { tr("Huỷ"), tr(D.clabel) };
        for (int i = 0; i < 2; i++) {
            int lw = cat_measure_text(fb, lab[i]) + cat_device_scale(24);
            int bx = x + pad + (i ? bw + cat_device_scale(14) : 0), ww = i ? (lw > bw ? lw : bw) : bw;
            if (D.choice == i) {
                cat_draw_pill(bx, by, ww, bh, th->highlight);
                cat_draw_text(fb, lab[i], bx + (ww - cat_measure_text(fb, lab[i])) / 2, by + (bh - TTF_FontHeight(fb)) / 2, th->highlighted_text);
            } else {
                cat_draw_pill(bx, by, ww, bh, lift(th->background, -22));
                cat_draw_text(fb, lab[i], bx + (ww - cat_measure_text(fb, lab[i])) / 2, by + (bh - TTF_FontHeight(fb)) / 2, th->text);
            }
        }
        cat_draw_text_ellipsized(fs, tr("Trái/Phải chọn nút"), x + pad + 2 * bw + cat_device_scale(40),
                                 by + (bh - TTF_FontHeight(fs)) / 2, th->hint, w - 2 * bw - cat_device_scale(70));
    }
    if (D.nw > rows) {
        char info[96];
        snprintf(info, sizeof info, tr("(dòng %d-%d/%d — Lên/Xuống để cuộn)"), D.scroll + 1, D.scroll + rows < D.nw ? D.scroll + rows : D.nw, D.nw);
        cat_draw_text(fs, info, x + pad, by - TTF_FontHeight(fs) - cat_device_scale(4), th->hint);
    }
}

/* ============================================================ tác vụ nền */
static struct {
    lm_job *job;
    char label[96];
    ui_task_cb cb;
    char arg[UI_ARGLEN];
    unsigned t0;
    int cancelable, cancel_req;
} T;
static int g_cancelled;     /* chỉ đúng trong lúc callback của tác vụ vừa bị tạm dừng chạy */

int ui_busy(void) { return T.job != NULL; }
void ui_task_cancelable(int on) { if (T.job) T.cancelable = on; }
int ui_task_cancelled(void) { return g_cancelled; }

int ui_task(const char *label, char *const argv[], int timeout_ms, ui_task_cb cb, const char *arg) {
    if (T.job) return 0;      /* chống chạy chồng: đang có tác vụ thì bỏ qua */
    char err[200];
    lm_job *j = lm_job_start(argv, timeout_ms, err, sizeof err);
    if (!j) { ui_info1(tr("Lỗi"), tr("%s thất bại: %s"), label, err); return 0; }
    T.job = j;
    T.cb = cb;
    snprintf(T.label, sizeof T.label, "%s", tr(label));
    snprintf(T.arg, sizeof T.arg, "%s", arg ? arg : "");
    T.t0 = SDL_GetTicks();
    T.cancelable = T.cancel_req = 0;
    cat_request_frame();
    return 1;
}

void ui_job_failure(const lm_job *j, char *buf, size_t n) {
    if (lm_job_timed_out(j)) { snprintf(buf, n, tr("engine quá thời gian chờ")); return; }
    int ec = lm_job_exit_code(j);
    if (ec == 127) { snprintf(buf, n, tr("không chạy được engine (execv thất bại)")); return; }
    const char *e = lm_job_stderr(j);
    size_t len = strlen(e);
    while (len && (e[len - 1] == '\n' || e[len - 1] == '\r')) len--;
    size_t s = len;
    while (s && e[s - 1] != '\n') s--;
    if (len > s) snprintf(buf, n, tr("engine thoát mã %d: %.*s"), ec, (int)(len - s), e + s);
    else snprintf(buf, n, tr("engine thoát mã %d, không có thông báo"), ec);
}

cJSON *ui_job_json(const lm_job *j, char *err, size_t errsz) {
    cJSON *r = cJSON_Parse(lm_job_stdout(j));
    if (cJSON_IsObject(r) || cJSON_IsArray(r)) return r;
    cJSON_Delete(r);
    char why[300];
    ui_job_failure(j, why, sizeof why);
    snprintf(err, errsz, tr("JSON không hợp lệ — %s"), why);
    return NULL;
}

static int task_pump(void) {
    if (!T.job) return 0;
    if (!lm_job_poll(T.job)) return 1;
    lm_job *j = T.job;
    ui_task_cb cb = T.cb;
    char arg[UI_ARGLEN];
    snprintf(arg, sizeof arg, "%s", T.arg);
    int was_cancelled = T.cancel_req;
    T.job = NULL;                  /* cb có thể khởi chạy tác vụ tiếp theo */
    T.cancelable = T.cancel_req = 0;
    g_cancelled = was_cancelled;
    if (cb) cb(j, arg);
    g_cancelled = 0;
    lm_job_free(j);
    return T.job != NULL;
}

/* ============================================================ đo nhãn (kiểm thử) */
static int credit_h(void);
/* LM_MEASURE=1: đọc từng dòng "label<TAB>tag" (hoặc "#HEAD<TAB>tiêu đề nhóm") ở stdin, in những dòng sẽ bị cắt "..."
 * theo đúng bố cục của render()+draw_list(). Dùng bởi native/tools/label_audit.py để rà mọi nhãn viết cứng. */
void ui_measure_rows(FILE *in, FILE *out) {
    SDL_Rect a = cat_get_content_rect(true, true, true);
    int inset = cat_device_scale(5);
    a.x += inset; a.w -= 2 * inset;
    a.h -= credit_h();
    TTF_Font *fh = cat_get_font(CAT_FONT_TINY), *fl = cat_get_font(CAT_FONT_SMALL);
    int lw = a.w * 54 / 100, sbw = cat_device_scale(6), pad = cat_device_scale(8);
    int avail = lw - sbw - 2 * pad;
    char line[1024];
    while (fgets(line, sizeof line, in)) {
        line[strcspn(line, "\r\n")] = 0;
        char *tag = strchr(line, '\t');
        if (tag) *tag++ = 0; else tag = "";
        if (!strcmp(line, "#HEAD")) {
            int w = cat_measure_text(fh, tag);
            if (w > lw - sbw) fprintf(out, "CUT\tHEAD\t%s\t\t%d/%d\n", tag, w, lw - sbw);
            continue;
        }
        int labw = cat_measure_text(fl, line), tagw = 0, tagfull = 0, cap = 0;
        if (tag[0]) {
            cap = avail * 40 / 100;
            int room = avail - labw - pad;
            if (room > cap) cap = room;
            tagfull = cat_measure_text(fh, tag);
            tagw = cat_measure_text_ellipsized(fh, tag, cap);
        }
        int allow = avail - (tagw ? tagw + pad : 0);
        if (labw > allow || tagfull > cap)
            fprintf(out, "CUT\t%s\t%s\t%s\t%d/%d\n", labw > allow ? "LABEL" : "TAG", line, tag, labw, allow);
    }
}

/* ============================================================ vẽ danh sách */
static void draw_list(ui_screen *s, SDL_Rect a) {
    cat_theme *th = cat_get_theme();
    TTF_Font *fh = cat_get_font(CAT_FONT_TINY), *fl = cat_get_font(CAT_FONT_SMALL);
    int gap = cat_device_scale(6), y = a.y;
    int hlh = TTF_FontHeight(fh) + cat_device_scale(1);
    /* header (tối đa 5 dòng sau khi bẻ) */
    int hl_drawn = 0;
    for (int i = 0; i < s->hdr.n && hl_drawn < 5; i++) {
        lines_t t = {0};
        ui_wrap(fh, s->hdr.v[i], a.w, &t);
        for (int k = 0; k < t.n && hl_drawn < 5; k++, hl_drawn++) {
            cat_draw_color c = s->hdr.v[i][0] == '!' ? ui_line_color(s->hdr.v[i]) : th->hint;
            cat_draw_text(fh, t.v[k], a.x, y, c);
            y += hlh;
        }
        lines_free(&t);
    }
    y += gap;
    int body_h = a.y + a.h - y;
    if (body_h < 40) return;
    int lw = a.w * 54 / 100, px = a.x + lw + gap, pw = a.w - lw - gap;
    int row_h = TTF_FontHeight(fl) + cat_device_scale(8);
    int rows = body_h / row_h;
    if (rows < 1) rows = 1;
    if (s->sel < s->top) s->top = s->sel;
    if (s->sel >= s->top + rows) s->top = s->sel - rows + 1;
    if (s->top < 0) s->top = 0;
    int sbw = cat_device_scale(6);
    for (int i = s->top; i < s->n && i < s->top + rows; i++) {
        ui_item *it = s->items[i];
        int ry = y + (i - s->top) * row_h;
        if (it->head) {
            cat_draw_text_ellipsized(fh, it->label, a.x + cat_device_scale(4), ry + (row_h - TTF_FontHeight(fh)) / 2 + cat_device_scale(2), th->hint, lw - sbw);
            continue;
        }
        int sel = i == s->sel;
        if (sel) cat_draw_rounded_rect(a.x, ry, lw - sbw - 2, row_h - 2, cat_device_scale(6), th->highlight);
        cat_draw_color tc = sel ? th->highlighted_text : th->text;
        int pad = cat_device_scale(8);
        int avail = lw - sbw - 2 * pad, labw = cat_measure_text(fl, it->label), tagw = 0;
        if (it->tag[0]) {   /* tag lấy tối đa 40% bề ngang (hoặc phần chỗ thừa nếu nhãn ngắn); nhãn bị cắt "..." nếu vẫn không đủ */
            int cap = avail * 40 / 100, room = avail - labw - pad;
            if (room > cap) cap = room;
            tagw = cat_measure_text_ellipsized(fh, it->tag, cap);
        }
        draw_mid_ellipsized(fl, it->label, a.x + pad, ry + (row_h - TTF_FontHeight(fl)) / 2, tc, avail - (tagw ? tagw + pad : 0));
        if (tagw) {
            cat_draw_color gc = sel ? th->highlighted_text : (it->tagc == ST_UNKNOWN ? th->hint : ui_color((status_t)it->tagc));
            cat_draw_text_ellipsized(fh, it->tag, a.x + lw - sbw - pad - tagw, ry + (row_h - TTF_FontHeight(fh)) / 2, gc, tagw);
        }
    }
    if (s->n > rows) cat_draw_scrollbar(a.x + lw - sbw, y, rows * row_h, rows, s->n, s->top);

    /* khung giải thích */
    cat_draw_rounded_rect(px, y, pw, body_h, cat_device_scale(8), th->hint);
    cat_draw_rounded_rect(px + 2, y + 2, pw - 4, body_h - 4, cat_device_scale(8), lift(th->background, 16));
    int ip = cat_device_scale(8), tx = px + ip, tw = pw - 2 * ip, ty = y + ip;
    ui_item *it = s->n ? s->items[s->sel] : NULL;
    if (!it || it->head) {
        cat_draw_text(fh, tr("(Không có mục nào)"), tx, ty, th->hint);
        return;
    }
    cat_draw_text_ellipsized(fh, tr("Giải thích"), tx, ty, th->hint, tw);
    ty += hlh + cat_device_scale(4);
    wl_t *w = NULL; int nw = 0, cap = 0;
    lines_t all = {0};
    for (int i = 0; i < it->exp.n; i++) {
        lines_t tmp = {0};
        ui_wrap(fh, it->exp.v[i], tw, &tmp);
        for (int k = 0; k < tmp.n; k++) {
            if (nw >= cap) { cap = cap ? cap * 2 : 64; wl_t *nwp = realloc(w, (size_t)cap * sizeof *nwp); if (!nwp) break; w = nwp; }
            w[nw].s = tmp.v[k]; w[nw].col = ui_line_color(it->exp.v[i]); nw++; tmp.v[k] = NULL;
        }
        lines_free(&tmp);
    }
    int lh = TTF_FontHeight(fh) + cat_device_scale(2);
    int vis = (y + body_h - ip - ty) / lh;
    if (nw > vis) vis--;        /* chừa một hàng cho chỉ báo cuộn */
    if (vis < 1) vis = 1;
    int maxs = nw > vis ? nw - vis : 0;
    if (s->exp_scroll > maxs) s->exp_scroll = maxs;
    if (s->exp_scroll < 0) s->exp_scroll = 0;
    int shown = nw - s->exp_scroll < vis ? nw - s->exp_scroll : vis;
    for (int i = 0; i < shown; i++) cat_draw_text(fh, w[s->exp_scroll + i].s, tx, ty + i * lh, w[s->exp_scroll + i].col);
    if (nw > vis) {
        char ind[64];
        snprintf(ind, sizeof ind, tr("L1/R1 cuộn %d-%d/%d"), s->exp_scroll + 1, s->exp_scroll + shown, nw);
        int iw = cat_measure_text(fh, ind);
        cat_draw_rounded_rect(px + pw - iw - 2 * ip, y + body_h - hlh - ip, iw + ip, hlh + 2, cat_device_scale(4), lift(th->background, 16));
        cat_draw_text(fh, ind, px + pw - iw - ip - cat_device_scale(4), y + body_h - hlh - ip + 1, th->hint);
    }
    for (int i = 0; i < nw; i++) free(w[i].s);
    free(w);
    lines_free(&all);
}

static int list_key(ui_screen *s, cat_input_event *ev) {
    ui_item *it = s->n ? s->items[s->sel] : NULL;
    int rows = 8;
    {   /* số dòng/trang xấp xỉ giống lúc vẽ */
        TTF_Font *fl = cat_get_font(CAT_FONT_SMALL);
        int rh = TTF_FontHeight(fl) + cat_device_scale(8);
        rows = (cat_get_screen_height() * 55 / 100) / rh;
        if (rows < 3) rows = 3;
    }
    switch (ev->button) {
    case CAT_BTN_UP:   step_sel(s, -1); return 1;
    case CAT_BTN_DOWN: step_sel(s, 1); return 1;
    case CAT_BTN_LEFT:
    case CAT_BTN_RIGHT: {
        int d = ev->button == CAT_BTN_RIGHT ? 1 : -1;
        int ns = s->sel + d * rows;
        if (ns < 0) ns = 0;
        if (ns >= s->n) ns = s->n - 1;
        if (ns != s->sel) { s->sel = ns; s->exp_scroll = 0; if (s->n && s->items[s->sel]->head) step_sel(s, d); }
        return 1;
    }
    case CAT_BTN_L1: s->exp_scroll -= SCROLL_STEP; return 1;
    case CAT_BTN_R1: s->exp_scroll += SCROLL_STEP; return 1;
    default: break;
    }
    if (ev->repeated) return 1;
    if (ev->button == CAT_BTN_B) { ui_pop(); return 1; }
    if (it && !it->head) {
        int slot = ev->button == CAT_BTN_A ? 0 : ev->button == CAT_BTN_X ? 1 : ev->button == CAT_BTN_Y ? 2 : -1;
        if (slot >= 0 && it->fn[slot]) it->fn[slot](s, it);
    }
    return 1;
}

/* ============================================================ vòng lặp chính */
static void draw_busy(void) {
    cat_theme *th = cat_get_theme();
    int sw = cat_get_screen_width(), sh = cat_get_screen_height();
    cat_draw_rect(0, 0, sw, sh, (cat_draw_color){ 0, 0, 0, 120 });
    TTF_Font *ft = cat_get_font(CAT_FONT_LARGE), *fs = cat_get_font(CAT_FONT_TINY);
    char msg[160], note[260];
    int pct = -1;
    snprintf(note, sizeof note, tr("Vui lòng chờ, đừng tắt máy. Tác vụ dài có thể mất vài phút."));
    snprintf(msg, sizeof msg, "%s... (%us)", T.label, (SDL_GetTicks() - T.t0) / 1000u);
    /* tiến độ do CLI báo trên stderr: dòng "LMPROGRESS <phase> <done> <total> <i> <n>\t<tên>" (dòng cuối ĐÃ kết thúc bằng \n) */
    const char *e = lm_job_stderr(T.job), *last = NULL, *q = e;
    while ((q = strstr(q, "LMPROGRESS ")) != NULL) { if (strchr(q, '\n')) last = q; q += 11; }
    char phase[16] = "", nm[120] = "";
    double done = 0, total = 0;
    int idx = 0, cnt = 0;
    if (last && sscanf(last, "LMPROGRESS %15s %lf %lf %d %d", phase, &done, &total, &idx, &cnt) == 5 && total > 0) {
        const char *t = strchr(last, '\t'), *nl = strchr(last, '\n');
        if (t && nl && t < nl) { size_t k = (size_t)(nl - t - 1); if (k >= sizeof nm) k = sizeof nm - 1; memcpy(nm, t + 1, k); nm[k] = 0; }
        pct = (int)(done * 100.0 / total);
        if (pct > 100) pct = 100;
        char a[32], b[32];
        ui_fmt_bytes(a, sizeof a, done);
        ui_fmt_bytes(b, sizeof b, total);
        snprintf(msg, sizeof msg, "%s... %d%% (%us)", T.label, pct, (SDL_GetTicks() - T.t0) / 1000u);
        if (!strcmp(phase, "install")) snprintf(note, sizeof note, tr("Đang cài bằng trình Nhập game (%s)"), b);
        else if (!strcmp(phase, "catalog")) snprintf(note, sizeof note, "Trang %.0f/%.0f — %s", done, total, nm);
        else snprintf(note, sizeof note, "%s / %s — %s %d/%d: %s", a, b, !strcmp(phase, "verify") ? tr("kiểm hash") : "file", idx, cnt, nm);
    }
    const char *note2 = T.cancelable ? (T.cancel_req ? tr("Đang tạm dừng...") : tr("B: tạm dừng (tải tiếp được sau). Đừng tắt máy.")) : NULL;
    int pad = cat_device_scale(14), barh = pct >= 0 ? cat_device_scale(10) : 0, g6 = cat_device_scale(6);
    int w = cat_measure_text(ft, msg), w2 = cat_measure_text(fs, note);
    w = (w > w2 ? w : w2) + 2 * pad;
    if (pct >= 0 || note2) { int mw = sw * 3 / 4; if (w < mw) w = mw; }
    if (w > sw - 20) w = sw - 20;
    int lh2 = TTF_FontHeight(fs);
    int h = TTF_FontHeight(ft) + lh2 + 2 * pad + g6 + (barh ? barh + g6 : 0) + (note2 ? lh2 + g6 : 0);
    int x = (sw - w) / 2, y = (sh - h) / 2, ty = y + pad;
    cat_draw_rounded_rect(x, y, w, h, cat_device_scale(10), th->highlight);
    cat_draw_rounded_rect(x + 3, y + 3, w - 6, h - 6, cat_device_scale(10), lift(th->background, 14));
    cat_draw_text_ellipsized(ft, msg, x + pad, ty, th->text, w - 2 * pad);
    ty += TTF_FontHeight(ft) + g6;
    if (barh) {
        cat_draw_rect(x + pad, ty, w - 2 * pad, barh, lift(th->background, -22));
        cat_draw_rect(x + pad, ty, (w - 2 * pad) * pct / 100, barh, th->highlight);
        ty += barh + g6;
    }
    cat_draw_text_ellipsized(fs, note, x + pad, ty, th->hint, w - 2 * pad);
    if (note2) cat_draw_text_ellipsized(fs, note2, x + pad, ty + lh2 + g6, th->hint, w - 2 * pad);
}

/* Dòng ghi công: một dải mỏng ngay trên thanh nút, chung cho mọi màn (render() trừ chiều cao dải khỏi vùng nội dung). */
#define LM_CREDIT "DEVELOPED BY BACHTD"
static int credit_h(void) {
    TTF_Font *f = cat_get_font(CAT_FONT_MICRO);
    return f ? TTF_FontHeight(f) + cat_device_scale(2) : 0;
}
static void draw_credit(void) {
    TTF_Font *f = cat_get_font(CAT_FONT_MICRO);
    if (!f) return;
    int y = cat_get_screen_height() - cat_get_footer_height() - credit_h();
    cat_draw_text(f, LM_CREDIT, (cat_get_screen_width() - cat_measure_text(f, LM_CREDIT)) / 2, y, cat_get_theme()->hint);
}

static void render(ui_screen *s) {
    cat_clear_screen();
    cat_status_bar_opts sb = { .show_clock = CAT_CLOCK_SHOW, .use_24h = true, .show_battery = true, .show_wifi = true };
    cat_draw_screen_title(tr(s->title), &sb);
    cat_draw_status_bar(&sb);   /* cat_draw_screen_title không tự vẽ status bar dù tài liệu nói có */
    SDL_Rect c = cat_get_content_rect(true, true, true);
    int inset = cat_device_scale(5);
    c.x += inset; c.w -= 2 * inset;
    c.h -= credit_h();
    draw_credit();
    if (s->draw) s->draw(s, c);
    else draw_list(s, c);

    cat_footer_item f[6];
    int nf = 0;
    if (D.open) {
        if (D.confirm) {
            f[nf++] = (cat_footer_item){ .button = CAT_BTN_B, .label = tr("HUỶ") };
            f[nf++] = (cat_footer_item){ .button = CAT_BTN_A, .label = tr("CHỌN"), .is_confirm = true };
        } else f[nf++] = (cat_footer_item){ .button = CAT_BTN_A, .label = tr("ĐÓNG"), .is_confirm = true };
    } else if (s->draw && s->nfooter) {
        for (int i = 0; i < s->nfooter && nf < 6; i++) { f[nf] = s->footer[i]; f[nf].label = tr(f[nf].label); nf++; }
    } else {
        ui_item *it = s->n ? s->items[s->sel] : NULL;
        f[nf++] = (cat_footer_item){ .button = CAT_BTN_B, .label = tr("QUAY LẠI") };
        if (it && !it->head) {
            if (it->fn[2] && it->lbl[2]) f[nf++] = (cat_footer_item){ .button = CAT_BTN_Y, .label = tr(it->lbl[2]) };
            if (it->fn[1] && it->lbl[1]) f[nf++] = (cat_footer_item){ .button = CAT_BTN_X, .label = tr(it->lbl[1]) };
            if (it->fn[0] && it->lbl[0]) f[nf++] = (cat_footer_item){ .button = CAT_BTN_A, .label = tr(it->lbl[0]), .is_confirm = true };
        }
    }
    if (D.open) dialog_draw();
    if (T.job) draw_busy();
    if (!T.job) cat_draw_footer(f, nf);
}

int ui_run(lm_harness *h, ui_screen *home) {
    int rc = 0;
    g_stack[0] = home;
    g_depth = 1;
    if (home->build) ui_rebuild(home);
    while (!g_quit_req) {
        cat_input_event ev;
        while (lm_harness_poll(h, &ev)) {
            if (!ev.pressed) continue;
            if (ev.button == CAT_BTN_START) ev.button = CAT_BTN_A;   /* như bản Python: Start = A, Menu = B */
            else if (ev.button == CAT_BTN_MENU) ev.button = CAT_BTN_B;
            if (T.job) {                /* đang chạy tác vụ: nuốt mọi phím (chống bấm đúp / chạy chồng); trừ B khi tác vụ cho phép tạm dừng */
                if (T.cancelable && !T.cancel_req && ev.button == CAT_BTN_B && !ev.repeated) { T.cancel_req = 1; lm_job_kill(T.job); }
                continue;
            }
            if (D.open) { dialog_key(&ev); continue; }
            ui_screen *s = ui_top();
            if (s->key_fn && s->key_fn(s, &ev)) continue;
            if (!s->draw) list_key(s, &ev);
        }
        int busy = task_pump();
        ui_screen *s = ui_top();
        render(s);
        if (busy || lm_harness_active(h)) cat_request_frame();
        if (lm_harness_frame_end(h, busy)) { rc = h->timed_out ? 1 : 0; break; }
        cat_present();
        if (busy) SDL_Delay(30);
    }
    return rc;
}
