/* LoongMate native — Home, Máy, Thẻ nhớ, SaveVault, gói hỗ trợ; điểm vào chương trình.
 * Mọi số liệu và mọi hành động đi qua engine Python (cli.py --json) bằng fork+execv (lm_run).
 * Native không tự đọc/ghi/băm/xoá file người dùng. Hành vi bám engine/ui_screens.py.
 *
 *   lm-home [--pak DIR] [--python PATH] [--userdata DIR] [--primary DIR] [--secondary DIR]
 *           [--screenshot out.ppm] [--keys DOWN,A,SHOT,...]    (hai cờ cuối chỉ để kiểm thử offscreen)
 */
#define _GNU_SOURCE
#include "lm_app.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

cJSON *g_ready;
cJSON *g_snaps;
char g_ready_err[300];
static cJSON *g_health;
static char g_health_err[300];

/* ============================================================ tiện ích dùng chung */
void app_scalar(const cJSON *v, char *buf, size_t n) {
    if (!v || cJSON_IsNull(v)) snprintf(buf, n, "None");
    else if (cJSON_IsString(v)) snprintf(buf, n, "%s", v->valuestring ? v->valuestring : "");
    else if (cJSON_IsBool(v)) snprintf(buf, n, "%s", cJSON_IsTrue(v) ? "True" : "False");
    else if (cJSON_IsNumber(v)) {
        double d = v->valuedouble;
        if (d == (long long)d && d < 1e15 && d > -1e15) snprintf(buf, n, "%lld", (long long)d);
        else snprintf(buf, n, "%g", d);
    } else {
        char *t = cJSON_PrintUnformatted(v);
        snprintf(buf, n, "%s", t ? t : "");
        free(t);
    }
}

cJSON *app_json_or_error(lm_job *j, const char *what) {
    char err[400];
    cJSON *r = ui_job_json(j, err, sizeof err);
    if (!r) {
        lines_t L = {0};
        lines_add(&L, "!! Không đọc được kết quả của engine:");
        lines_add(&L, "%s", err);
        ui_info(what, &L);
    }
    return r;
}

void app_add_rescan_lines(lines_t *L, const cJSON *rs) {
    if (!cJSON_IsObject(rs)) return;
    const char *msg = jstrd(rs, "message", "");
    int attempted = jbool_d(rs, "attempted", 0);
    cJSON *okv = jget(rs, "ok");
    if (attempted && cJSON_IsTrue(okv)) {
        double w = jnumd(rs, "waited_s", -1);
        if (w >= 0) lines_add(L, "+ Đã cập nhật thư viện Leaf: %s (%gs)", msg, w);
        else lines_add(L, "+ Đã cập nhật thư viện Leaf: %s", msg);
    } else if (attempted) {
        lines_add(L, "!! Tự cập nhật thư viện Leaf thất bại: %s", msg);
        lines_add(L, "Vào System > Rescan Library trên máy để Leaf cập nhật danh sách game.");
    } else {
        lines_add(L, "%s", msg);   /* CLI đã gồm lời nhắc bấm tay và lý do thật */
    }
}


/* ============================================================ kiểm tra toàn vẹn kho, kế hoạch đã duyệt */
#define VERIFY_STALE_DAYS 14   /* như VERIFY_STALE_DAYS của engine/ui_screens.py */

void app_vv_eval(const cJSON *v, vv_t *o) {
    memset(o, 0, sizeof *o);
    cJSON *ok = jget(v, "ok");
    if (!cJSON_IsBool(ok)) return;                     /* chưa kiểm bao giờ / dữ liệu không dùng được -> level 0, KHÔNG cảnh báo */
    cJSON *bad = jarr(v, "bad_objects");
    o->nbad = bad ? cJSON_GetArraySize(bad) : 0;
    double m = jnumd(v, "missing_objects", 0);
    o->nmissing = m > 0 ? (int)m : 0;
    o->count = o->nbad + o->nmissing;
    o->partial = jbool_d(v, "partial", 0);
    o->has_checked = jnum(v, "checked", &o->checked);
    if (jnum(v, "ts", &o->ts)) {
        double d = ((double)time(NULL) - o->ts) / 86400.0;
        o->age_days = d > 0 ? (int)d : 0;
        o->has_age = 1;
    }
    if (!cJSON_IsTrue(ok)) o->level = 3;
    else if (o->has_age && o->age_days > VERIFY_STALE_DAYS) o->level = 2;
    else o->level = 1;
}

void app_vv_line(const vv_t *v, char *buf, size_t n) {
    if (v->level == 3) snprintf(buf, n, "!! Kiểm tra toàn vẹn: %d object hỏng/thiếu", v->count);
    else if (v->level == 2) snprintf(buf, n, "! Kiểm tra toàn vẹn đã %d ngày chưa làm lại", v->age_days);
    else if (v->level == 1) {
        if (!v->has_age) snprintf(buf, n, "+ Toàn vẹn kho: đã kiểm");
        else if (v->age_days == 0) snprintf(buf, n, "+ Toàn vẹn kho: đã kiểm (hôm nay)");
        else snprintf(buf, n, "+ Toàn vẹn kho: đã kiểm (%d ngày trước)", v->age_days);
    } else buf[0] = 0;
}

int app_plan_rejected(lm_job *j, const cJSON *r, const char *title) {
    int code = lm_job_exit_code(j);
    if (code != 3 && jarr(r, "applied")) return 0;      /* 0 = xong hết, 1 = có mục bị từ chối: cả hai đều có "applied" */
    lines_t L = {0};
    if (code == 3) {
        lines_add(&L, "!! Kế hoạch đã cũ hoặc không hợp lệ — CHƯA làm gì cả.");
        lines_add(&L, "Hãy xem trước lại để lập kế hoạch mới.");
        lines_add(&L, "%s", "");
        lines_add(&L, "Lý do engine: %s", jstrd(r, "message", "không nêu lý do"));
    } else {
        lines_add(&L, "!! Chưa thực hiện được:");
        lines_add(&L, "%s", jstrd(r, "message", "engine không trả kết quả thực hiện"));
    }
    ui_info(title, &L);
    return 1;
}

int app_plan_written(const cJSON *r, const char *kind, const char *title) {
    const char *id = jstr(r, "plan_id"), *path = jstr(r, "plan_path");
    if (id && id[0] && path && !strcmp(path, plan_file(kind))) return 1;
    ui_info1(title, "!! Engine không ghi file kế hoạch (không có plan_id/plan_path đúng). Không thể thực hiện đúng danh sách đã xem, nên không làm gì cả. Có thể engine trên máy là bản cũ chưa hỗ trợ --plan-out.");
    return 0;
}

/* ============================================================ readiness toàn cục */
static void (*g_then)(void);

static void run_then(void) {
    void (*then)(void) = g_then;
    g_then = NULL;
    if (then) then();
}

/* `snapshots --json`: danh sách đầy đủ + vault_verify. Lỗi (engine cũ) thì không có cảnh báo toàn vẹn, không hiện hộp. */
static void on_snaps(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = cJSON_Parse(lm_job_stdout(j));
    cJSON_Delete(g_snaps);
    g_snaps = (cJSON_IsObject(r) && jarr(r, "snapshots")) ? r : NULL;
    if (!g_snaps) cJSON_Delete(r);
    ui_rebuild_all();
    run_then();
}

static void on_readiness(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = cJSON_Parse(lm_job_stdout(j));      /* cli in JSON cả khi thiếu thẻ chính (mã thoát khác 0) */
    if (cJSON_IsObject(r)) {
        cJSON_Delete(g_ready);
        g_ready = r;
        g_ready_err[0] = 0;
    } else {
        cJSON_Delete(r);
        char why[260];
        ui_job_failure(j, why, sizeof why);
        cJSON_Delete(g_ready);
        g_ready = NULL;
        snprintf(g_ready_err, sizeof g_ready_err, "JSON không hợp lệ — %s", why);
    }
    ui_rebuild_all();
    if (g_ready && !jbool_d(g_ready, "primary_missing", 0) &&
        cli_task("Đang đọc danh sách snapshot", SHORT_TIMEOUT_MS, on_snaps, "", "snapshots", NULL))
        return;
    cJSON_Delete(g_snaps);
    g_snaps = NULL;
    run_then();
}

void app_refresh_then(void (*then)(void)) {
    g_then = then;
    if (!cli_task("Đang đọc dữ liệu từ engine", SHORT_TIMEOUT_MS, on_readiness, "", "readiness", NULL)) g_then = NULL;
}
void app_refresh(void) { app_refresh_then(NULL); }

