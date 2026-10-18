/* LoongMate native — lịch sử pin/nhiệt (biểu đồ cột), trạng thái agent, thiết bị trong mạng (Tailscale), kiểm tra nút.
 * Dữ liệu đến từ `cli.py --json history|agent status|nodes ...`; native chỉ vẽ lại đúng những gì engine trả về. */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define AGENT_HINT tr("Bật service LoongMate Agent trong màn Services của Leaf: Enable rồi Start.")

static uint8_t clamp8(int v) { return (uint8_t)(v > 255 ? 255 : v < 0 ? 0 : v); }
static cat_draw_color lift(cat_draw_color c, int d) {
    return (cat_draw_color){ clamp8(c.r + d), clamp8(c.g + d), clamp8(c.b + d), 255 };
}
static void fmt_time(char *buf, size_t n, double ts, const char *fmt) {
    time_t t = (time_t)ts;
    struct tm tmv;
    localtime_r(&t, &tmv);
    strftime(buf, n, fmt, &tmv);
}

/* ============================================================ lịch sử pin & nhiệt */
typedef struct { double ts, batt, peak; } samp_t;
typedef struct {
    samp_t *v; int n;
    double now;
    int rng;                 /* 0 = 24 giờ, 1 = 7 ngày */
    cJSON *day;              /* `history --days 1` (tóm tắt đúng khoảng 24 giờ) */
} hist_t;

static const struct { const char *name; double span; int nb; const char *fmt; int nt; } RANGES[2] = {
    { "24 giờ qua", 24 * 3600.0, 48, "%H:%M", 4 },
    { "7 ngày qua", 7 * 86400.0, 84, "%d/%m", 7 },
};

static cat_draw_color temp_color(double c) { return ui_color(c > 70 ? ST_RED : c >= 60 ? ST_YELLOW : ST_GREEN); }
static cat_draw_color batt_color(double p) { return ui_color(p <= 15 ? ST_RED : p <= 30 ? ST_YELLOW : ST_GREEN); }

static double num_or_nan(const cJSON *o, const char *k) {
    double v;
    return jnum(o, k, &v) ? v : NAN;
}

/* Chia các mẫu trong [t0, t0+span] thành nb cột đều nhau; cột không có mẫu là NaN (KHÔNG nội suy, KHÔNG điền số). */
static void bucketize(const hist_t *h, double t0, double span, int nb, int use_batt, double *out) {
    double *sum = calloc((size_t)nb, sizeof *sum), *mx = malloc((size_t)nb * sizeof *mx);
    int *cnt = calloc((size_t)nb, sizeof *cnt);
    if (!sum || !mx || !cnt) { free(sum); free(mx); free(cnt); for (int i = 0; i < nb; i++) out[i] = NAN; return; }
    for (int i = 0; i < nb; i++) mx[i] = -1e9;
    for (int i = 0; i < h->n; i++) {
        double v = use_batt ? h->v[i].batt : h->v[i].peak;
        double ts = h->v[i].ts;
        if (isnan(v) || ts < t0 || ts > t0 + span) continue;
        int b = (int)((ts - t0) * nb / span);
        if (b >= nb) b = nb - 1;
        sum[b] += v; cnt[b]++;
        if (v > mx[b]) mx[b] = v;
    }
    for (int i = 0; i < nb; i++) out[i] = cnt[i] ? (use_batt ? sum[i] / cnt[i] : mx[i]) : NAN;
    free(sum); free(mx); free(cnt);
}

static void draw_chart(int px, int pw, int y, int ph, const double *vals, int nb, int use_batt, const char *title, int title_x) {
    cat_theme *th = cat_get_theme();
    TTF_Font *ft = cat_get_font(CAT_FONT_TINY);
    cat_draw_text_ellipsized(ft, title, title_x, y - TTF_FontHeight(ft) - 2, th->hint, pw + (px - title_x));
    cat_draw_rounded_rect(px, y, pw, ph, 0, lift(th->background, 16));
    static const char *const labs[3] = { "0", "50", "100" };
    for (int i = 0; i < 3; i++) {
        int gy = y + ph - ph * i / 2;
        if (gy >= y + ph) gy = y + ph - 1;
        cat_draw_rect(px, gy, pw, 1, lift(th->background, -40));
        int ly = gy - TTF_FontHeight(ft) / 2;
        if (ly < y) ly = y;
        if (ly > y + ph - TTF_FontHeight(ft)) ly = y + ph - TTF_FontHeight(ft);
        cat_draw_text(ft, labs[i], title_x, ly, th->hint);
    }
    for (int i = 0; i < nb; i++) {
        if (isnan(vals[i])) continue;
        int x0 = px + i * pw / nb, x1 = px + (i + 1) * pw / nb;
        double v = vals[i] > 100 ? 100 : vals[i] < 0 ? 0 : vals[i];
        int hh = (int)(ph * v / 100.0);
        if (hh < 2) hh = 2;
        int bw = x1 - x0 - 1;
        if (bw < 1) bw = 1;
        cat_draw_rounded_rect(x0, y + ph - hh, bw, hh, 0, use_batt ? batt_color(vals[i]) : temp_color(vals[i]));
    }
}

static void hist_summary(const cJSON *sm, lines_t *L) {
    if (!sm || jnumd(sm, "samples", 0) <= 0) return;
    char a[40], b[40];
    fmt_time(a, sizeof a, jnumd(sm, "first_ts", 0), "%d/%m %H:%M");
    fmt_time(b, sizeof b, jnumd(sm, "last_ts", 0), "%d/%m %H:%M");
    lines_add(L, tr("%.0f mẫu, từ %s đến %s"), jnumd(sm, "samples", 0), a, b);
    cJSON *dr = jget(sm, "drain_pct_per_hour");
    if (!cJSON_IsNumber(dr)) lines_add(L, tr("Hao pin: chưa đủ dữ liệu (cần ít nhất 0,5 giờ xả liên tục, mẫu cách nhau không quá 30 phút)."));
    else lines_add(L, tr("Hao pin: %g %%/giờ (tính trên %g giờ xả liên tục)"), dr->valuedouble, jnumd(sm, "discharge_hours", 0));
    char t[3][80];
    static const char *const nm[3] = { "SoC", "GPU", "Pin" }, *const kk[3] = { "soc", "gpu", "batt" };
    cJSON *tp = jget(sm, "temp");
    for (int i = 0; i < 3; i++) {
        cJSON *d = jget(tp, kk[i]);
        if (cJSON_IsObject(d)) snprintf(t[i], sizeof t[i], "%s %g°C (TB %g°C)", nm[i], jnumd(d, "max", 0), jnumd(d, "avg", 0));
        else snprintf(t[i], sizeof t[i], tr("%s: không đọc được"), nm[i]);
    }
    lines_add(L, tr("Cao nhất — %s • %s • %s"), t[0], t[1], t[2]);
    lines_add(L, tr("Vượt 70°C: %.0f lần (%.0f mẫu)"), jnumd(sm, "over_70_events", 0), jnumd(sm, "over_70_samples", 0));
}