static int need_ready(void) {
    if (g_ready) return 1;
    ui_info1("Lỗi", "%s", g_ready_err[0] ? g_ready_err : "Engine không khả dụng.");
    return 0;
}

/* ============================================================ Home */
#define MAX_LINES 8
#define LINE_LEN 200
typedef struct {
    char title[32];
    char lines[MAX_LINES][LINE_LEN];
    unsigned char ell[MAX_LINES];   /* 1 = một hàng, cắt "..." thay vì xuống dòng (id dài không có khoảng trắng) */
    int n;
    status_t status;
} card_t;

static void addf(card_t *c, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void addf(card_t *c, const char *fmt, ...) {
    if (c->n >= MAX_LINES) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(c->lines[c->n++], LINE_LEN, fmt, ap);
    va_end(ap);
}

static void add_warnings(card_t *c, const cJSON *parent, int max) {
    cJSON *it;
    int k = 0;
    cJSON_ArrayForEach(it, jget(parent, "warnings")) {
        if (k >= max) break;
        if (cJSON_IsString(it) && it->valuestring) { addf(c, "! %s", it->valuestring); k++; }
    }
}

static void add_card_storage(card_t *c, const char *label, const cJSON *s) {
    double fr, tot;
    int mounted = 0, wr = 0;
    jbool(s, "mounted", &mounted);
    if (!cJSON_IsObject(s) || !mounted || !jnum(s, "free_mb", &fr) || !jnum(s, "total_mb", &tot)) {
        addf(c, "%s: %s", label, cJSON_IsObject(s) ? "không đọc được" : "chưa gắn thẻ");
        return;
    }
    char a[24], b[24];
    ui_fmt_mb(a, sizeof a, fr);
    ui_fmt_mb(b, sizeof b, tot);
    int fsd = 1;
    jbool(s, "fs_detected", &fsd);
    const char *extra = "";
    if (jbool(s, "writable", &wr) && !wr) extra = " | chỉ đọc";
    else if (!fsd) extra = " | chưa xác minh FS";
    addf(c, "%s: %s trống / %s%s", label, a, b, extra);
}

static int color_rank(status_t s) { return s == ST_GREEN ? 0 : s == ST_UNKNOWN ? 1 : s == ST_YELLOW ? 2 : 3; }
static status_t worse_status(status_t a, status_t b) { return color_rank(a) >= color_rank(b) ? a : b; }

/* Dựng 4 thẻ — cùng quy tắc với HomeScreen.cards() của bản Python. */
static void build_cards(card_t out[4]) {
    memset(out, 0, 4 * sizeof(card_t));
    snprintf(out[0].title, 32, "1) MÁY");
    snprintf(out[1].title, 32, "2) THẺ NHỚ");
    snprintf(out[2].title, 32, "3) SAVE");
    snprintf(out[3].title, 32, "4) LIBRARY");
    if (!g_ready) {
        for (int i = 0; i < 4; i++) {
            out[i].status = ST_UNKNOWN;
            addf(&out[i], "Không đọc được dữ liệu.");
            addf(&out[i], "%s", g_ready_err[0] ? g_ready_err : "Đang đọc...");
        }
        return;
    }
    cJSON *dev = jget(g_ready, "device"), *st = jget(g_ready, "storage");
    cJSON *bat = jget(dev, "battery"), *th = jget(dev, "thermal");
    card_t *c = &out[0];
    c->status = ui_parse_status(jstr(dev, "status"));
    double v;
    int chg = 0, bav = 0, tav = 0;
    jbool(bat, "available", &bav);
    jbool(th, "available", &tav);
    if (bav && jnum(bat, "percent", &v)) {
        jbool(bat, "is_charging", &chg);
        addf(c, "Pin: %.0f%% (%s)", v, chg ? "đang sạc" : "không sạc");
    } else addf(c, "Pin: không đọc được");
    if (tav && jnum(th, "current_celsius", &v)) {
        const char *tr = jstr(th, "trend");
        addf(c, "Nhiệt độ: %.0f°C%s%s%s", v, tr ? " (" : "", tr ? tr : "", tr ? ")" : "");
    } else addf(c, "Nhiệt độ: không đọc được");
    add_warnings(c, dev, 2);

    c = &out[1];
    c->status = ui_parse_status(jstr(st, "status"));
    add_card_storage(c, "SD1", jget(st, "primary"));
    add_card_storage(c, "SD2", jget(st, "secondary"));
    add_warnings(c, st, 1);

    c = &out[2];
    cJSON *sv = jget(g_ready, "savevault");
    cJSON *rec = jget(g_ready, "crash_recovery");
    int primary_missing = 0;
    jbool(g_ready, "primary_missing", &primary_missing);
    double cnt;
    if (primary_missing || !jnum(sv, "snapshot_count", &cnt)) {
        c->status = ST_UNKNOWN;
        addf(c, "SaveVault không khả dụng.");
        if (primary_missing) addf(c, "Không thấy thẻ chính.");
    } else if (cJSON_IsObject(rec)) {
        c->status = ST_RED;
        addf(c, "Phát hiện phục hồi dở dang!");
        addf(c, "Mở giao diện chi tiết (A) để rollback.");
    } else if (cnt < 1) {
        c->status = ST_YELLOW;
        addf(c, "Chưa có snapshot nào.");
        addf(c, "Nhấn X để sao lưu ngay.");
    } else {
        const char *id = jstr(sv, "latest_snapshot_id"), *at = jstr(sv, "latest_snapshot_at");
        c->status = ST_GREEN;
        addf(c, "%.0f snapshot, mới nhất:", cnt);
        addf(c, "%s", id ? id : "không rõ");
        c->ell[c->n - 1] = 1;
        addf(c, "Lúc: %s", at ? at : "không rõ");
    }
    vv_t vv;
    app_vv_eval(jget(g_snaps, "vault_verify"), &vv);
    int have_vv = !primary_missing && jnum(sv, "snapshot_count", &cnt) && vv.level;
    char vln[LINE_LEN];
    app_vv_line(&vv, vln, sizeof vln);
    /* kiểm tra toàn vẹn kho (agent ghi): hỏng/thiếu -> đỏ, cũ > 14 ngày -> vàng (đặt TRƯỚC dòng bản sao ngoài máy để không bị cắt);
     * đã kiểm và ổn -> dòng xanh đặt sau cùng; chưa kiểm bao giờ -> KHÔNG thêm gì */
    if (have_vv && vv.level >= 2) {
        addf(c, "%s", vln);
        c->status = worse_status(c->status, vv.level == 3 ? ST_RED : ST_YELLOW);
    }
    {   /* bản sao ngoài máy (engine chấm mức theo tuổi lần kéo về PC): dòng riêng + viền lấy mức xấu hơn */
        cJSON *off = jget(g_ready, "offsite");
        if (!primary_missing && jnum(sv, "snapshot_count", &cnt) && cJSON_IsObject(off) && jstr(off, "line")) {
            addf(c, "%s", jstr(off, "line"));
            c->status = worse_status(c->status, ui_parse_status(jstr(off, "color")));
        }
    }
    if (have_vv && vv.level == 1) addf(c, "%s", vln);

    c = &out[3];
    cJSON *lib = jget(g_ready, "library");
    int lav = 1;
    jbool(lib, "available", &lav);   /* chỉ nhánh "chưa quét" của cli mới có available=false */
    double total;
    if (!lav || !jnum(lib, "total_issues", &total)) {
        c->status = ST_UNKNOWN;
        addf(c, "Chưa quét lần nào.");
        addf(c, "Mở giao diện chi tiết (A) rồi chọn Quét lại.");
    } else {
        cJSON *sev = jget(lib, "severity_counts");
        double cr = 0, wa = 0, inf = 0;
        jnum(sev, "critical", &cr);
        jnum(sev, "warning", &wa);
        jnum(sev, "info", &inf);
        const char *gen = jstr(lib, "generated_at");
        double fx = jnumd(lib, "fixable", -1);
        if (cr > 0) addf(c, "Nghiêm trọng: %.0f", cr);
        addf(c, wa > 0 ? "Cần xử lý: %.0f" : "Không có lỗi cần xử lý", wa);
        if (fx > 0) addf(c, "Sửa được ngay: %.0f", fx);
        addf(c, "Ghi chú: %.0f (không phải lỗi)", inf);
        addf(c, "Quét lúc: %s", gen ? gen : "không rõ");
        c->ell[c->n - 1] = 1;
        c->status = cr > 0 ? ST_RED : (wa > 0 ? ST_YELLOW : ST_GREEN);
    }
}

static uint8_t clamp8(int v) { return (uint8_t)(v > 255 ? 255 : v < 0 ? 0 : v); }
static cat_draw_color lift(cat_draw_color c, int d) {
    return (cat_draw_color){ clamp8(c.r + d), clamp8(c.g + d), clamp8(c.b + d), 255 };
}

static void draw_card(const card_t *c, int x, int y, int w, int h, int focused) {
    cat_theme *th = cat_get_theme();
    int pad = cat_device_scale(8), r = cat_device_scale(8);
    int bd = focused ? cat_device_scale(3) : cat_device_scale(2);
    cat_draw_color edge = focused ? th->highlight : ui_color(c->status);
    cat_draw_rounded_rect(x, y, w, h, r, edge);
    cat_draw_rounded_rect(x + bd, y + bd, w - 2 * bd, h - 2 * bd, r > bd ? r - bd : 0, lift(th->background, 18));
    TTF_Font *ft = cat_get_font(CAT_FONT_LARGE), *fb = cat_get_font(CAT_FONT_SMALL);
    int tx = x + bd + pad, tw = w - 2 * (bd + pad);
    int ty = y + bd + pad;
    {   /* chữ cỡ SMALL nếu vừa thẻ, không thì hạ xuống TINY để không mất dòng */
        int avail = h - 2 * (bd + pad) - TTF_FontHeight(ft) - cat_device_scale(4), need = 0;
        for (int i = 0; i < c->n; i++)
            need += (c->ell[i] ? TTF_FontHeight(fb) : cat_measure_wrapped_text_height(fb, c->lines[i], tw)) + cat_device_scale(2);
        if (need > avail && cat_get_font(CAT_FONT_TINY)) fb = cat_get_font(CAT_FONT_TINY);
    }
    int dot = cat_device_scale(6), gapx = cat_device_scale(6);
    cat_draw_circle(tx + dot, ty + TTF_FontHeight(ft) / 2, dot, ui_color(c->status));   /* chấm = status thật của engine */
    cat_draw_text_ellipsized(ft, c->title, tx + 2 * dot + gapx, ty, th->text, tw - 2 * dot - gapx);
    ty += TTF_FontHeight(ft) + cat_device_scale(4);
    int bottom = y + h - bd - pad;
    for (int i = 0; i < c->n; i++) {
        int hh = c->ell[i] ? TTF_FontHeight(fb) : cat_measure_wrapped_text_height(fb, c->lines[i], tw);
        if (ty + hh > bottom) { cat_draw_text(fb, "...", tx, ty, th->hint); break; }
        cat_draw_color col = (c->lines[i][0] == '!' || c->lines[i][0] == '+') ? ui_line_color(c->lines[i]) : th->hint;
        if (c->ell[i]) {
            cat_draw_text_ellipsized(fb, c->lines[i], tx, ty, col, tw);
            ty += hh;
        } else {
            ty += cat_draw_text_wrapped(fb, c->lines[i], tx, ty, tw, col, CAT_ALIGN_LEFT);
        }
        ty += cat_device_scale(2);
    }
}

static int g_home_sel;   /* 0..3 = hàng*2 + cột như bản Python */

static void home_draw(ui_screen *s, SDL_Rect c) {
    (void)s;
    card_t cards[4];
    build_cards(cards);
    int gap = cat_device_scale(6);
    int cw = (c.w - gap) / 2, ch = (c.h - gap) / 2;
    for (int i = 0; i < 4; i++)
        draw_card(&cards[i], c.x + (i % 2) * (cw + gap), c.y + (i / 2) * (ch + gap), cw, ch, i == g_home_sel);
}

/* ============================================================ SaveVault: sao lưu / xác minh / xem trước / khôi phục / rollback */
static void backup_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Sao lưu SaveVault");
    if (!r) return;
    lines_t L = {0};
    if (jbool_d(r, "success", 0)) {
        char b[32], tail[80];
        lines_add(&L, "+ Sao lưu thành công.");
        lines_add(&L, "Snapshot: %s", jstrd(r, "snapshot_id", "không rõ"));
        char n[32];
        app_scalar(jget(r, "files_count"), n, sizeof n);
        lines_add(&L, "Số file: %s", n);
        ui_fmt_bytes(b, sizeof b, jnumd(r, "total_bytes", 0));
        lines_add(&L, "Dung lượng: %s", b);
        const char *path = jstr(r, "path");
        if (path) { ui_tail(tail, sizeof tail, path, 60); lines_add(&L, "Vị trí: %s", tail); }
    } else {
        lines_add(&L, "!! Sao lưu thất bại:");
        lines_add(&L, "%s", jstrd(r, "message", "không rõ nguyên nhân"));
    }
    cJSON_Delete(r);
    ui_info("Sao lưu SaveVault", &L);
    app_refresh();
}