static void hist_draw(ui_screen *s, SDL_Rect a) {
    hist_t *h = s->priv;
    cat_theme *th = cat_get_theme();
    TTF_Font *fs = cat_get_font(CAT_FONT_SMALL), *ft = cat_get_font(CAT_FONT_TINY);
    int gap = cat_device_scale(6), lh = TTF_FontHeight(ft) + 2, y = a.y;
    if (h->n == 0) {
        cat_draw_text_ellipsized(fs, tr("Chưa có dữ liệu — bật service LoongMate Agent trong màn Services của Leaf: Enable rồi Start."), a.x, y + gap, ui_color(ST_YELLOW), a.w);
        lines_t t = {0};
        ui_wrap(ft, tr("Agent ghi một mẫu pin/nhiệt mỗi 5 phút vào thư mục telemetry của LoongMate."), a.w, &t);
        ui_wrap(ft, tr("LoongMate không vẽ số giả: biểu đồ chỉ có sau khi agent đã chạy."), a.w, &t);
        for (int i = 0; i < t.n; i++) cat_draw_text(ft, t.v[i], a.x, y + gap * 4 + (i + 1) * (lh + 4), th->hint);
        lines_free(&t);
        return;
    }
    int tabh = TTF_FontHeight(fs) + cat_device_scale(8), tabw = cat_device_scale(130);
    for (int i = 0; i < 2; i++) {
        int x = a.x + i * (tabw + gap);
        int sel = i == h->rng;
        cat_draw_pill(x, y, tabw, tabh, sel ? th->highlight : lift(th->background, -22));
        int w = cat_measure_text(fs, RANGES[i].name);
        cat_draw_text(fs, RANGES[i].name, x + (tabw - w) / 2, y + (tabh - TTF_FontHeight(fs)) / 2, sel ? th->highlighted_text : th->text);
    }
    char nowb[48], nows[64];
    fmt_time(nowb, sizeof nowb, h->now, "%d/%m %H:%M");
    snprintf(nows, sizeof nows, tr("Đọc lúc %s"), nowb);
    cat_draw_text(ft, nows, a.x + a.w - cat_measure_text(ft, nows), y + (tabh - TTF_FontHeight(ft)) / 2, th->hint);
    y += tabh + gap * 2;

    double span = RANGES[h->rng].span, t0 = h->now - span;
    int nb = RANGES[h->rng].nb, nt = RANGES[h->rng].nt;
    int in_range = 0;
    double last_ts = 0;
    for (int i = 0; i < h->n; i++) { if (h->v[i].ts >= t0) in_range++; last_ts = h->v[i].ts; }
    if (!in_range) {
        char lt[40], msg[200], nm[40];
        fmt_time(lt, sizeof lt, last_ts, "%d/%m %H:%M");
        snprintf(nm, sizeof nm, "%s", RANGES[h->rng].name);
        snprintf(msg, sizeof msg, tr("Không có mẫu nào trong %s. Mẫu gần nhất: %s."), nm, lt);
        cat_draw_text_ellipsized(fs, msg, a.x, y, ui_color(ST_YELLOW), a.w);
        cat_draw_text_ellipsized(ft, AGENT_HINT, a.x, y + TTF_FontHeight(fs) + gap, th->hint, a.w);
        return;
    }
    lines_t raw = {0}, sum = {0};
    hist_summary(h->rng == 0 ? jget(h->day, "summary") : jget(s->d, "summary"), &raw);
    int nsum = raw.n, nsum_lines = 0;
    lines_add(&raw, tr("Cột trống = không có mẫu (máy ngủ/tắt hoặc agent không chạy)."));
    lines_add(&raw, tr("Màu nhiệt: xanh <60 / vàng 60-70 / đỏ >70°C.  Màu pin: xanh >30 / vàng 16-30 / đỏ ≤15%%."));
    int lblw = cat_measure_text(ft, "100") + gap * 2;
    int px = a.x + lblw, pw = a.w - lblw, block = 0, ph = 0, reserved = 0;
    for (int with_notes = 1; with_notes >= 0; with_notes--) {
        lines_free(&sum);
        for (int i = 0; i < (with_notes ? raw.n : nsum); i++) {
            ui_wrap(ft, raw.v[i], a.w, &sum);
            if (i == nsum - 1) nsum_lines = sum.n;
        }
        reserved = sum.n * lh + gap * 2;
        block = (a.y + a.h - y - reserved) / 2;
        ph = block - 2 * lh - gap;
        if (ph >= cat_device_scale(36)) break;       /* biểu đồ đủ cao thì giữ chú thích; không thì bỏ chú thích để nhường chỗ */
    }
    lines_free(&raw);
    if (ph < 24) ph = 24;
    double *vals = malloc((size_t)nb * sizeof *vals);
    if (!vals) { lines_free(&sum); return; }
    for (int c = 0; c < 2; c++) {
        bucketize(h, t0, span, nb, c == 0, vals);
        int cy = y + lh + c * block;
        draw_chart(px, pw, cy, ph, vals, nb, c == 0,
                   c == 0 ? tr("Pin (%) — trung bình mỗi cột") : tr("Nhiệt độ cao nhất (°C) trong SoC/GPU/pin — cao nhất mỗi cột"), a.x);
        for (int k = 0; k <= nt; k++) {
            char lab[24];
            fmt_time(lab, sizeof lab, t0 + span * k / nt, RANGES[h->rng].fmt);
            int w = cat_measure_text(ft, lab), lx = px + pw * k / nt;
            lx = (k > 0 && k < nt) ? lx - w / 2 : (k == 0 ? lx : lx - w);
            cat_draw_text(ft, lab, lx, cy + ph + 2, th->hint);
        }
    }
    free(vals);
    int ty = y + 2 * block + gap;
    for (int i = 0; i < sum.n; i++, ty += lh) cat_draw_text(ft, sum.v[i], a.x, ty, i < nsum_lines ? th->text : th->hint);
    lines_free(&sum);
}

static int hist_key(ui_screen *s, cat_input_event *ev) {
    hist_t *h = s->priv;
    if (ev->repeated) return 1;
    switch (ev->button) {
    case CAT_BTN_LEFT: case CAT_BTN_RIGHT: case CAT_BTN_UP: case CAT_BTN_DOWN: case CAT_BTN_X: h->rng = 1 - h->rng; break;
    case CAT_BTN_B: ui_pop(); break;
    default: break;
    }
    return 1;
}

static void hist_free(ui_screen *s) {
    hist_t *h = s->priv;
    if (h) { free(h->v); cJSON_Delete(h->day); free(h); }
}

static cJSON *lm_hist_week;   /* `history --days 7` chờ bước đọc 24 giờ */

static void history_day_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *week = lm_hist_week;
    lm_hist_week = NULL;
    cJSON *day = app_json_or_error(j, tr("Lịch sử pin & nhiệt"));
    if (!day) { cJSON_Delete(week); return; }
    hist_t *h = calloc(1, sizeof *h);
    ui_screen *s = ui_screen_new("Lịch sử pin & nhiệt", NULL);
    if (!h || !s) { free(h); cJSON_Delete(week); cJSON_Delete(day); return; }
    cJSON *rows = jarr(week, "rows"), *r;
    int n = rows ? cJSON_GetArraySize(rows) : 0;
    h->v = calloc((size_t)(n ? n : 1), sizeof *h->v);
    for (int i = 0; rows && i < n; i++) {
        r = cJSON_GetArrayItem(rows, i);
        double ts = num_or_nan(r, "ts");
        if (isnan(ts)) continue;
        double pk = NAN;
        static const char *const ks[3] = { "soc_c", "gpu_c", "batt_c" };
        for (int k = 0; k < 3; k++) { double t = num_or_nan(r, ks[k]); if (!isnan(t) && (isnan(pk) || t > pk)) pk = t; }
        h->v[h->n++] = (samp_t){ ts, num_or_nan(r, "battery_pct"), pk };
    }
    h->now = (double)time(NULL);
    h->day = day;
    s->priv = h;
    s->d = week;
    s->draw = hist_draw;
    s->key_fn = hist_key;
    s->on_free = hist_free;
    s->footer[0] = (cat_footer_item){ .button = CAT_BTN_B, .label = tr("QUAY LẠI") };
    s->footer[1] = (cat_footer_item){ .button = CAT_BTN_X, .label = tr("ĐỔI KHOẢNG"), .is_confirm = true };
    s->nfooter = 2;
    ui_push(s);
}

static void history_week_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *week = app_json_or_error(j, tr("Lịch sử pin & nhiệt"));
    if (!week) return;
    lm_hist_week = week;
    cli_task(tr("Đang đọc lịch sử"), SHORT_TIMEOUT_MS, history_day_done, "", "history", "--days", "1", NULL);
}
void open_history(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc lịch sử"), SHORT_TIMEOUT_MS, history_week_done, "", "history", "--days", "7", NULL);
}