static void do_backup(void) { cli_task("Đang sao lưu", LONG_TIMEOUT_MS, backup_done, "", "backup", NULL); }
static void act_backup(ui_screen *s, ui_item *it) { (void)s; (void)it; do_backup(); }

static void verify_done(lm_job *j, const char *sid) {
    cJSON *r = app_json_or_error(j, "Xác minh snapshot");
    if (!r) return;
    lines_t L = {0};
    char n[32];
    app_scalar(jget(r, "checked"), n, sizeof n);
    if (jstr(r, "error") && !jget(r, "ok")) {
        lines_add(&L, "!! %s", jstrd(r, "message", jstr(r, "error")));
    } else if (jbool_d(r, "ok", 0)) {
        lines_add(&L, "+ Snapshot %s HỢP LỆ.", sid);
        lines_add(&L, "Đã kiểm tra %s file.", n);
    } else {
        lines_add(&L, "!! Snapshot %s KHÔNG hợp lệ.", sid);
        lines_add(&L, "Đã kiểm tra: %s file", n);
        static const char *const keys[] = { "error", "message", "missing", "size_mismatch", "hash_mismatch" };
        for (int i = 0; i < 5; i++) {
            cJSON *v = jget(r, keys[i]);
            if (!v || cJSON_IsNull(v) || (cJSON_IsArray(v) && cJSON_GetArraySize(v) == 0)) continue;
            char t[600];
            app_scalar(v, t, sizeof t);
            lines_add(&L, "%s: %s", keys[i], t);
        }
    }
    cJSON_Delete(r);
    ui_info("Xác minh snapshot", &L);
}

static void act_verify(ui_screen *s, ui_item *it) {
    (void)s;
    cli_task("Đang xác minh", LONG_TIMEOUT_MS, verify_done, it->arg, "verify", "--snapshot", it->arg, NULL);
}

/* trả 1 nếu có kế hoạch; counts = {thêm, cập nhật, xung đột, không đổi}. L nhận danh sách dòng chi tiết */
static int preview_lines(const cJSON *p, const char *sid, lines_t *L, int counts[4]) {
    if (!jarr(p, "to_add") || !jarr(p, "to_update") || !jarr(p, "unchanged")) {
        lines_add(L, "!! %s", jstrd(p, "message", jstrd(p, "error", "Không có kế hoạch khôi phục từ engine.")));
        return 0;
    }
    cJSON *conf = jarr(p, "conflicts");
    counts[0] = jarr_n(p, "to_add"); counts[1] = jarr_n(p, "to_update");
    counts[2] = conf ? cJSON_GetArraySize(conf) : 0; counts[3] = jarr_n(p, "unchanged");
    lines_add(L, "Snapshot %s", sid);
    lines_add(L, "Thêm mới: %d file", counts[0]);
    lines_add(L, "Cập nhật: %d file", counts[1]);
    lines_add(L, "Xung đột (giữ cả hai): %d file", counts[2]);
    lines_add(L, "Không đổi: %d file", counts[3]);
    lines_add(L, "%s", "");
    cJSON *it;
    cJSON_ArrayForEach(it, jarr(p, "to_add")) if (cJSON_IsString(it)) lines_add(L, "+ [THÊM] %s", it->valuestring);
    cJSON_ArrayForEach(it, jarr(p, "to_update")) if (cJSON_IsString(it)) lines_add(L, "[CẬP NHẬT] %s", it->valuestring);
    cJSON_ArrayForEach(it, conf) {
        const char *rp = cJSON_IsObject(it) ? jstr(it, "rel_path") : (cJSON_IsString(it) ? it->valuestring : NULL);
        lines_add(L, "! [XUNG ĐỘT] %s", rp ? rp : "?");
    }
    return 1;
}