/* ============================================================ trạng thái agent */
static void build_agent(ui_screen *s) {
    cJSON *a = s->d;
    ui_hdr(s, tr("Chỉ đọc: màn này không ghi gì (lệnh agent status chỉ đọc khoá và agent_state.json)."));
    int running = jbool_d(a, "running", 0);
    const char *state = jstr(a, "state");     /* running | stopped | never; thiếu = engine cũ chỉ có "running" */
    int never = state && !strcmp(state, "never");
    ui_item *it = ui_add(s, "Agent", running ? tr("ĐANG CHẠY") : never ? tr("CHƯA TỪNG CHẠY") : tr("KHÔNG CHẠY"), running ? ST_GREEN : never ? ST_UNKNOWN : ST_YELLOW);
    if (running) {
        double pid = jnumd(a, "pid", 0);
        if (pid > 0) ui_exp(it, tr("Đang chạy (pid %.0f)."), pid); else ui_exp(it, tr("Đang chạy (pid ?)."));
    } else if (never) {
        ui_exp(it, tr("Agent CHƯA TỪNG CHẠY trên máy này (không có agent.lock lẫn agent_state.json)."));
        ui_exp(it, tr("Không có mẫu pin/nhiệt và không tự sao lưu khi thoát game cho tới khi bạn bật service."));
        ui_exp(it, AGENT_HINT);
    } else {
        ui_exp(it, tr("! Agent đã chạy trước đây nhưng KHÔNG chạy lúc này (không ai giữ khoá). Không có mẫu pin/nhiệt mới và không tự sao lưu khi thoát game."));
        ui_exp(it, AGENT_HINT);
        if (!state) ui_exp(it, tr("(Engine không báo 'state': không phân biệt được 'chưa từng chạy'.)"));
    }

    cJSON *lb = jget(a, "last_backup");
    if (cJSON_IsObject(lb) && jstr(lb, "at")) {
        int ok = jbool_d(lb, "success", 0);
        it = ui_add(s, tr("Tự sao lưu gần nhất"), jstr(lb, "at"), ok ? ST_GREEN : ST_RED);
        ui_exp(it, tr("Lúc: %s"), jstr(lb, "at"));
        ui_exp(it, "Snapshot: %s", jstrd(lb, "id", "None"));
        if (ok) ui_exp(it, tr("+ Thành công.")); else ui_exp(it, tr("!! Thất bại: %s"), jstrd(lb, "error", tr("không rõ nguyên nhân")));
    } else if (cJSON_IsString(lb) && lb->valuestring[0]) {
        it = ui_add(s, tr("Tự sao lưu gần nhất"), lb->valuestring, ST_UNKNOWN);
        ui_exp(it, "%s", lb->valuestring);
    } else {
        it = ui_add(s, tr("Tự sao lưu gần nhất"), tr("chưa có"), ST_UNKNOWN);
        ui_exp(it, tr("Chưa ghi nhận lần sao lưu tự động nào."));
        ui_exp(it, tr("Agent tự sao lưu SaveVault khi phát hiện bạn thoát game."));
    }

    cJSON *ls = jget(a, "last_sample");
    if (cJSON_IsObject(ls)) {
        double ts = jnumd(ls, "ts", 0);
        int age = (int)((time(NULL) - ts) / 60);
        char tag[48], when[40];
        snprintf(tag, sizeof tag, tr("%d phút trước"), age);
        fmt_time(when, sizeof when, ts, "%d/%m %H:%M:%S");
        it = ui_add(s, tr("Pin/nhiệt gần nhất"), tag, running && age <= 15 ? ST_GREEN : ST_YELLOW);
        ui_exp(it, tr("Lúc: %s"), when);
        char pb[32], t1[32], t2[32], t3[32];
        snprintf(pb, sizeof pb, "%s", tr("không đọc được"));
        snprintf(t1, sizeof t1, "%s", pb);
        snprintf(t2, sizeof t2, "%s", pb);
        snprintf(t3, sizeof t3, "%s", pb);
        if (cJSON_IsNumber(jget(ls, "battery_pct"))) snprintf(pb, sizeof pb, "%.0f%%", jnumd(ls, "battery_pct", 0));
        if (cJSON_IsNumber(jget(ls, "soc_c"))) snprintf(t1, sizeof t1, "%g°C", jnumd(ls, "soc_c", 0));
        if (cJSON_IsNumber(jget(ls, "gpu_c"))) snprintf(t2, sizeof t2, "%g°C", jnumd(ls, "gpu_c", 0));
        if (cJSON_IsNumber(jget(ls, "batt_c"))) snprintf(t3, sizeof t3, "%g°C", jnumd(ls, "batt_c", 0));
        ui_exp(it, "Pin: %s (%s)", pb, jstrd(ls, "status", "?"));
        ui_exp(it, "SoC: %s   GPU: %s   Pin: %s", t1, t2, t3);
        cJSON *sm = jget(a, "summary_7d");
        if (cJSON_IsObject(sm)) ui_exp(it, tr("7 ngày qua: %.0f mẫu."), jnumd(sm, "samples", 0));
        if (age > 15) ui_exp(it, tr("! Mẫu đã cũ: agent không còn ghi mẫu mới."));
    } else {
        it = ui_add(s, tr("Pin/nhiệt gần nhất"), tr("chưa có"), ST_UNKNOWN);
        ui_exp(it, tr("Chưa có mẫu nào trong 7 ngày qua."));
        ui_exp(it, AGENT_HINT);
    }
    {   /* kiểm tra toàn vẹn kho SaveVault (agent ghi) */
        vv_t vv;
        app_vv_eval(jget(a, "vault_verify"), &vv);
        if (!vv.level) {
            it = ui_add(s, tr("Kiểm tra toàn vẹn kho"), tr("chưa có"), ST_UNKNOWN);
            ui_exp(it, tr("Agent chưa ghi kết quả kiểm tra toàn vẹn SaveVault (không phải lỗi)."));
        } else {
            char ln[160], when[40];
            app_vv_line(&vv, ln, sizeof ln);
            it = ui_add(s, tr("Kiểm tra toàn vẹn kho"), vv.level == 3 ? tr("CÓ LỖI") : vv.level == 2 ? tr("cũ") : tr("ổn"),
                        vv.level == 3 ? ST_RED : vv.level == 2 ? ST_YELLOW : ST_GREEN);
            ui_exp(it, "%s", ln);
            if (vv.has_age) { fmt_time(when, sizeof when, vv.ts, "%d/%m/%Y %H:%M"); ui_exp(it, tr("Lúc: %s"), when); }
            if (vv.has_checked) ui_exp(it, tr("Đã kiểm: %.0f object"), vv.checked);
            ui_exp(it, tr("Hỏng: %d   Thiếu: %d%s"), vv.nbad, vv.nmissing, vv.partial ? tr("   (kiểm một phần)") : "");
        }
    }
    {   /* băm trước ROM nghi trùng */
        cJSON *pw = jget(a, "prewarm");
        if (cJSON_IsObject(pw) && cJSON_IsBool(jget(pw, "done"))) {
            int done = jbool_d(pw, "done", 0);
            double hashed, remaining, ts;
            it = ui_add(s, tr("Băm trước (hash)"), done ? "xong" : tr("đang làm"), done ? ST_GREEN : ST_YELLOW);
            if (done && jnum(pw, "hashed", &hashed)) ui_exp(it, tr("+ Băm trước: xong (%.0f file)"), hashed);
            else if (done) ui_exp(it, tr("+ Băm trước: xong"));
            else if (jnum(pw, "remaining", &remaining)) ui_exp(it, tr("Băm trước: đang làm, còn %.0f file"), remaining);
            else ui_exp(it, tr("Băm trước: đang làm"));
            ui_exp(it, tr("Agent băm sẵn ROM nghi trùng khi rảnh để lần quét Dọn ROM trùng chạy nhanh."));
            if (jnum(pw, "hashed", &hashed)) ui_exp(it, tr("Đã băm: %.0f file"), hashed);
            if (jnum(pw, "remaining", &remaining)) ui_exp(it, tr("Còn lại: %.0f file"), remaining);
            if (jnum(pw, "ts", &ts)) { char when[40]; fmt_time(when, sizeof when, ts, "%d/%m/%Y %H:%M"); ui_exp(it, tr("Cập nhật lúc: %s"), when); }
        } else {
            it = ui_add(s, tr("Băm trước (hash)"), tr("chưa có"), ST_UNKNOWN);
            ui_exp(it, tr("Agent chưa ghi trạng thái băm trước."));
        }
    }
    {   /* lần tự nạp lại gần nhất sau cập nhật */
        cJSON *rl = jget(a, "reload");
        if (cJSON_IsObject(rl) && jstr(rl, "to")) {
            double ts;
            it = ui_add(s, tr("Tự nạp lại gần nhất"), jstr(rl, "to"), ST_GREEN);
            ui_exp(it, tr("Lên bản: %s"), jstr(rl, "to"));
            ui_exp(it, tr("Từ bản: %s"), jstrd(rl, "from", tr("không rõ (lần đầu)")));
            if (jnum(rl, "ts", &ts)) { char when[40]; fmt_time(when, sizeof when, ts, "%d/%m/%Y %H:%M"); ui_exp(it, tr("Lúc: %s"), when); }
            ui_exp(it, tr("Agent tự nạp lại chính nó sau khi cập nhật LoongMate."));
        } else {
            it = ui_add(s, tr("Tự nạp lại gần nhất"), tr("chưa có"), ST_UNKNOWN);
            ui_exp(it, tr("Chưa ghi nhận lần agent tự nạp lại sau cập nhật nào — hoặc `agent status` của engine chưa trả mục 'reload' (agent_state.json có thể có)."));
        }
    }
    it = ui_add(s, tr("Tín hiệu thoát game"), "", ST_UNKNOWN);
    ui_exp(it, "%s", jstrd(a, "exit_signal", tr("không rõ")));
    ui_exp(it, "Log Leaf: %s (%s)", jstrd(a, "leaf_log", "?"), jbool_d(a, "leaf_log_exists", 0) ? tr("có") : tr("không thấy file"));
    it = ui_add(s, tr("Cách bật service"), "", ST_UNKNOWN);
    ui_exp(it, AGENT_HINT);
    ui_exp(it, tr("Service chạy nền: ghi mẫu pin/nhiệt mỗi 5 phút và tự sao lưu khi thoát game."));
    ui_exp(it, tr("Chỉ chạy một bản agent (khoá agent.lock)."));
}

static void agent_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *a = app_json_or_error(j, tr("Trạng thái agent"));
    if (!a) return;
    ui_screen *s = ui_screen_new("Trạng thái agent", build_agent);
    s->d = a;
    ui_push(s);
}
void open_agent(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc trạng thái agent"), SHORT_TIMEOUT_MS, agent_done, "", "agent", "status", NULL);
}

/* ============================================================ thiết bị trong mạng */
static cJSON *g_nodes;       /* kết quả `nodes list` của phiên */
static cJSON *g_node_res;    /* kết quả ping/kiểm cổng gần nhất theo node (chỉ trong bộ nhớ) */

static cJSON *find_node(const char *name) {
    cJSON *n;
    cJSON_ArrayForEach(n, jarr(g_nodes, "nodes")) if (!strcmp(jstrd(n, "name", ""), name)) return n;
    return NULL;
}

static void remember(const char *name, const char *key, const char *summary, const char *tag, const char *color, lines_t *lines) {
    if (!g_node_res) g_node_res = cJSON_CreateObject();
    cJSON *nd = cJSON_GetObjectItemCaseSensitive(g_node_res, name);
    if (!nd) nd = cJSON_AddObjectToObject(g_node_res, name);
    cJSON_DeleteItemFromObjectCaseSensitive(nd, key);
    cJSON *e = cJSON_AddObjectToObject(nd, key);
    char at[16];
    fmt_time(at, sizeof at, (double)time(NULL), "%H:%M:%S");
    cJSON_AddStringToObject(e, "summary", summary);
    cJSON_AddStringToObject(e, "tag", tag);
    cJSON_AddStringToObject(e, "color", color);
    cJSON_AddStringToObject(e, "at", at);
    cJSON *ls = cJSON_AddArrayToObject(e, "lines");
    if (lines) for (int i = 0; i < lines->n; i++) cJSON_AddItemToArray(ls, cJSON_CreateString(lines->v[i]));
}
static cJSON *node_res(const char *name, const char *key) { return jget(jget(g_node_res, name), key); }

static void fmt_mac(char *buf, size_t n, const char *mac) {
    size_t l = strlen(mac);
    if (l != 12) { snprintf(buf, n, "%s", mac); return; }
    snprintf(buf, n, "%.2s:%.2s:%.2s:%.2s:%.2s:%.2s", mac, mac + 2, mac + 4, mac + 6, mac + 8, mac + 10);
}

static void open_node(ui_screen *s, ui_item *it);
static void refresh_nodes(ui_screen *s, ui_item *it);

static void build_nodes(ui_screen *s) {
    cJSON *d = g_nodes;
    cJSON *nodes = jarr(d, "nodes"), *n;
    int total = nodes ? cJSON_GetArraySize(nodes) : 0, online = 0;
    cJSON_ArrayForEach(n, nodes) if (jbool_d(n, "online", 0)) online++;
    if (!jbool_d(d, "ok", 0)) {
        ui_hdr(s, "!! %s", jstrd(d, "error", tr("Chưa đọc được danh sách."))[0] ? jstrd(d, "error", "") : tr("Chưa đọc được danh sách."));
        ui_hdr(s, tr("Tailscale có đang chạy không? LoongMate chỉ đọc trạng thái, không quản lý daemon."));
    } else {
        cJSON *me = jget(d, "self");
        if (cJSON_IsObject(me)) ui_hdr(s, tr("Tailscale: %s • máy này: %s %s"), jstrd(d, "backend_state", "?"), jstrd(me, "name", "?"), jstrd(me, "ip", ""));
        else ui_hdr(s, "Tailscale: %s", jstrd(d, "backend_state", "?"));
        ui_hdr(s, tr("%d máy khác, %d đang online."), total, online);
    }
    ui_item *it = ui_add(s, tr("Làm mới danh sách"), "", ST_UNKNOWN);
    ui_exp(it, tr("Đọc lại trạng thái các máy trong tailnet (chạy nền)."));
    ui_act(it, 0, tr("LÀM MỚI"), refresh_nodes);
    const char *grp = NULL;
    cJSON_ArrayForEach(n, nodes) {
        int fav = jbool_d(n, "favorite", 0), on = jbool_d(n, "online", 0);
        const char *g = fav ? tr("Yêu thích") : (on ? "Online" : "Offline");
        if (!grp || strcmp(g, grp)) { grp = g; ui_head(s, "%s", g); }
        const char *name = jstrd(n, "name", "?"), *ip = jstr(n, "ip"), *os = jstr(n, "os"), *path = jstr(n, "path"), *ls = jstr(n, "last_seen"), *mac = jstr(n, "mac");
        it = ui_add(s, name, on ? "online" : "offline", on ? ST_GREEN : ST_UNKNOWN);
        ui_arg(it, name);
        ui_exp(it, "IP: %s", ip && ip[0] ? ip : tr("(không có)"));
        ui_exp(it, tr("Hệ điều hành: %s"), os && os[0] ? os : tr("không rõ"));
        if (on) ui_exp(it, tr("Trạng thái: ONLINE"));
        else if (ls && ls[0]) ui_exp(it, tr("Trạng thái: OFFLINE (thấy lần cuối %s)"), ls);
        else ui_exp(it, tr("Trạng thái: OFFLINE"));
        if (path && !strcmp(path, "direct")) ui_exp(it, tr("Đường đi lần cuối biết: trực tiếp (muốn đo thật dùng Ping)"));
        else if (path && !strcmp(path, "relay")) ui_exp(it, tr("Đường đi lần cuối biết: qua relay %s (muốn đo thật dùng Ping)"), jstrd(n, "relay", "?"));
        else ui_exp(it, tr("Đường đi: không rõ (muốn đo thật dùng Ping)"));
        ui_exp(it, fav ? tr("Yêu thích: có") : tr("Yêu thích: không"));
        if (mac && mac[0]) { char m[40]; fmt_mac(m, sizeof m, mac); ui_exp(it, "MAC (cho Wake-on-LAN): %s", m); }
        else ui_exp(it, tr("MAC: chưa lưu (cần để dùng Wake-on-LAN)"));
        static const char *const keys[2] = { "ping", "probe" }, *const labs[2] = { "Ping", "Kiểm cổng" };   /* no-tr */   /* tr() ở nơi dùng */
        for (int k = 0; k < 2; k++) {
            cJSON *r = node_res(name, keys[k]);
            if (r) ui_exp(it, tr("%s gần nhất: %s"), tr(labs[k]), jstrd(r, "summary", "?"));
        }
        ui_act(it, 0, tr("MỞ"), open_node);
    }
    if (jbool_d(d, "ok", 0) && !total) ui_head(s, tr("(Tailnet chưa có máy khác)"));
}