static void preview_done(lm_job *j, const char *sid) {
    cJSON *p = app_json_or_error(j, "Xem trước khôi phục");
    if (!p) return;
    lines_t L = {0};
    int c[4];
    preview_lines(p, sid, &L, c);
    cJSON_Delete(p);
    ui_info("Xem trước khôi phục (chưa ghi gì)", &L);
}

static void act_preview(ui_screen *s, ui_item *it) {
    (void)s;
    cli_task("Đang so sánh", LONG_TIMEOUT_MS, preview_done, it->arg, "restore", "--snapshot", it->arg, "--dry-run", NULL);
}

static void restore_done(lm_job *j, const char *sid) {
    (void)sid;
    cJSON *r = app_json_or_error(j, "Kết quả khôi phục");
    if (!r) return;
    lines_t L = {0};
    if (jbool_d(r, "success", 0)) {
        char a[300];
        lines_add(&L, "+ Khôi phục xong.");
        app_scalar(jget(r, "restored_files_count"), a, sizeof a);
        lines_add(&L, "Số file đã khôi phục: %s", a);
        lines_add(&L, "Pre-restore snapshot (đường lùi): %s", jstrd(r, "pre_restore_id", "None"));
        char pc[300], cd[400] = "";
        app_scalar(jget(r, "preserved_conflicts"), pc, sizeof pc);
        cJSON *pcv = jget(r, "preserved_conflicts");
        int has_pc = cJSON_IsArray(pcv) ? cJSON_GetArraySize(pcv) > 0 : jnumd(r, "preserved_conflicts", 0) > 0;
        if (has_pc && jstr(r, "conflicts_dir")) snprintf(cd, sizeof cd, " (trong %s)", jstr(r, "conflicts_dir"));
        lines_add(&L, "Xung đột được bảo toàn: %s%s", pc, cd);
        app_scalar(jget(r, "rejected_paths"), a, sizeof a);
        lines_add(&L, "Path bị từ chối: %s", a);
        app_scalar(jget(r, "skipped_readonly"), a, sizeof a);
        lines_add(&L, "Bỏ qua do read-only/gần đầy: %s", a);
    } else {
        lines_add(&L, "!! Khôi phục thất bại:");
        lines_add(&L, "%s", jstrd(r, "message", "không rõ"));
        lines_add(&L, "Nếu đã ghi dở, mở lại SaveVault: sẽ có mục Rollback.");
    }
    cJSON_Delete(r);
    ui_info("Kết quả khôi phục", &L);
    app_refresh();
}

static void do_restore(const char *sid) {
    /* --yes chỉ được truyền SAU khi người dùng đã xác nhận trong hộp thoại */
    cli_task("Đang khôi phục", LONG_TIMEOUT_MS, restore_done, sid, "restore", "--snapshot", sid, "--yes", NULL);
}

static void restore_plan_done(lm_job *j, const char *sid) {
    cJSON *p = app_json_or_error(j, "Xác nhận khôi phục");
    if (!p) return;
    lines_t det = {0};
    int c[4] = {0};
    if (!preview_lines(p, sid, &det, c)) {
        cJSON_Delete(p);
        ui_info("Không thể khôi phục", &det);
        return;
    }
    cJSON_Delete(p);
    lines_t L = {0};
    lines_add(&L, "!! KHÔI PHỤC sẽ ghi đè Saves/States đang chơi bằng nội dung snapshot.");
    lines_add(&L, "• LoongMate TỰ TẠO pre-restore snapshot trước khi ghi (đường lùi).");
    lines_add(&L, "• File xung đột: bản đang chơi được CẤT vào SaveVault/conflicts/, không bị mất.");
    lines_add(&L, "• So với Saves hiện tại: thêm %d, cập nhật %d, xung đột (giữ cả hai) %d, không đổi %d.", c[0], c[1], c[2], c[3]);
    lines_add(&L, "• Engine khôi phục toàn bộ file của snapshot nên file 'không đổi' cũng có thể được ghi lại cùng nội dung.");
    lines_add(&L, "• Bản đang chơi không có trong snapshot sẽ được giữ nguyên.");
    lines_add(&L, "%s", "");
    for (int i = 1; i < det.n; i++) lines_add(&L, "%s", det.v[i]);
    lines_free(&det);
    ui_confirm("Xác nhận khôi phục", &L, "Khôi phục", do_restore, sid);
}

static void act_restore(ui_screen *s, ui_item *it) {
    (void)s;
    cli_task("Đang tính kế hoạch", LONG_TIMEOUT_MS, restore_plan_done, it->arg, "restore", "--snapshot", it->arg, "--dry-run", NULL);
}

static void rollback_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả rollback");
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "%s%s", jbool_d(r, "success", 0) ? "+ " : "!! ", jstrd(r, "message", "không rõ"));
    cJSON_Delete(r);
    ui_info("Kết quả rollback", &L);
    app_refresh();
}

static void do_rollback(const char *arg) {
    (void)arg;
    cli_task("Đang rollback", LONG_TIMEOUT_MS, rollback_done, "", "recover", "--rollback", "--yes", NULL);
}

static void act_rollback(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cJSON *rec = jget(g_ready, "crash_recovery");
    if (!cJSON_IsObject(rec)) { ui_info1("Rollback", "Không còn phiên khôi phục dở dang."); return; }
    lines_t L = {0};
    cJSON *jr = jget(rec, "journal");
    lines_add(&L, "!! Rollback sẽ đưa Saves/States về bản pre-restore snapshot %s.", jstrd(jr, "pre_restore_id", "None"));
    lines_add(&L, "Phiên khôi phục dở dang: đã ghi %.0f/%.0f file.", jnumd(rec, "restored_count", 0), jnumd(rec, "planned_count", 0));
    lines_add(&L, "Nội dung ghi dở sau lần khôi phục đó sẽ bị thay bằng bản trước khi khôi phục.");
    ui_confirm("Xác nhận rollback", &L, "Rollback", do_rollback, "");
}