static void nodes_list_done(lm_job *j, const char *arg) {
    int push = atoi(arg);
    cJSON *r = app_json_or_error(j, tr("Thiết bị trong mạng"));
    if (!r) return;
    cJSON_Delete(g_nodes);
    g_nodes = r;
    if (push) ui_push(ui_screen_new("Thiết bị trong mạng", build_nodes));
    else ui_rebuild_all();
}
void open_nodes(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc danh sách thiết bị"), SHORT_TIMEOUT_MS, nodes_list_done, "1", "nodes", "list", NULL);
}
static void refresh_nodes(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc danh sách thiết bị"), SHORT_TIMEOUT_MS, nodes_list_done, "0", "nodes", "list", NULL);
}

static void act_ping(ui_screen *s, ui_item *it);
static void act_probe(ui_screen *s, ui_item *it);
static void act_fav(ui_screen *s, ui_item *it);
static void act_wol(ui_screen *s, ui_item *it);

static void build_node(ui_screen *s) {
    const char *name = s->key;
    cJSON *n = find_node(name);
    if (!n) {
        ui_hdr(s, tr("!! '%s' không còn trong danh sách — quay lại và làm mới."), name);
        return;
    }
    const char *ip = jstr(n, "ip"), *os = jstr(n, "os"), *mac = jstr(n, "mac");
    int on = jbool_d(n, "online", 0), fav = jbool_d(n, "favorite", 0);
    ui_hdr(s, "%s • %s • %s", ip && ip[0] ? ip : tr("không có IP"), on ? "ONLINE" : "OFFLINE", os && os[0] ? os : tr("OS không rõ"));
    cJSON *pg = node_res(name, "ping"), *pb = node_res(name, "probe"), *ln;
    ui_item *it = ui_add(s, "Ping", pg ? jstrd(pg, "tag", "") : tr("chưa đo"), pg ? ui_tagc(jstr(pg, "color")) : ST_UNKNOWN);
    ui_arg(it, name);
    ui_exp(it, tr("Chạy `tailscale ping` một lần: đo thật, không phải trạng thái cũ."));
    ui_exp(it, tr("Trong tailnet không có ICMP ping; đây là ping của Tailscale."));
    if (pg) ui_exp(it, tr("Kết quả (%s): %s"), jstrd(pg, "at", "?"), jstrd(pg, "summary", ""));
    ui_act(it, 0, "PING", act_ping);

    it = ui_add(s, tr("Kiểm cổng"), pb ? jstrd(pb, "tag", "") : tr("chưa đo"), pb ? ui_tagc(jstr(pb, "color")) : ST_UNKNOWN);
    ui_arg(it, name);
    ui_exp(it, tr("Thử nối TCP tới SSH (22), SMB (445), Sunshine (47989), RDP (3389) qua SOCKS5 của Tailscale."));
    ui_exp(it, tr("'Không phản hồi' thường là tường lửa chặn hoặc chưa có dịch vụ; 'đóng' là máy chủ động từ chối."));
    ui_exp(it, tr("Mất tới vài giây."));
    if (pb) {
        ui_exp(it, tr("Kết quả (%s):"), jstrd(pb, "at", "?"));
        cJSON_ArrayForEach(ln, jarr(pb, "lines")) if (cJSON_IsString(ln)) ui_exp(it, "%s", ln->valuestring);
    }
    ui_act(it, 0, tr("KIỂM CỔNG"), act_probe);

    it = ui_add(s, tr("Yêu thích"), fav ? tr("có") : tr("không"), fav ? ST_GREEN : ST_UNKNOWN);
    ui_arg(it, name);
    ui_exp(it, tr("Máy yêu thích được đưa lên đầu danh sách. Lưu trong config.json của LoongMate."));
    ui_exp(it, tr("A — bật/tắt."));
    ui_act(it, 0, tr("BẬT/TẮT"), act_fav);

    it = ui_add(s, "Wake-on-LAN", mac && mac[0] ? tr("có MAC") : tr("chưa có MAC"), mac && mac[0] ? ST_YELLOW : ST_UNKNOWN);
    ui_arg(it, name);
    ui_exp(it, tr("Gửi gói đánh thức UDP broadcast tới MAC đã lưu."));
    if (mac && mac[0]) { char m[40]; fmt_mac(m, sizeof m, mac); ui_exp(it, "MAC: %s", m); }
    else ui_exp(it, tr("! Chưa lưu MAC cho máy này. Lưu bằng lệnh: loongmate nodes mac %s <MAC>"), name);
    ui_exp(it, tr("! CHƯA KIỂM CHỨNG trên Wi-Fi của máy: nhiều AP chặn broadcast; máy đích phải bật WOL trong BIOS và driver."));
    ui_exp(it, tr("Gói đi qua LAN, KHÔNG qua tailnet. 'Đã gửi' không có nghĩa là máy đã dậy."));
    if (mac && mac[0]) ui_act(it, 0, tr("GỬI GÓI ĐÁNH THỨC"), act_wol);
}

static void open_node(ui_screen *s, ui_item *it) {
    (void)s;
    ui_screen *ns = ui_screen_new(it->arg, build_node);
    snprintf(ns->title, sizeof ns->title, "%.80s", it->arg);
    snprintf(ns->key, sizeof ns->key, "%s", it->arg);
    ui_push(ns);
}

static void ping_done(lm_job *j, const char *name) {
    cJSON *r = app_json_or_error(j, "Ping");
    if (!r) return;
    lines_t L = {0};
    char ttl[200];
    snprintf(ttl, sizeof ttl, "Ping %s", name);
    if (jbool_d(r, "ok", 0)) {
        char tag[40];
        snprintf(tag, sizeof tag, "%g ms", jnumd(r, "latency_ms", 0));
        remember(name, "ping", jstrd(r, "message", ""), tag, "green", NULL);
        lines_add(&L, "+ %s", jstrd(r, "message", ""));
        if (!strcmp(jstrd(r, "via", ""), "direct")) lines_add(&L, tr("Đường: trực tiếp"));
        else lines_add(&L, tr("Đường: qua relay DERP %s"), jstrd(r, "derp", "?"));
    } else {
        remember(name, "ping", jstrd(r, "message", ""), tr("lỗi"), "red", NULL);
        lines_add(&L, tr("!! Không ping được:"));
        lines_add(&L, "%s", jstrd(r, "message", ""));
    }
    cJSON_Delete(r);
    ui_info(ttl, &L);
    ui_rebuild_all();
}
static void act_ping(ui_screen *s, ui_item *it) {
    (void)s;
    cli_task(tr("Đang ping"), SHORT_TIMEOUT_MS, ping_done, it->arg, "nodes", "ping", "--", it->arg, NULL);
}

static const char *port_state_vi(const char *st) {
    if (!strcmp(st, "open")) return tr("MỞ");
    if (!strcmp(st, "closed")) return tr("đóng");
    if (!strcmp(st, "unreachable")) return tr("không tới được");
    if (!strcmp(st, "timeout")) return tr("không phản hồi");
    if (!strcmp(st, "error")) return tr("lỗi");
    return st;
}
static void probe_done(lm_job *j, const char *name) {
    cJSON *r = app_json_or_error(j, tr("Kiểm cổng"));
    if (!r) return;
    lines_t L = {0}, store = {0};
    char ttl[240];
    if (!jbool_d(r, "ok", 0)) {
        remember(name, "probe", jstrd(r, "error", ""), tr("lỗi"), "red", NULL);
        snprintf(ttl, sizeof ttl, tr("Kiểm cổng %s"), name);
        lines_add(&L, "!! %s", jstrd(r, "error", tr("lỗi không rõ")));
    } else {
        cJSON *p, *ports = jarr(r, "ports");
        int nopen = 0, np = ports ? cJSON_GetArraySize(ports) : 0;
        cJSON_ArrayForEach(p, ports) {
            const char *st = jstrd(p, "state", "?");
            int open = !strcmp(st, "open");
            if (open) nopen++;
            lines_add(&store, "%s: %s (%.0f ms) — %s", jstrd(p, "label", "?"), port_state_vi(st), jnumd(p, "ms", 0), jstrd(p, "detail", ""));
            lines_add(&L, "%s%s", open ? "+ " : "", store.v[store.n - 1]);
        }
        char sum[48], tag[32];
        snprintf(sum, sizeof sum, tr("%d/%d cổng mở"), nopen, np);
        snprintf(tag, sizeof tag, tr("%d/%d mở"), nopen, np);
        remember(name, "probe", sum, tag, nopen ? "green" : "yellow", &store);
        snprintf(ttl, sizeof ttl, tr("Kiểm cổng %s (%s)"), name, jstrd(r, "host", "?"));
    }
    lines_free(&store);
    cJSON_Delete(r);
    ui_info(ttl, &L);
    ui_rebuild_all();
}
static void act_probe(ui_screen *s, ui_item *it) {
    (void)s;
    cli_task(tr("Đang kiểm cổng"), SHORT_TIMEOUT_MS, probe_done, it->arg, "nodes", "probe", "--", it->arg, NULL);
}

static void fav_done(lm_job *j, const char *name) {
    (void)name;
    cJSON *r = app_json_or_error(j, tr("Yêu thích"));
    if (!r) return;
    if (!jbool_d(r, "success", 0)) {
        ui_info1(tr("Lỗi"), tr("!! Không lưu được yêu thích: %s"), jstrd(r, "message", tr("không rõ")));
        cJSON_Delete(r);
        return;
    }
    cJSON_Delete(r);
    cli_task(tr("Đang đọc danh sách thiết bị"), SHORT_TIMEOUT_MS, nodes_list_done, "0", "nodes", "list", NULL);
}
static void act_fav(ui_screen *s, ui_item *it) {
    (void)s;
    cJSON *n = find_node(it->arg);
    if (!n) return;
    cli_task(tr("Đang lưu yêu thích"), SHORT_TIMEOUT_MS, fav_done, it->arg, "nodes", "fav", "--", it->arg, jbool_d(n, "favorite", 0) ? "off" : "on", NULL);
}