static void build_savevault(ui_screen *s) {
    cJSON *sv = jget(g_ready, "savevault"), *rec = jget(g_ready, "crash_recovery");
    cJSON *snaps = jarr(g_snaps, "snapshots");
    double cnt = snaps ? cJSON_GetArraySize(snaps) : jnumd(sv, "snapshot_count", 0);
    ui_hdr(s, "%.0f snapshot (mới nhất trên cùng).", cnt);
    cJSON *off = jget(g_ready, "offsite");
    if (cJSON_IsObject(off) && jstr(off, "line")) ui_hdr(s, "%s", jstr(off, "line"));
    int has_rec = cJSON_IsObject(rec);
    if (has_rec) ui_hdr(s, "!! Phiên khôi phục trước bị gián đoạn — nên Rollback ngay.");
    if (has_rec) {
        ui_item *it = ui_add(s, "!! Rollback sau mất điện", "CẦN XỬ LÝ", ST_RED);
        ui_exp(it, "!! %s", jstrd(rec, "message", ""));
        ui_exp(it, "Đã lên kế hoạch %.0f file, mới khôi phục %.0f.", jnumd(rec, "planned_count", 0), jnumd(rec, "restored_count", 0));
        if (jbool_d(rec, "can_rollback", 0)) {
            ui_exp(it, "Rollback đưa các file đã ghi dở về trạng thái trước khi khôi phục (từ pre-restore snapshot).");
            ui_act(it, 0, "ROLLBACK", act_rollback);
        } else ui_exp(it, "!! KHÔNG tìm thấy pre-restore snapshot — không rollback tự động được; kiểm tra dữ liệu thủ công.");
    }
    ui_item *it = ui_add(s, "Khôi phục save một game", "", ST_UNKNOWN);
    ui_exp(it, "Chọn MỘT game rồi chọn phiên bản save/state cũ của nó (từ các snapshot) để đưa về.");
    ui_exp(it, "Chỉ file save/state của game đó bị ghi; game khác không bị đụng.");
    ui_exp(it, "Có xem trước và hộp xác nhận (mặc định Huỷ); LoongMate tạo pre-restore snapshot trước khi ghi.");
    ui_act(it, 0, "CHỌN GAME", open_game_saves);
    it = ui_add(s, "Sao lưu ngay", "", ST_UNKNOWN);
    ui_exp(it, "Tạo snapshot mới của Saves/States (cả thẻ SD2 nếu có) và save của emulator standalone.");
    ui_exp(it, "Tăng tiến: file không đổi không bị chép lại; chỉ file đã đổi mới được băm.");
    ui_exp(it, "Ghi .partial > fsync > đổi tên; chỉ hợp lệ khi manifest được ghi cuối cùng.");
    ui_exp(it, "Không sửa gì ở Saves/States hiện tại.");
    ui_act(it, 0, "SAO LƯU", act_backup);
    if (cJSON_IsObject(off)) {
        const char *level = jstrd(off, "level", "missing");
        char tag[40];
        cJSON *age = jget(off, "age_days");
        if (!strcmp(level, "missing")) snprintf(tag, sizeof tag, "chưa có");
        else if (!strcmp(level, "invalid")) snprintf(tag, sizeof tag, "lỗi file");
        else if (!strcmp(level, "future")) snprintf(tag, sizeof tag, "giờ lệch");
        else if (cJSON_IsNumber(age) && age->valuedouble == 0) snprintf(tag, sizeof tag, "hôm nay");
        else if (cJSON_IsNumber(age)) snprintf(tag, sizeof tag, "%.0f ngày", age->valuedouble);
        else snprintf(tag, sizeof tag, "?");
        it = ui_add(s, "Bản sao ngoài máy", tag, ui_parse_status(jstr(off, "color")));
        ui_exp(it, "%s", jstrd(off, "line", jstrd(off, "message", "")));
        ui_exp(it, "Bản sao thứ hai nằm trên PC (không cùng thẻ với save): thẻ hỏng/mất thì vẫn còn.");
        ui_exp(it, "Cảnh báo vàng khi cũ hơn 3 ngày, đỏ khi cũ hơn 14 ngày.");
        if (jstr(off, "last_pull_at")) {
            ui_exp(it, "Lần kéo gần nhất: %s%s%s", jstr(off, "last_pull_at"), jstr(off, "pc_host") ? " bởi " : "", jstr(off, "pc_host") ? jstr(off, "pc_host") : "");
            char ob[40];
            app_scalar(jget(off, "objects"), ob, sizeof ob);
            ui_exp(it, "Số snapshot đã kéo: %d   Số đối tượng: %s", jarr_n(off, "snapshot_ids"), ob);
            ui_exp(it, jbool_d(off, "verified", 0) ? "+ Đã xác minh sau khi kéo." : "!! Lần kéo này CHƯA được xác minh.");
        }
        if (strcmp(level, "ok")) ui_exp(it, "Cài/kiểm tra lịch kéo tự động trên PC: pc/install_schedule.ps1");
    }
    vv_t vv;
    app_vv_eval(jget(g_snaps, "vault_verify"), &vv);
    if (vv.level >= 2) {
        char ln[160];
        app_vv_line(&vv, ln, sizeof ln);
        ui_hdr(s, "%s", ln);
    }
    {
        static const char *const vtag[4] = { "chưa kiểm", "ổn", "cũ", "" };
        char tg[48];
        if (vv.level == 3) snprintf(tg, sizeof tg, "%d hỏng/thiếu", vv.count); else snprintf(tg, sizeof tg, "%s", vtag[vv.level]);
        it = ui_add(s, "Toàn vẹn kho", tg, vv.level == 3 ? ST_RED : vv.level == 2 ? ST_YELLOW : vv.level == 1 ? ST_GREEN : ST_UNKNOWN);
        ui_exp(it, "Agent định kỳ băm lại các object trong kho SaveVault để phát hiện thẻ nhớ hỏng ngầm trước khi bạn cần khôi phục.");
        ui_exp(it, "Cảnh báo vàng khi lần kiểm cũ hơn %d ngày; đỏ khi có object hỏng/thiếu.", VERIFY_STALE_DAYS);
        if (!vv.level) ui_exp(it, "Chưa có kết quả kiểm tra (agent chưa chạy lần kiểm nào, hoặc engine chưa trả) — không phải lỗi.");
        else {
            char ln[160], when[40];
            app_vv_line(&vv, ln, sizeof ln);
            ui_exp(it, "%s", ln);
            if (vv.has_age) {
                time_t t = (time_t)vv.ts;
                strftime(when, sizeof when, "%d/%m/%Y %H:%M", localtime(&t));
                ui_exp(it, "Lần kiểm gần nhất: %s", when);
            }
            if (vv.has_checked) ui_exp(it, "Số object đã kiểm: %.0f", vv.checked);
            ui_exp(it, "Hỏng: %d   Thiếu: %d%s", vv.nbad, vv.nmissing, vv.partial ? "   (kiểm một phần)" : "");
        }
    }
    ui_head(s, "Snapshot (chọn bản bất kỳ)");
    int i = 0;
    cJSON *sn;
    cJSON_ArrayForEach(sn, snaps) {
        const char *id = jstr(sn, "id");
        if (!id) { i++; continue; }
        int autob = jbool_d(sn, "auto", 0);
        char tg[48], b[32];
        ui_fmt_bytes(b, sizeof b, jnumd(sn, "total_size_bytes", 0));
        snprintf(tg, sizeof tg, "%s%s", autob ? "tự động " : "", b);
        int verified = jbool_d(sn, "verified", 0);
        it = ui_add(s, jstrd(sn, "created_at", id), tg, verified ? ST_GREEN : ST_UNKNOWN);
        ui_arg(it, id);
        ui_exp(it, "%s%s", i == 0 ? "Bản mới nhất" : "Bản cũ hơn", autob ? " (tạo tự động)." : ".");
        ui_exp(it, "ID: %s", id);
        ui_exp(it, "Nhãn: %s", jstrd(sn, "label", ""));
        ui_exp(it, "Tạo lúc: %s", jstrd(sn, "created_at", "không rõ"));
        ui_exp(it, "Gồm %.0f file, %s.", jnumd(sn, "total_files", 0), b);
        ui_exp(it, verified ? "+ Đã xác minh: agent kiểm toàn vẹn cả kho thành công SAU khi snapshot này được tạo."
                            : "Chưa có bản ghi kiểm toàn vẹn cho snapshot này (không có nghĩa là hỏng). X để xác minh ngay.");
        ui_exp(it, "A — Xem trước khôi phục: so sánh với Saves hiện tại, KHÔNG ghi gì.");
        ui_exp(it, "X — Xác minh: băm lại toàn bộ snapshot để chứng minh đọc lại được.");
        ui_exp(it, "Y — Khôi phục: ghi đè Saves bằng snapshot này (có hộp xác nhận).");
        ui_act(it, 0, "XEM TRƯỚC", act_preview);
        ui_act(it, 1, "XÁC MINH", act_verify);
        ui_act(it, 2, "KHÔI PHỤC", act_restore);
        i++;
    }
    if (!i) ui_head(s, g_snaps ? "(chưa có snapshot — chọn Sao lưu ngay)" : "(không đọc được danh sách snapshot từ engine)");
}

static void open_savevault(void) { ui_push(ui_screen_new("SaveVault", build_savevault)); }
static void act_open_savevault(void) { if (need_ready()) app_refresh_then(open_savevault); }

/* ============================================================ Thẻ nhớ */
static void build_cards_screen(ui_screen *s) {
    cJSON *st = jget(g_ready, "storage");
    ui_hdr(s, "Mỗi thẻ: đường dẫn, filesystem, dung lượng, quyền ghi.");
    cJSON *w;
    cJSON_ArrayForEach(w, jarr(st, "warnings")) if (cJSON_IsString(w)) ui_hdr(s, "! %s", w->valuestring);
    for (int k = 0; k < 2; k++) {
        const char *label = k ? "SD2 (Secondary)" : "SD1 (Primary)";
        cJSON *c = jget(st, k ? "secondary" : "primary");
        if (!cJSON_IsObject(c) || !jbool_d(c, "mounted", 0)) {
            ui_item *it = ui_add(s, label, "không có", ST_UNKNOWN);
            ui_exp(it, k ? "Chưa gắn thẻ ở khe 2." : "!! Không xác định được thẻ chính.");
            continue;
        }
        int fsd = jbool_d(c, "fs_detected", 1);
        double free_mb = jnumd(c, "free_mb", 0), used = jnumd(c, "percent_used", 0);
        char t[32], a[40], b[40];
        app_scalar(jget(c, "total_mb"), t, sizeof t);
        app_scalar(jget(c, "free_mb"), a, sizeof a);
        snprintf(b, sizeof b, "%.0f MB trống", free_mb);
        ui_item *it = ui_add(s, label, b, ST_GREEN);
        ui_exp(it, "Đường dẫn: %s", jstrd(c, "path", "?"));
        ui_exp(it, "Filesystem: %s", fsd ? jstrd(c, "filesystem", "?") : "chưa xác minh");
        ui_exp(it, "Tổng: %s MB", t);
        ui_exp(it, "Trống: %s MB (%.0f%%)", a, 100 - used);
        ui_exp(it, "Read-only mount: %s", jbool_d(c, "read_only_mount", 0) ? "CÓ" : "không");
        int writable = jbool_d(c, "writable", 0);
        ui_exp(it, "Ghi được: %s", writable ? "có" : "KHÔNG");
        int color = ST_GREEN;
        if (!writable) {
            color = ST_RED;
            ui_exp(it, "!! Thẻ không ghi được: kiểm tra khoá cơ học/rút cắm lại. Không thể sao lưu hay xoá ROM.");
        }
        if (free_mb < 200) {
            color = ST_RED;
            ui_exp(it, "!! Gần đầy (<200 MB): dùng Library > Dọn ROM trùng hoặc Xoá ROM để giải phóng.");
        } else if (free_mb < 1000) {
            if (color == ST_GREEN) color = ST_YELLOW;
            ui_exp(it, "! Còn dưới 1 GB: dùng Library > Dọn ROM trùng để giải phóng dung lượng.");
        }
        if (!fsd) {
            if (color == ST_GREEN) color = ST_UNKNOWN;
            ui_exp(it, "! Chưa xác minh được filesystem (không đọc được /proc/mounts); LoongMate không kết luận đúng/sai.");
        } else if (k && !jbool_d(c, "is_fat32", 0)) {
            color = ST_RED;
            ui_exp(it, "!! Leaf yêu cầu thẻ khe 2 là FAT32. Sao lưu rồi format FAT32 trên máy tính — LoongMate KHÔNG format thẻ (sẽ xoá hết dữ liệu).");
        }
        it->tagc = color;
    }
    ui_head(s, "Công cụ");
    ui_item *it = ui_add(s, "Chuyển game sang thẻ kia", "SHA-256", ST_YELLOW);
    ui_exp(it, "Chuyển trọn bộ game (ROM, track, playlist, ảnh bìa) giữa SD1 và SD2 để giải phóng thẻ đang chật.");
    ui_exp(it, "Gợi ý hàng loạt theo mục tiêu dung lượng trống (1/2/3/5 GB), hoặc chọn từng game.");
    ui_exp(it, "+ Chép > xác minh SHA-256 từng file > chỉ khi cả game đủ mới xoá nguồn; lỗi thì hoàn tác cả game.");
    ui_exp(it, "! Save/state được CHÉP sang nhưng GIỮ LẠI ở thẻ cũ.");
    ui_act(it, 0, "MỞ", open_transfer_screen);
}

static void open_cards_now(void) { ui_push(ui_screen_new("Thẻ nhớ", build_cards_screen)); }
static void act_open_cards(void) { if (need_ready()) app_refresh_then(open_cards_now); }

/* ============================================================ Máy */
typedef struct { const char *key, *name, *meaning, *advice; } diag_info_t;
static const diag_info_t DIAG_INFO[] = {
    { "userdata_rw_integrity", "Ghi/đọc dữ liệu LoongMate",
      "Ghi một file tạm 32 KB vào thư mục dữ liệu của LoongMate, đọc lại, so sánh rồi xoá.",
      "Nếu FAIL: thẻ có thể đầy, bị khoá ghi hoặc hỏng. SaveVault sẽ không lưu được — kiểm tra mục Thẻ nhớ." },
    { "leaf_directory_structure", "Cấu trúc thư mục Leaf",
      "Kiểm tra các thư mục chuẩn (Roms, Saves, States, BIOS...) có mặt trên thẻ chính.",
      "Nếu FAIL: mở Library > Quét lại, mục 'Thiếu thư mục chuẩn' có sửa an toàn tạo lại thư mục." },
    { "secondary_sd_presence", "Thẻ SD2", "Kiểm tra thẻ nhớ khe 2 (nếu có khai báo) còn truy cập được.",
      "Nếu FAIL: rút thẻ ra cắm lại, rồi chạy lại kiểm tra." },
    { "mount_and_space", "Mount và dung lượng", "Kiểm tra filesystem, chế độ read-only và dung lượng trống của từng thẻ.",
      "Nếu FAIL: xem mục Thẻ nhớ để biết thẻ nào lỗi (đầy, read-only, sai FAT32)." },
    { "thermal_battery_sample", "Mẫu nhiệt độ và pin", "Lấy một mẫu tức thời nhiệt độ, pin, trạng thái sạc.",
      "Nếu không đọc được: máy không lộ cảm biến qua sysfs; LoongMate không đoán số." },
    { "platform_identity", "Nhận dạng thiết bị", "Đọc model và phiên bản hệ điều hành thật nếu có.",
      "Không đọc được thì để trống, không bịa." },
};

static void diag_extra(ui_item *it, const cJSON *t) {
    cJSON *pd = jarr(t, "present_dirs");
    if (pd) {
        char buf[600] = "";
        cJSON *d;
        cJSON_ArrayForEach(d, pd) if (cJSON_IsString(d)) { if (buf[0]) strncat(buf, ", ", sizeof buf - strlen(buf) - 1); strncat(buf, d->valuestring, sizeof buf - strlen(buf) - 1); }
        ui_exp(it, "Thư mục tìm thấy: %s", buf[0] ? buf : "(không có)");
    }
    cJSON *mounts = jget(t, "mounts"), *m;
    cJSON_ArrayForEach(m, mounts) {
        char buf[600] = "";
        cJSON *kv;
        cJSON_ArrayForEach(kv, m) {
            char v[160];
            app_scalar(kv, v, sizeof v);
            size_t l = strlen(buf);
            snprintf(buf + l, sizeof buf - l, "%s%s=%s", l ? ", " : "", kv->string, v);
        }
        ui_exp(it, "Thẻ %s: %s", m->string, buf);
    }
    static const char *const keys[] = { "path", "model", "os_release", "leaf_version", "model_reason", "reason" };
    for (int i = 0; i < 6; i++) {
        cJSON *v = jget(t, keys[i]);
        if (!v || cJSON_IsNull(v) || (cJSON_IsString(v) && !v->valuestring[0])) continue;
        char b[400];
        app_scalar(v, b, sizeof b);
        ui_exp(it, "%s: %s", keys[i], b);
    }
    if (jget(t, "latency_ms")) {
        char b[40];
        app_scalar(jget(t, "latency_ms"), b, sizeof b);
        ui_exp(it, "Độ trễ ghi/đọc: %s ms", b);
    }
}

static void do_python_ui(const char *arg) { (void)arg; ui_exec_python_ui(); }

static void act_python_ui(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    lines_t L = {0};
    lines_add(&L, "Đóng giao diện native và mở giao diện Python (cùng engine, cùng dữ liệu).");
    lines_add(&L, "Dùng khi giao diện native lỗi hiển thị. Thoát giao diện Python sẽ về launcher; mở lại LoongMate để vào native.");
    ui_confirm("Mở giao diện Python", &L, "Mở", do_python_ui, "");
}