static void wol_done(lm_job *j, const char *name) {
    cJSON *r = app_json_or_error(j, "Wake-on-LAN");
    if (!r) return;
    lines_t L = {0};
    char ttl[200];
    snprintf(ttl, sizeof ttl, "Wake-on-LAN %s", name);
    lines_add(&L, "%s%s", jbool_d(r, "ok", jbool_d(r, "success", 0)) ? "+ " : "!! ", jstrd(r, "message", ""));
    lines_add(&L, tr("! Chưa kiểm chứng trên Wi-Fi của máy: hãy kiểm tra máy đích có dậy thật không."));
    cJSON_Delete(r);
    ui_info(ttl, &L);
}
static void do_wol(const char *name) {
    cli_task(tr("Đang gửi gói đánh thức"), SHORT_TIMEOUT_MS, wol_done, name, "nodes", "wake", "--", name, NULL);
}
static void act_wol(ui_screen *s, ui_item *it) {
    (void)s;
    cJSON *n = find_node(it->arg);
    const char *mac = jstr(n, "mac");
    if (!mac || !mac[0]) return;
    char m[40], ttl[200];
    fmt_mac(m, sizeof m, mac);
    snprintf(ttl, sizeof ttl, "Wake-on-LAN %s", it->arg);
    lines_t L = {0};
    lines_add(&L, tr("Sẽ gửi gói đánh thức tới MAC %s qua broadcast theo cấu hình (mặc định 255.255.255.255), cổng UDP 9."), m);
    lines_add(&L, tr("Gói đi qua LAN (Wi-Fi của máy), KHÔNG qua tailnet."));
    lines_add(&L, tr("! CHƯA KIỂM CHỨNG trên Wi-Fi của máy: nhiều AP chặn broadcast / cô lập client."));
    lines_add(&L, tr("! Máy đích phải bật Wake-on-LAN trong BIOS và driver."));
    lines_add(&L, tr("'Đã gửi' chỉ nghĩa là gói đã rời máy này, không chứng tỏ máy đích dậy."));
    ui_confirm(ttl, &L, tr("Gửi"), do_wol, it->arg);
}

/* ============================================================ kiểm tra nút */
typedef struct { cat_button last; int same; char shown[160]; } bt_t;

static const char *button_name(cat_button b) {
    switch (b) {
    case CAT_BTN_UP: return tr("D-pad LÊN");
    case CAT_BTN_DOWN: return tr("D-pad XUỐNG");
    case CAT_BTN_LEFT: return tr("D-pad TRÁI");
    case CAT_BTN_RIGHT: return tr("D-pad PHẢI");
    case CAT_BTN_A: return "A";
    case CAT_BTN_B: return "B";
    case CAT_BTN_X: return "X";
    case CAT_BTN_Y: return "Y";
    case CAT_BTN_L1: return "L1";
    case CAT_BTN_L2: return "L2";
    case CAT_BTN_R1: return "R1";
    case CAT_BTN_R2: return "R2";
    case CAT_BTN_START: return "START";
    case CAT_BTN_SELECT: return "SELECT";
    case CAT_BTN_MENU: return "MENU";
    case CAT_BTN_POWER: return "POWER";
    case CAT_BTN_QUIT: return "QUIT";
    case CAT_BTN_STICK: return "STICK";
    default: return "?";
    }
}

static int bt_key(ui_screen *s, cat_input_event *ev) {
    bt_t *b = s->priv;
    if (ev->repeated) return 1;
    b->same = ev->button == b->last ? b->same + 1 : 1;
    b->last = ev->button;
    snprintf(b->shown, sizeof b->shown, tr("nút %s"), button_name(ev->button));
    fprintf(stderr, tr("[LoongMate][kiểm tra nút] %s (Catastrophe cat_button=%d)\n"), b->shown, (int)ev->button);
    if (b->same >= 3) ui_pop();
    return 1;
}
static void bt_draw(ui_screen *s, SDL_Rect a) {
    bt_t *b = s->priv;
    cat_theme *th = cat_get_theme();
    TTF_Font *fx = cat_get_font(CAT_FONT_EXTRA_LARGE), *fb = cat_get_font(CAT_FONT_SMALL), *ft = cat_get_font(CAT_FONT_TINY);
    int y = a.y + cat_device_scale(4);
    lines_t t = {0};
    ui_wrap(fb, tr("Bấm bất kỳ nút/hướng nào trên máy; tên nút hiện ở đây và được ghi vào log."), a.w, &t);
    for (int i = 0; i < t.n; i++, y += TTF_FontHeight(fb)) cat_draw_text(fb, t.v[i], a.x, y, th->hint);
    lines_free(&t);
    y += cat_device_scale(24);
    if (!b->last) cat_draw_text(fx, tr("Chưa bấm gì..."), a.x + cat_device_scale(10), y, th->hint);
    else {
        cat_draw_text(fx, b->shown, a.x + cat_device_scale(10), y, ui_color(ST_GREEN));
        y += TTF_FontHeight(fx) + cat_device_scale(10);
        char ln[100];
        snprintf(ln, sizeof ln, tr("Lần bấm liên tiếp giống nhau: %d/3"), b->same);
        cat_draw_text(fb, ln, a.x + cat_device_scale(10), y, th->text);
    }
    static const char *const notes[] = {
        "Mục đích: xem Catastrophe nhận nút nào (A/B/X/Y/L1/R1/D-pad...) khi bạn bấm.",
        "Số hiệu joystick thô không hiện ở bản native (toolkit đã đổi sang tên nút); muốn dò số thô hãy dùng Kiểm tra nút của giao diện Python.",
        "Thoát: bấm CÙNG một nút 3 lần liên tiếp (kể cả nút B).",
    };
    lines_t w = {0};
    for (int i = 0; i < 3; i++) ui_wrap(ft, tr(notes[i]), a.w, &w);
    int lh = TTF_FontHeight(ft) + 2;
    y = a.y + a.h - w.n * lh;
    for (int k = 0; k < w.n; k++, y += lh) cat_draw_text(ft, w.v[k], a.x, y, th->hint);
    lines_free(&w);
}
static void bt_free(ui_screen *s) { free(s->priv); }

void open_buttontest(ui_screen *sc, ui_item *it) {
    (void)sc; (void)it;
    ui_screen *s = ui_screen_new("Kiểm tra nút", NULL);
    s->priv = calloc(1, sizeof(bt_t));
    s->draw = bt_draw;
    s->key_fn = bt_key;
    s->on_free = bt_free;
    s->footer[0] = (cat_footer_item){ .button = CAT_BTN_B, .label = tr("BẤM 3 LẦN ĐỂ THOÁT") };
    s->nfooter = 1;
    ui_push(s);
}