static void build_device(ui_screen *s) {
    cJSON *dev = jget(g_ready, "device"), *bat = jget(dev, "battery"), *th = jget(dev, "thermal");
    ui_hdr(s, "Tình trạng chung: %s", ui_status_vi(jstr(dev, "status")));
    cJSON *w;
    cJSON_ArrayForEach(w, jarr(dev, "warnings")) if (cJSON_IsString(w)) ui_hdr(s, "! %s", w->valuestring);
    if (g_health_err[0]) ui_hdr(s, "!! Chẩn đoán thất bại: %s", g_health_err);
    ui_head(s, "Thông số");

    int bav = jbool_d(bat, "available", 0);
    double pct = jnumd(bat, "percent", -1);
    int chg = jbool_d(bat, "is_charging", 0);
    char tag[64];
    ui_item *it;
    if (bav && pct >= 0) {
        snprintf(tag, sizeof tag, "%.0f%% (%s)", pct, chg ? "đang sạc" : "không sạc");
        int c = (pct <= 15 && !chg) ? ST_RED : (pct <= 25 && !chg) ? ST_YELLOW : ST_GREEN;
        it = ui_add(s, "Pin", tag, c);
        ui_exp(it, "Pin của máy.");
        ui_exp(it, "Dung lượng: %.0f%%", pct);
        ui_exp(it, "Trạng thái: %s", jstrd(bat, "status", "None"));
        ui_exp(it, "Nguồn đọc: %s", jstrd(bat, "device", "None"));
        if (pct <= 25 && !chg) ui_exp(it, "! Pin thấp: cắm sạc trước khi sao lưu hoặc khôi phục (mất điện giữa chừng có thể làm dở dang).");
    } else {
        it = ui_add(s, "Pin", "không đọc được", ST_UNKNOWN);
        ui_exp(it, "Pin của máy.");
        ui_exp(it, "!! Không đọc được pin: %s", jstrd(bat, "reason", "None"));
        ui_exp(it, "LoongMate không đoán số khi không đọc được.");
    }
    int tav = jbool_d(th, "available", 0);
    double cur = jnumd(th, "current_celsius", -1000);
    if (tav && cur > -1000) {
        snprintf(tag, sizeof tag, "%.1f°C (%s)", cur, jstrd(th, "trend", ""));
        it = ui_add(s, "Nhiệt độ", tag, cur >= 70 ? ST_RED : cur >= 60 ? ST_YELLOW : ST_GREEN);
        ui_exp(it, "Nhiệt độ trung bình các vùng nhiệt.");
        ui_exp(it, "Hiện tại: %.1f°C", cur);
        ui_exp(it, "Cao nhất: %.1f°C", jnumd(th, "max_celsius", 0));
        ui_exp(it, "Xu hướng: %s", jstrd(th, "trend", "None"));
        if (cur >= 60) ui_exp(it, "! Máy đang nóng: ngừng chơi vài phút, tránh sạc khi chơi nặng.");
    } else {
        it = ui_add(s, "Nhiệt độ", "không đọc được", ST_UNKNOWN);
        ui_exp(it, "Nhiệt độ trung bình các vùng nhiệt.");
        ui_exp(it, "!! Không đọc được: %s", jstrd(th, "reason", "None"));
    }

    if (g_health) {
        {
            const char *ts = jstrd(g_health, "timestamp", "?"), *sp = strchr(ts, ' ');
            ui_head(s, "Kiểm tra tự động (%s)", sp ? sp + 1 : ts);
        }
        cJSON *t;
        cJSON_ArrayForEach(t, jarr(g_health, "tests")) {
            const char *key = jstrd(t, "test", "?");
            const diag_info_t *di = NULL;
            for (size_t i = 0; i < sizeof DIAG_INFO / sizeof DIAG_INFO[0]; i++)
                if (!strcmp(DIAG_INFO[i].key, key)) di = &DIAG_INFO[i];
            const char *desc = jstrd(t, "description", "");
            int skipped = jbool_d(t, "skipped", 0), passed = jbool_d(t, "passed", 1);
            const char *st = skipped ? "SKIP" : (passed ? "PASS" : "FAIL");
            it = ui_add(s, di ? di->name : key, st, skipped ? ST_YELLOW : (passed ? ST_GREEN : ST_RED));
            ui_exp(it, "%s", di ? di->meaning : desc);
            ui_exp(it, "%s", desc);
            diag_extra(it, t);
            if (!strcmp(st, "FAIL")) { ui_exp(it, "!! KẾT QUẢ: FAIL"); ui_exp(it, "%s", di ? di->advice : ""); }
            else if (!strcmp(st, "SKIP")) ui_exp(it, "! Bỏ qua: %s", jstrd(t, "reason", ""));
            else ui_exp(it, "+ KẾT QUẢ: PASS");
        }
    }

    ui_head(s, "Công cụ");
    it = ui_add(s, "Xuất gói hỗ trợ", "ZIP", ST_UNKNOWN);
    ui_exp(it, "Tạo file loongmate_support_<thời gian>.zip gồm: summary.json, checks.txt, mounts.txt, environment.txt, library_issues.json, logs.txt.");
    ui_exp(it, "+ Đã lọc mật khẩu Wi-Fi, token, khoá SSH khỏi mọi file.");
    ui_exp(it, "+ Privacy Mode bật: tên game được ẩn danh ở mọi file trong gói.");
    ui_exp(it, "Gói chỉ nằm trên thẻ; LoongMate không tự gửi đi đâu.");
    ui_exp(it, "Cần quét thư viện (~15 giây) để có danh sách vấn đề đầy đủ.");
    ui_act(it, 0, "XUẤT GÓI", open_bundle_confirm);
    it = ui_add(s, "Lịch sử pin & nhiệt", "24 giờ / 7 ngày", ST_UNKNOWN);
    ui_exp(it, "Biểu đồ pin (%%) và nhiệt độ cao nhất (°C) theo 24 giờ và 7 ngày, kèm tóm tắt hao pin và số lần vượt 70°C.");
    ui_exp(it, "Dữ liệu do LoongMate Agent ghi mỗi 5 phút; chưa có agent thì chưa có biểu đồ (không vẽ số giả).");
    ui_exp(it, "Chỉ đọc.");
    ui_act(it, 0, "XEM", open_history);
    it = ui_add(s, "Sức khoẻ pin", "ước lượng", ST_UNKNOWN);
    ui_exp(it, "Dung lượng đầy hiện tại so với dung lượng thiết kế, %% sức khoẻ ước lượng, mức 'health' do driver báo và xu hướng mAh mỗi 30 ngày.");
    ui_exp(it, "Đây là ƯỚC LƯỢNG từ mẫu telemetry, không phải số đo chính hãng; màn ghi rõ nguồn của dung lượng thiết kế.");
    ui_exp(it, "Cần LoongMate Agent đã ghi mẫu một thời gian. Chỉ đọc.");
    ui_act(it, 0, "XEM", open_battery);
    it = ui_add(s, "Cập nhật LoongMate", "gói trên thẻ", ST_UNKNOWN);
    ui_exp(it, "Xem phiên bản đang chạy, tìm gói cập nhật trong LoongMate-update/ trên thẻ, kiểm tra toàn vẹn rồi áp dụng; có thể quay về bản trước.");
    ui_exp(it, "Áp dụng luôn qua hộp xác nhận (mặc định Huỷ). Cần thoát và mở lại LoongMate để dùng bản mới.");
    ui_act(it, 0, "MỞ", open_update);
    it = ui_add(s, "Tự tiếp tục game", "Games/Favorites", ST_UNKNOWN);
    ui_exp(it, "Mở game từ Games/Favorites thì vào thẳng chỗ đang chơi dở như tab Recents.");
    ui_exp(it, "Bật/tắt (hiệu lực ngay). Cần LoongMate Agent đang chạy; không thay file nào của Leaf.");
    ui_exp(it, "Có Hot Tabs trên thẻ thì màn này cũng cho cài/gỡ Hot Tabs.");
    ui_act(it, 0, "MỞ", open_hottabs);
    it = ui_add(s, "Cấu hình Leaf", "RetroArch", ST_UNKNOWN);
    ui_exp(it, "Sao lưu và khôi phục phần bạn đã chỉnh: retroarch.cfg, override/core option/remap, cấu hình emulator độc lập.");
    ui_exp(it, "So sánh từng khoá với hiện tại; sau khi cập nhật Leaf, xem ngay cấu hình nào đã đổi.");
    ui_act(it, 0, "MỞ", open_leafcfg);
    it = ui_add(s, "Trạng thái agent", "", ST_UNKNOWN);
    ui_exp(it, "LoongMate Agent có đang chạy không, lần sao lưu tự động gần nhất, mẫu pin/nhiệt gần nhất.");
    ui_exp(it, "Chỉ đọc.");
    ui_exp(it, "Bật service LoongMate Agent trong màn Services của Leaf: Enable rồi Start.");
    ui_act(it, 0, "XEM", open_agent);
    it = ui_add(s, "Thiết bị trong mạng", "Tailscale", ST_UNKNOWN);
    ui_exp(it, "Các máy trong tailnet (PC, Mac, Pi, NAS): online/offline, IP; chọn máy để ping, kiểm cổng SSH/SMB/Sunshine/RDP, đánh dấu yêu thích, Wake-on-LAN.");
    ui_exp(it, "Cần Tailscale đã cài và chạy ngoài LoongMate; LoongMate chỉ đọc trạng thái.");
    ui_exp(it, "Tác vụ mạng chạy nền, có thể mất vài giây.");
    ui_act(it, 0, "MỞ", open_nodes);
    it = ui_add(s, "Kiểm tra nút", "", ST_UNKNOWN);
    ui_exp(it, "Hiện nút (theo tên Catastrophe: A/B/X/Y/L1/R1/D-pad...) bạn vừa bấm trên bản native.");
    ui_exp(it, "! Bản native không thấy số hiệu joystick thô (toolkit đã đổi sang tên nút); muốn dò số hiệu thô hãy dùng Kiểm tra nút của giao diện Python.");
    ui_exp(it, "Thoát: bấm CÙNG một nút 3 lần liên tiếp.");
    ui_act(it, 0, "MỞ", open_buttontest);
    it = ui_add(s, "Mở giao diện Python (dự phòng)", "", ST_UNKNOWN);
    ui_exp(it, "Thoát giao diện native và chạy giao diện Python của LoongMate (cùng engine, cùng dữ liệu, chức năng tương đương).");
    ui_exp(it, "Dùng khi giao diện native hiển thị lỗi hoặc thiếu gì đó. Có hộp xác nhận (mặc định Huỷ).");
    ui_exp(it, "Thoát giao diện Python (B) sẽ về launcher của Leaf, không quay lại native; mở lại LoongMate để vào native.");
    ui_exp(it, "Thiếu Python hoặc engine/ui_sdl.py thì báo lỗi và ở lại đây.");
    ui_act(it, 0, "MỞ", act_python_ui);
}

static void open_device_now(void) { ui_push(ui_screen_new("Máy", build_device)); }

static void on_health(lm_job *j, const char *arg) {
    (void)arg;
    cJSON_Delete(g_health);
    g_health = NULL;
    g_health_err[0] = 0;
    char err[300];
    cJSON *h = ui_job_json(j, err, sizeof err);    /* health thoát mã 1 khi có test FAIL nhưng JSON vẫn hợp lệ */
    if (h) g_health = h; else snprintf(g_health_err, sizeof g_health_err, "%s", err);
    open_device_now();
}

static void device_after_ready(void) {
    cli_task("Đang chạy kiểm tra tự động", SHORT_TIMEOUT_MS, on_health, "", "health", NULL);
}
static void act_open_device(void) { if (need_ready()) app_refresh_then(device_after_ready); }

/* ============================================================ gói hỗ trợ */
static void bundle_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Gói hỗ trợ");
    if (!r) return;
    lines_t L = {0};
    const char *p = jstr(r, "bundle_path");
    if (p) { lines_add(&L, "+ Đã xuất gói hỗ trợ tại:"); lines_add(&L, "%s", p); }
    else { lines_add(&L, "!! Không xuất được gói hỗ trợ:"); lines_add(&L, "%s", jstrd(r, "message", "engine không trả đường dẫn")); }
    cJSON_Delete(r);
    ui_info("Gói hỗ trợ", &L);
    app_refresh();
}

static void do_bundle(const char *arg) {
    (void)arg;
    cli_task("Đang xuất gói hỗ trợ", LONG_TIMEOUT_MS, bundle_done, "", "bundle", NULL);   /* mặc định Privacy Mode bật */
}

void open_bundle_confirm(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    lines_t L = {0};
    lines_add(&L, "Sẽ quét thư viện (~15 giây) rồi tạo file ZIP chẩn đoán trên thẻ.");
    lines_add(&L, "Gói gồm: summary.json, checks.txt, mounts.txt, environment.txt, library_issues.json, logs.txt.");
    lines_add(&L, "+ Mật khẩu, token, khoá SSH đã được lọc khỏi mọi file.");
    lines_add(&L, "+ Privacy Mode: tên game bị ẩn danh.");
    lines_add(&L, "Không tự gửi đi đâu; bạn tự chép file ra.");
    ui_confirm("Xuất gói hỗ trợ", &L, "Xuất gói", do_bundle, "");
}

/* ============================================================ Home: phím + khởi động */
static int home_key(ui_screen *s, cat_input_event *ev) {
    (void)s;
    switch (ev->button) {
    case CAT_BTN_UP:    g_home_sel &= 1; return 1;
    case CAT_BTN_DOWN:  g_home_sel |= 2; return 1;
    case CAT_BTN_LEFT:  g_home_sel &= 2; return 1;
    case CAT_BTN_RIGHT: g_home_sel |= 1; return 1;
    default: break;
    }
    if (ev->repeated) return 1;
    switch (ev->button) {
    case CAT_BTN_A:
        if (!g_ready && P.python[0]) { app_refresh(); return 1; }   /* đọc readiness lỗi trước đó: bấm A để thử lại */
        if (g_home_sel == 0) act_open_device();
        else if (g_home_sel == 1) act_open_cards();
        else if (g_home_sel == 2) act_open_savevault();
        else if (need_ready()) { app_refresh_then(open_library_screen); }
        return 1;
    case CAT_BTN_X: do_backup(); return 1;
    case CAT_BTN_Y: open_bundle_confirm(NULL, NULL); return 1;
    case CAT_BTN_B: ui_quit(); return 1;
    default: return 1;
    }
}

int main(int argc, char **argv) {
    const char *arg_pak = NULL, *arg_py = NULL, *arg_ud = NULL, *arg_p = NULL, *arg_s = NULL, *arg_apps = NULL;
    lm_harness hn = {0};
    for (int i = 1; i < argc; i++) {
        if (lm_harness_arg(&hn, argc, argv, &i)) continue;
        if (!strcmp(argv[i], "--pak") && i + 1 < argc) arg_pak = argv[++i];
        else if (!strcmp(argv[i], "--python") && i + 1 < argc) arg_py = argv[++i];
        else if (!strcmp(argv[i], "--userdata") && i + 1 < argc) arg_ud = argv[++i];
        else if (!strcmp(argv[i], "--primary") && i + 1 < argc) arg_p = argv[++i];
        else if (!strcmp(argv[i], "--secondary") && i + 1 < argc) arg_s = argv[++i];
        else if (!strcmp(argv[i], "--apps-dir") && i + 1 < argc) arg_apps = argv[++i];
        else {
            fprintf(stderr, "dùng: %s [--pak DIR] [--python PATH] [--userdata DIR] [--primary DIR] [--secondary DIR] [--apps-dir DIR] "
                            "[--screenshot out.ppm] [--keys A,SHOT,...]\n", argv[0]);
            return 2;
        }
    }
    if (lm_harness_active(&hn)) g_debounce_ms = 0;

    cat_config cfg = { .window_title = "LoongMate", .log_path = cat_resolve_log_path("loongmate-native") };
    if (cat_init(&cfg) != CAT_OK) { fprintf(stderr, "cat_init thất bại\n"); return 1; }
    /* Offscreen mặc định tạo cửa sổ 1024x768 trong khi giao diện vẽ ở 960x720: SDL co giãn 1,067 lần làm các hàng 1px bị
     * rơi mất (vạch ngang giả). Máy thật chạy đúng 960x720 nên không có co giãn; khi thử offscreen phải ép cùng kích thước. */
    if (lm_harness_active(&hn)) {
        SDL_SetWindowFullscreen(cat_get_window(), 0);
        SDL_SetWindowSize(cat_get_window(), cat_get_screen_width(), cat_get_screen_height());
    }
    lm_ensure_vietnamese_font();

    char err[300];
    int have_engine = cli_resolve(arg_pak, arg_py, arg_ud, arg_p, arg_s, arg_apps, err, sizeof err) == 0;
    if (!have_engine) snprintf(g_ready_err, sizeof g_ready_err, "%s", err);

    ui_screen *home = ui_screen_new("LoongMate — Home", NULL);
    home->draw = home_draw;
    home->key_fn = home_key;
    home->footer[0] = (cat_footer_item){ .button = CAT_BTN_B, .label = "THOÁT" };
    home->footer[1] = (cat_footer_item){ .button = CAT_BTN_Y, .label = "GÓI HỖ TRỢ" };
    home->footer[2] = (cat_footer_item){ .button = CAT_BTN_X, .label = "SAO LƯU" };
    home->footer[3] = (cat_footer_item){ .button = CAT_BTN_A, .label = "CHI TIẾT", .is_confirm = true };
    home->nfooter = 4;
    if (have_engine) app_refresh();

    int rc = ui_run(&hn, home);
    cJSON_Delete(g_ready);
    cJSON_Delete(g_health);
    cat_quit();
    return rc;
}
