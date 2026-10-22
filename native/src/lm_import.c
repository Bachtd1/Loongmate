/* LoongMate native — Library > Nhập game (Local Importer). Mọi việc đi qua `cli.py import ... --json`; native không đọc/giải nén/ghi
 * file người dùng. Luồng: `import scan` -> danh sách (X chọn nhiều, A trên mục mơ hồ = chọn hệ) -> `import plan --select ... --plan-out`
 * (chọn thẻ, kiểm trùng; thẻ phụ không dùng được thì HỎI, không tự dùng thẻ Leaf) -> màn kế hoạch (đổi hành động khi trùng tên, xác nhận BIOS
 * riêng) -> hộp xác nhận mặc định Huỷ -> `import apply --plan FILE --yes ...` -> kết quả, đứng yên tại danh sách (đã quét lại).
 * Chỉ nhập nội dung người dùng tự đưa vào: không có chức năng tải từ internet. */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { char sys[24]; unsigned char mark; } iu_t;   /* trạng thái người dùng của MỘT mục scan (theo chỉ số) */

static cJSON *g_scan;            /* `import scan --json` của phiên (sở hữu) */
static iu_t *g_u;
static int g_nu;
static int g_delete_src;         /* "Dọn file đã nhập": xoá thay vì chuyển vào Import/.done/ */
static cJSON *g_plan;            /* `import plan --json` đang xem = đúng bản đã ghi vào file kế hoạch (sở hữu) */
static unsigned char *g_pact;    /* hành động khi trùng tên theo chỉ số mục kế hoạch: 0 keep 1 replace 2 copy 3 cancel */
static unsigned char *g_pbios;   /* 1 = đã xác nhận nhập BIOS này */
static int g_npu;
static lines_t g_pending;        /* kết quả đã dựng, chờ hiện sau khi quét lại danh sách */
static char g_pending_title[64];

static const char *const ACT_KEY[4] = { "keep", "replace", "copy", "cancel" };
static const char *const ACT_VI[4] = { "giữ bản cũ, không cài", "thay (bản cũ vào .loongmate-import/trash/)", "cài thêm bản sao", "huỷ mục này" };

static const char *mbs(double bytes) { static char b[4][32]; static int i; i = (i + 1) & 3; ui_fmt_bytes(b[i], sizeof b[i], bytes); return b[i]; }
static cJSON *scan_units(void) { return jarr(g_scan, "units"); }
static cJSON *unit_at(int i) { return cJSON_GetArrayItem(scan_units(), i); }
static int is_markable(const cJSON *u) {
    const char *st = jstrd(u, "status", "");
    return !strcmp(st, "ready") || !strcmp(st, "needs_choice");
}
static int n_marked(void) { int n = 0; for (int i = 0; i < g_nu; i++) n += g_u[i].mark; return n; }

/* nhận kết quả scan mới; giữ dấu chọn/hệ đã chọn của mục còn tồn tại (so theo id) */
static void adopt_scan(cJSON *r) {
    cJSON *old = g_scan;
    iu_t *oldu = g_u;
    int oldn = g_nu;
    g_scan = r;
    g_nu = jarr_n(r, "units");
    g_u = calloc((size_t)(g_nu ? g_nu : 1), sizeof *g_u);
    for (int i = 0; i < g_nu && g_u; i++) {
        const char *id = jstrd(unit_at(i), "id", "");
        for (int k = 0; k < oldn; k++) {
            cJSON *ou = cJSON_GetArrayItem(jarr(old, "units"), k);
            if (!strcmp(jstrd(ou, "id", "\1"), id)) { g_u[i] = oldu[k]; break; }
        }
    }
    free(oldu);
    cJSON_Delete(old);
}

static void show_pending(void) {
    if (g_pending.n) { lines_t L = g_pending; g_pending = (lines_t){0}; ui_info(g_pending_title, &L); }
}

static void select_unit(ui_screen *s, int idx) {
    for (int i = 0; i < s->n; i++)
        if (!s->items[i]->head && s->items[i]->iarg == idx && s->items[i]->arg[0] == 'u') { s->sel = i; s->exp_scroll = 0; return; }
}

/* ============================================================ danh sách */
static void build_import(ui_screen *s);
static void do_rescan(ui_screen *s, ui_item *it);

static void scan_done(lm_job *j, const char *arg) {
    int push = atoi(arg);
    cJSON *r = app_json_or_error(j, tr("Nhập game"));
    if (!r) return;
    adopt_scan(r);
    if (push) ui_push(ui_screen_new("NHẬP GAME", build_import)); else ui_rebuild_all();
    show_pending();
}

void open_import(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    for (int i = 0; i < g_nu; i++) g_u[i].mark = 0;      /* mở lại màn = bắt đầu chọn mới */
    cli_task(tr("Đang quét thư mục Import/"), SHORT_TIMEOUT_MS, scan_done, "1", "import", "scan", NULL);
}
static void do_rescan(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang quét lại Import/"), SHORT_TIMEOUT_MS, scan_done, "0", "import", "scan", NULL);
}

static void mkdir_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("Thư mục nhập")), *d;
    if (!r) return;
    lines_t L = {0};
    cJSON_ArrayForEach(d, jarr(r, "dirs")) lines_add(&L, "%s%s", jbool_d(d, "ok", 0) ? "+ " : "!! ", jstrd(d, "message", "?"));
    lines_add(&L, "%s", "");
    lines_add(&L, tr("Chép file ROM/.zip vào thư mục trên (qua máy tính, USB hoặc pc/import_push.py), rồi chọn Quét lại."));
    cJSON_Delete(r);
    snprintf(g_pending_title, sizeof g_pending_title, tr("Thư mục nhập"));
    lines_free(&g_pending);
    g_pending = L;
    cli_task(tr("Đang quét lại Import/"), SHORT_TIMEOUT_MS, scan_done, "0", "import", "scan", NULL);
}
static void do_mkdir(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang tạo thư mục Import/"), SHORT_TIMEOUT_MS, mkdir_done, "", "import", "mkdir", NULL);
}

static void toggle_delete(ui_screen *s, ui_item *it) {
    (void)it;
    g_delete_src = !g_delete_src;
    ui_rebuild(s);
}

static void do_toggle(ui_screen *s, ui_item *it) {
    int i = it->iarg;
    if (i < 0 || i >= g_nu) return;
    g_u[i].mark = !g_u[i].mark;
    ui_rebuild(s);
    select_unit(s, i);
}

static void clear_marks(ui_screen *s, ui_item *it) {
    (void)it;
    for (int i = 0; i < g_nu; i++) g_u[i].mark = 0;
    s->sel = 0;
    ui_rebuild(s);
}
/* Chọn mọi mục nhập được (sẵn sàng / cần chọn hệ). BIOS không tự chọn: luôn xác nhận riêng. Vẫn qua kế hoạch + xác nhận. */
static void select_all(ui_screen *s, ui_item *it) {
    (void)it;
    cJSON *units = scan_units();
    for (int i = 0; i < g_nu; i++) if (is_markable(cJSON_GetArrayItem(units, i))) g_u[i].mark = 1;
    s->sel = 0;
    ui_rebuild(s);
}

/* ---- chọn hệ máy cho mục mơ hồ (không đoán) */
static void pick_system(ui_screen *s, ui_item *it) {
    (void)s;
    int i = it->iarg;
    if (i < 0 || i >= g_nu) return;
    snprintf(g_u[i].sys, sizeof g_u[i].sys, "%s", it->arg);
    g_u[i].mark = 1;
    ui_pop();
    ui_rebuild_all();
}
static void build_choose(ui_screen *s) {
    int idx = atoi(s->key);
    cJSON *u = unit_at(idx), *c;
    ui_hdr(s, "'%s': %s", jstrd(u, "name", "?"), jstrd(u, "evidence", ""));
    ui_hdr(s, tr("Chọn hệ máy đúng. Chọn sai thì game nằm nhầm thư mục."));
    cJSON_ArrayForEach(c, jarr(u, "candidates")) {
        if (!cJSON_IsString(c)) continue;
        ui_item *it = ui_add(s, c->valuestring, tr("chọn"), ST_GREEN);
        ui_exp(it, tr("Cài vào Roms/%s/."), c->valuestring);
        ui_act(it, 0, tr("CHỌN"), pick_system);
        it->iarg = idx;
        ui_arg(it, c->valuestring);
    }
}
static void open_choose(ui_screen *s, ui_item *it) {
    (void)s;
    ui_screen *c = ui_screen_new("CHỌN HỆ MÁY", build_choose);
    if (!c) return;
    snprintf(c->key, sizeof c->key, "%d", it->iarg);
    ui_push(c);
}

/* ---- danh sách chính */
static void make_plan(const char *pref, int only);   /* only >= 0: chỉ mục đó; -1: các mục đã chọn */
static void plan_marked(ui_screen *s, ui_item *it) { (void)s; (void)it; make_plan("auto", -1); }
/* A trên một mục: đã có mục được chọn (X) thì nhập TẤT CẢ mục đã chọn, gồm cả mục này; chưa chọn gì thì chỉ mục này. */
static void plan_one(ui_screen *s, ui_item *it) {
    (void)s;
    if (n_marked() && it->iarg >= 0 && it->iarg < g_nu && is_markable(unit_at(it->iarg))) {
        g_u[it->iarg].mark = 1;
        make_plan("auto", -1);
    } else make_plan("auto", it->iarg);
}

/* argv động: chuỗi strdup, giải phóng sau khi tiến trình con đã được tạo */
typedef struct { char **v; int n, cap; } sv_t;
static void sv_add(sv_t *a, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void sv_add(sv_t *a, const char *fmt, ...) {
    if (a->n >= a->cap) {
        int nc = a->cap ? a->cap * 2 : 32;
        char **nv = realloc(a->v, (size_t)nc * sizeof *nv);
        if (!nv) return;
        a->v = nv; a->cap = nc;
    }
    va_list ap;
    va_start(ap, fmt);
    char *t = NULL;
    if (vasprintf(&t, fmt, ap) < 0) t = NULL;
    va_end(ap);
    if (t) a->v[a->n++] = t;
}
static void sv_free(sv_t *a) { for (int i = 0; i < a->n; i++) free(a->v[i]); free(a->v); *a = (sv_t){0}; }

/* ---- giải nén archive (.7z/.rar/.tar.*...): `import unpack PATH --yes --auto-password` thử các mật khẩu gợi ý (chú thích archive,
 * tên file, .txt cạnh archive, Import/passwords.txt, mật khẩu đã dùng) — không dò. Không ra thì màn MẬT KHẨU: chọn ứng viên hoặc gõ. */
static char g_unpack_path[1024];
static cJSON *g_pw;              /* kết quả `import unpack` báo needs_password (sở hữu): candidates để chọn */
static void build_password(ui_screen *s);
static void run_unpack(const char *password);

static void unpack_done(lm_job *j, const char *arg) {
    cJSON *r = app_json_or_error(j, tr("Giải nén"));
    if (!r) return;
    int on_pw_screen = arg[0] == 'p';
    if (jbool_d(r, "needs_password", 0)) {
        cJSON_Delete(g_pw);
        g_pw = r;
        if (on_pw_screen) {
            ui_rebuild_all();
            if (jbool_d(r, "wrong_password", 0)) ui_info1(tr("Giải nén"), "%s", tr("Sai mật khẩu — chọn mật khẩu khác hoặc gõ lại."));
        } else ui_push(ui_screen_new("MẬT KHẨU ARCHIVE", build_password));
        return;
    }
    int ok = jbool_d(r, "success", 0);
    lines_t L = {0};
    lines_add(&L, "%s%s", ok ? "+ " : "!! ", jstrd(r, "message", "?"));
    if (ok) lines_add(&L, tr("Các file giải ra hiện trong danh sách Nhập game: chọn rồi nhập như bình thường."));
    cJSON_Delete(r);
    if (on_pw_screen) { cJSON_Delete(g_pw); g_pw = NULL; ui_pop(); }
    snprintf(g_pending_title, sizeof g_pending_title, tr("Giải nén"));
    lines_free(&g_pending);
    g_pending = L;
    cli_task(tr("Đang quét lại Import/"), SHORT_TIMEOUT_MS, scan_done, "0", "import", "scan", NULL);
}

static void run_unpack(const char *password) {
    sv_t a = {0};
    sv_add(&a, "import"); sv_add(&a, "unpack"); sv_add(&a, "%s", g_unpack_path); sv_add(&a, "--yes"); sv_add(&a, "--progress");
    if (password) { sv_add(&a, "--password"); sv_add(&a, "%s", password); }
    else sv_add(&a, "--auto-password");
    cli_task_v(password ? tr("Đang thử mật khẩu và giải nén") : tr("Đang giải nén"), LONG_TIMEOUT_MS, unpack_done,
               g_pw ? "p" : "l", (const char *const *)a.v, a.n);
    sv_free(&a);
}

static void do_unpack(ui_screen *s, ui_item *it) {
    (void)s;
    cJSON *u = unit_at(it->iarg);
    if (!u || !jstr(u, "archive")) return;
    snprintf(g_unpack_path, sizeof g_unpack_path, "%s", jstr(u, "archive"));
    cJSON_Delete(g_pw);
    g_pw = NULL;
    run_unpack(NULL);
}

static void pw_try(ui_screen *s, ui_item *it) { (void)s; run_unpack(it->arg); }
static void pw_typed(const char *text, const char *arg) { (void)arg; run_unpack(text); }
static void pw_type(ui_screen *s, ui_item *it) { (void)s; (void)it; ui_osk(tr("Gõ mật khẩu archive"), "", 128, pw_typed, ""); }
static void pw_screen_free(ui_screen *s) { (void)s; cJSON_Delete(g_pw); g_pw = NULL; }

static void build_password(ui_screen *s) {
    cJSON *c;
    const char *nm = strrchr(g_unpack_path, '/');
    s->on_free = pw_screen_free;
    ui_hdr(s, "%s", nm ? nm + 1 : g_unpack_path);
    ui_hdr(s, "%s", jstrd(g_pw, "message", ""));
    ui_hdr(s, tr("Mật khẩu gợi ý lấy từ chú thích archive, tên file, file .txt cạnh archive và Import/passwords.txt."));
    cJSON_ArrayForEach(c, jarr(g_pw, "candidates")) {
        const char *pw = jstr(c, "password");
        if (!pw) continue;
        ui_item *it = ui_add(s, pw, jstrd(c, "source", ""), ST_UNKNOWN);
        ui_exp(it, tr("Nguồn: %s"), jstrd(c, "source", "?"));
        ui_exp(it, tr("A — giải nén bằng mật khẩu này (sai thì không ghi gì)."));
        ui_act(it, 0, tr("THỬ"), pw_try);
        ui_arg(it, pw);
    }
    if (!jarr_n(g_pw, "candidates")) {
        ui_item *it = ui_add(s, tr("Không tìm thấy mật khẩu gợi ý"), "", ST_UNKNOWN);
        ui_exp(it, tr("Người đăng thường ghi mật khẩu ở trang tải, trong chú thích archive hoặc file .txt đi kèm."));
        ui_exp(it, tr("Chép file .txt đó vào cạnh archive, hoặc ghi mật khẩu vào Import/passwords.txt (mỗi dòng một), rồi giải nén lại."));
    }
    ui_item *it = ui_add(s, tr("Gõ mật khẩu…"), tr("bàn phím"), ST_GREEN);
    ui_exp(it, tr("Nhập mật khẩu bằng bàn phím trên màn hình (chữ không dấu)."));
    ui_exp(it, tr("Mật khẩu đúng được nhớ để lần sau tự thử trước."));
    ui_act(it, 0, tr("GÕ"), pw_type);
}

static void unit_tag(const cJSON *u, int i, char *tag, size_t n, int *color) {
    const char *st = jstrd(u, "status", "");
    *color = ST_UNKNOWN;
    if (g_u[i].sys[0]) { snprintf(tag, n, "%s", g_u[i].sys); *color = ST_GREEN; }
    else if (!strcmp(st, "blocked")) { snprintf(tag, n, tr("bị chặn")); *color = ST_RED; }
    else if (!strcmp(st, "needs_unpack")) { snprintf(tag, n, tr("giải nén")); *color = ST_YELLOW; }
    else if (!strcmp(st, "unsupported")) { snprintf(tag, n, tr("chưa hỗ trợ")); *color = ST_YELLOW; }
    else if (!strcmp(st, "empty")) snprintf(tag, n, tr("trống"));
    else if (!strcmp(jstrd(u, "kind", ""), "bios")) { snprintf(tag, n, "BIOS"); *color = ST_YELLOW; }
    else if (!strcmp(st, "needs_choice")) { snprintf(tag, n, tr("chọn hệ")); *color = ST_YELLOW; }
    else { snprintf(tag, n, "%s", jstrd(u, "system", "?")); *color = ST_GREEN; }
}

static void unit_explain(ui_item *it, const cJSON *u) {
    cJSON *x;
    ui_exp(it, tr("Nguồn: %s"), jstrd(u, "source", "?"));
    ui_exp(it, tr("Dung lượng: %s"), mbs(jnumd(u, "bytes", 0)));
    if (!strcmp(jstrd(u, "status", ""), "ready") && jstr(u, "system"))
        ui_exp(it, tr("Hệ: %s (thư mục Roms/%s/)"), jstr(u, "system"), jstrd(u, "folder", jstr(u, "system")));
    if (!strcmp(jstrd(u, "status", ""), "needs_choice")) {
        char buf[300] = "";
        cJSON_ArrayForEach(x, jarr(u, "candidates")) if (cJSON_IsString(x)) {
            size_t l = strlen(buf);
            snprintf(buf + l, sizeof buf - l, "%s%s", l ? ", " : "", x->valuestring);
        }
        ui_exp(it, tr("Hệ ứng viên: %s — A để chọn (không tự đoán)."), buf);
    }
    cJSON *bios = jget(u, "bios");
    if (cJSON_IsObject(bios)) ui_exp(it, tr("BIOS khớp md5: sẽ vào %s (xác nhận riêng ở bước kế hoạch)."), jstrd(bios, "dest", "BIOS/"));
    if (jstr(u, "evidence") && jstr(u, "evidence")[0]) ui_exp(it, tr("Nhận diện: %s"), jstr(u, "evidence"));
    if (!strcmp(jstrd(u, "kind", ""), "multi")) ui_exp(it, tr("Game nhiều file: cài cả bộ, giữ layout tương đối (%d file)."), jarr_n(u, "files"));
    cJSON_ArrayForEach(x, jarr(u, "reasons")) if (cJSON_IsString(x)) ui_exp(it, "!! %s", x->valuestring);
    cJSON_ArrayForEach(x, jarr(u, "warnings")) if (cJSON_IsString(x)) ui_exp(it, "! %s", x->valuestring);
}

static void build_import(ui_screen *s) {
    cJSON *d, *units = scan_units();
    int n = g_nu, marked = n_marked();
    ui_hdr(s, tr("%d mục trong Import/ (SD1 + SD2). Chỉ nhập nội dung bạn có quyền sử dụng."), n);
    cJSON_ArrayForEach(d, jarr(g_scan, "import_dirs"))
        ui_hdr(s, "%s: %s %s", !strcmp(jstrd(d, "card", ""), "primary") ? "SD1" : "SD2", jstrd(d, "path", "?"),
               jbool_d(d, "exists", 0) ? "" : tr("(chưa có — chọn Mở thư mục nhập)"));
    cJSON_ArrayForEach(d, jarr(g_scan, "errors")) ui_hdr(s, "! %s: %s", jstrd(d, "path", "?"), jstrd(d, "message", "?"));
    ui_item *it;
    if (marked) {
        double bytes = 0;
        for (int i = 0; i < n; i++) if (g_u[i].mark) bytes += jnumd(unit_at(i), "bytes", 0);
        char lab[80];
        snprintf(lab, sizeof lab, tr("Nhập %d mục đã chọn"), marked);
        it = ui_add(s, lab, mbs(bytes), ST_GREEN);
        ui_exp(it, tr("A — chọn thẻ đích, kiểm trùng và xem kế hoạch (chưa ghi gì)."));
        ui_exp(it, tr("X — bỏ chọn tất cả."));
        it->iarg = -1;
        ui_act(it, 0, tr("XEM KẾ HOẠCH"), plan_marked);
        ui_act(it, 1, tr("BỎ CHỌN HẾT"), clear_marks);
    }
    {
        int nfree = 0;
        double fbytes = 0;
        for (int i = 0; i < n; i++)
            if (!g_u[i].mark && is_markable(cJSON_GetArrayItem(units, i))) { nfree++; fbytes += jnumd(unit_at(i), "bytes", 0); }
        if (nfree) {
            char lab[64];
            snprintf(lab, sizeof lab, tr("Chọn tất cả %d mục"), nfree);
            it = ui_add(s, lab, mbs(fbytes), ST_UNKNOWN);
            ui_exp(it, tr("Đánh dấu mọi mục nhập được (bỏ qua mục bị chặn; BIOS luôn chọn riêng)."));
            ui_exp(it, tr("Chưa ghi gì: còn bước xem kế hoạch (thẻ đích, trùng tên) và xác nhận."));
            it->iarg = -1;
            ui_act(it, 0, tr("CHỌN TẤT CẢ"), select_all);
        }
    }
    for (int i = 0; i < n; i++) {
        cJSON *u = cJSON_GetArrayItem(units, i);
        char lab[220], tag[24];
        int col;
        snprintf(lab, sizeof lab, "%s%s", g_u[i].mark ? "[x] " : "[ ] ", jstrd(u, "name", "?"));
        unit_tag(u, i, tag, sizeof tag, &col);
        it = ui_add(s, lab, tag, col);
        unit_explain(it, u);
        it->iarg = i;
        ui_arg(it, "u");
        const char *st = jstrd(u, "status", ""), *kind = jstrd(u, "kind", "");
        if (!strcmp(st, "needs_choice")) ui_act(it, 0, tr("CHỌN HỆ"), open_choose);
        else if (!strcmp(st, "needs_unpack")) {
            ui_act(it, 0, tr("GIẢI NÉN"), do_unpack);
            ui_exp(it, tr("A — kiểm nội dung rồi giải vào Import/<tên>/; archive gốc vào Import/.done/."));
            ui_exp(it, tr("Có mật khẩu: tự thử mật khẩu gợi ý; không ra thì cho chọn hoặc gõ."));
        }
        else if (is_markable(u) || !strcmp(kind, "bios"))
            ui_act(it, 0, marked && is_markable(u) ? tr("NHẬP ĐÃ CHỌN") : tr("NHẬP MỤC NÀY"), plan_one);
        if (marked && is_markable(u)) ui_exp(it, tr("A — nhập tất cả mục đã chọn (kể cả mục này)."));
        if (is_markable(u) || !strcmp(kind, "bios")) ui_act(it, 1, g_u[i].mark ? tr("BỎ CHỌN") : tr("CHỌN"), do_toggle);
    }
    if (!n) {
        it = ui_add(s, tr("Chưa có gì trong Import/"), "", ST_UNKNOWN);
        ui_exp(it, tr("Thư mục Import/ trống (hoặc chưa có). Chọn Mở thư mục nhập để tạo, rồi chép file vào."));
        it->iarg = -1;
    }
    char tg[24];
    snprintf(tg, sizeof tg, tr("%d mục"), n);
    it = ui_add(s, tr("Quét lại Import/"), tg, ST_UNKNOWN);
    ui_exp(it, tr("Đọc lại Import/ của cả hai thẻ (chỉ đọc)."));
    it->iarg = -1;
    ui_act(it, 0, tr("QUÉT"), do_rescan);
    it = ui_add(s, tr("Mở thư mục nhập"), tr("tạo"), ST_UNKNOWN);
    ui_exp(it, tr("Tạo <thẻ>/Import/ ở gốc mỗi thẻ nếu chưa có và cho biết đường dẫn."));
    ui_exp(it, tr("Chép file ROM, .zip hoặc thư mục game vào đó (qua máy tính, USB hoặc pc/import_push.py)."));
    ui_exp(it, tr("LoongMate không tải ROM từ internet: chỉ nhập file bạn tự đưa vào."));
    it->iarg = -1;
    ui_act(it, 0, tr("TẠO"), do_mkdir);
    it = ui_add(s, tr("Dọn file đã nhập"), g_delete_src ? tr("XOÁ") : ".done/", g_delete_src ? ST_RED : ST_UNKNOWN);
    ui_exp(it, tr("Sau khi cài thành công, file gốc trong Import/ được %s."),
           g_delete_src ? tr("XOÁ (đang BẬT)") : tr("chuyển vào Import/.done/ (mặc định)"));
    ui_exp(it, tr("File chưa cài được (bị chặn, huỷ, lỗi) luôn ở nguyên chỗ."));
    ui_exp(it, tr("A — bật/tắt xoá thay vì chuyển."));
    it->iarg = -1;
    ui_act(it, 0, tr("BẬT/TẮT"), toggle_delete);
}

/* ============================================================ kế hoạch */
static void build_plan(ui_screen *s);
static void ask_apply(ui_screen *s, ui_item *it);
static void plan_screen_free(ui_screen *s);

static void drop_plan_data(void) {   /* chỉ bỏ dữ liệu trong bộ nhớ: KHÔNG xoá file kế hoạch (plan_done vừa nhận file mới) */
    cJSON_Delete(g_plan);
    g_plan = NULL;
    free(g_pact); g_pact = NULL;
    free(g_pbios); g_pbios = NULL;
    g_npu = 0;
}
static void free_plan(void) {
    drop_plan_data();
    plan_remove("import");
}

static void confirm_primary(const char *arg);

static void plan_done(lm_job *j, const char *arg) {
    cJSON *r = app_json_or_error(j, tr("Nhập game"));
    if (!r) return;
    if (!jget(r, "card") || !jarr(r, "units")) {
        ui_info1(tr("Nhập game"), "!! %s", jstrd(r, "message", tr("Engine không trả kế hoạch (engine cũ chưa có `import plan`?).")));
        cJSON_Delete(r);
        return;
    }
    if (jbool_d(r, "needs_card_choice", 0)) {      /* thẻ phụ không dùng được: HỎI, không tự dùng thẻ Leaf */
        lines_t L = {0};
        lines_add(&L, "!! %s", jstrd(jget(r, "card"), "reason", ""));
        lines_add(&L, "%s", "");
        lines_add(&L, tr("Dùng thẻ Leaf (thẻ chính) thì game nằm cùng thẻ với hệ thống; thẻ này thường gần đầy."));
        lines_add(&L, tr("LoongMate KHÔNG tự chuyển sang thẻ chính — bạn quyết định."));
        int usable = 0;
        cJSON *o;
        cJSON_ArrayForEach(o, jarr(jget(r, "card"), "options"))
            if (!strcmp(jstrd(o, "card", ""), "primary") && jbool_d(o, "usable", 0)) usable = 1;
        plan_remove("import");
        cJSON_Delete(r);
        if (usable) ui_confirm(tr("Thẻ phụ không dùng được"), &L, tr("Dùng thẻ Leaf"), confirm_primary, "primary");
        else ui_info(tr("Không có thẻ nào dùng được"), &L);
        return;
    }
    if (!jbool_d(r, "success", 0) || !jbool_d(r, "ok", 0)) {
        ui_info1(tr("Không lập được kế hoạch"), "!! %s", jstrd(jget(r, "card"), "reason", jstrd(r, "message", "?")));
        cJSON_Delete(r);
        return;
    }
    if (ui_card_pick(jget(r, "card"), arg, confirm_primary)) { plan_remove("import"); cJSON_Delete(r); return; }  /* hai thẻ dùng được: hỏi */
    if (!app_plan_written(r, "import", tr("Nhập game"))) { cJSON_Delete(r); return; }
    drop_plan_data();
    g_plan = r;
    g_npu = jarr_n(r, "units");
    g_pact = calloc((size_t)(g_npu ? g_npu : 1), 1);
    g_pbios = calloc((size_t)(g_npu ? g_npu : 1), 1);
    ui_screen *ps = ui_screen_new("KẾ HOẠCH NHẬP", build_plan);
    if (ps) ps->on_free = plan_screen_free;   /* đóng màn kế hoạch (huỷ hoặc xong) = bỏ kế hoạch + xoá file */
    ui_push(ps);
}

static void plan_screen_free(ui_screen *s) { (void)s; drop_plan_data(); plan_remove("import"); }


static int g_last_only = -1;   /* lần lập kế hoạch gần nhất: để lập lại đúng các mục đó khi người dùng chọn thẻ Leaf */

static void make_plan(const char *pref, int only) {
    g_last_only = only;
    for (int i = 0; i < g_nu; i++) {
        cJSON *u = unit_at(i);
        int pick = only >= 0 ? i == only : g_u[i].mark;
        if (pick && !strcmp(jstrd(u, "status", ""), "needs_choice") && !g_u[i].sys[0]) {
            ui_info1(tr("Chưa chọn hệ máy"), tr("!! Chọn hệ máy (A trên mục '%s') trước khi lập kế hoạch."), jstrd(u, "name", "?"));
            return;
        }
    }
    plan_remove("import");
    sv_t a = {0};
    sv_add(&a, "import"); sv_add(&a, "plan"); sv_add(&a, "--card"); sv_add(&a, "%s", pref);
    sv_add(&a, "--plan-out"); sv_add(&a, "%s", plan_file("import"));
    int n = 0;
    for (int i = 0; i < g_nu; i++) {
        if (!(only >= 0 ? i == only : g_u[i].mark)) continue;
        const char *id = jstrd(unit_at(i), "id", "");
        sv_add(&a, "--select"); sv_add(&a, "%s", id);
        if (g_u[i].sys[0]) { sv_add(&a, "--choice"); sv_add(&a, "%s=%s", id, g_u[i].sys); }
        n++;
    }
    if (n) cli_task_v(tr("Đang kiểm tra và lập kế hoạch"), LONG_TIMEOUT_MS, plan_done, pref, (const char *const *)a.v, a.n);
    sv_free(&a);
}

static void confirm_primary(const char *arg) { make_plan(arg, g_last_only); }     /* "primary" / "secondary" */

static const char *action_row(const cJSON *u, int i, char *tag, size_t n, int *color, int *has_act) {
    const char *a = jstrd(u, "action", "");
    *has_act = 0;
    *color = ST_UNKNOWN;
    if (!strcmp(a, "install")) { snprintf(tag, n, tr("cài")); *color = ST_GREEN; return "install"; }
    if (!strcmp(a, "present")) { snprintf(tag, n, tr("đã có")); return "present"; }
    if (!strcmp(a, "conflict")) { snprintf(tag, n, tr("trùng tên: %s"), ACT_KEY[g_pact[i]]); *color = ST_YELLOW; *has_act = 1; return "conflict"; }
    if (!strcmp(a, "bios_confirm")) { snprintf(tag, n, "BIOS: %s", g_pbios[i] ? tr("nhập") : tr("bỏ qua")); *color = g_pbios[i] ? ST_GREEN : ST_YELLOW; *has_act = 1; return "bios"; }
    if (!strcmp(a, "bios_present")) { snprintf(tag, n, tr("BIOS đã có")); return "bios_present"; }
    if (!strcmp(a, "bios_conflict")) { snprintf(tag, n, tr("BIOS khác")); *color = ST_RED; return "bios_conflict"; }
    if (!strcmp(a, "choose_system")) { snprintf(tag, n, tr("chưa chọn hệ")); *color = ST_YELLOW; return "choose"; }
    snprintf(tag, n, "%s", jstrd(u, "status", "?"));
    *color = ST_RED;
    return "other";
}

static int will_do(void) {
    int n = 0;
    cJSON *units = jarr(g_plan, "units");
    for (int i = 0; i < g_npu; i++) {
        const char *a = jstrd(cJSON_GetArrayItem(units, i), "action", "");
        if (!strcmp(a, "install") || (!strcmp(a, "conflict") && (g_pact[i] == 1 || g_pact[i] == 2)) || (!strcmp(a, "bios_confirm") && g_pbios[i])) n++;
    }
    return n;
}

static void cycle_act(ui_screen *s, ui_item *it) {
    int i = it->iarg;
    const char *a = jstrd(cJSON_GetArrayItem(jarr(g_plan, "units"), i), "action", "");
    if (!strcmp(a, "conflict")) g_pact[i] = (unsigned char)((g_pact[i] + 1) & 3);
    else if (!strcmp(a, "bios_confirm")) g_pbios[i] = !g_pbios[i];
    ui_rebuild(s);
}

static void build_plan(ui_screen *s) {
    cJSON *card = jget(g_plan, "card"), *units = jarr(g_plan, "units"), *x;
    ui_hdr(s, tr("Thẻ đích: %s"), jstrd(card, "reason", ""));
    ui_hdr(s, tr("Cần ~%s (gồm giải nén, staging, dự phòng)."), mbs(jnumd(g_plan, "needed_bytes", 0)));
    char lab[64];
    int n = will_do();
    snprintf(lab, sizeof lab, tr("THỰC HIỆN (%d việc)"), n);
    ui_item *it = ui_add(s, lab, "", n ? ST_GREEN : ST_UNKNOWN);
    ui_exp(it, tr("A — xác nhận cuối (mặc định Huỷ) rồi cài: giải nén vào thư mục tạm trên thẻ đích, kiểm SHA-256, rồi chuyển vào Roms/."));
    ui_exp(it, tr("Nguồn trong Import/ chỉ được chuyển/xoá sau khi cài thành công."));
    it->iarg = -1;
    if (n) ui_act(it, 0, tr("THỰC HIỆN"), ask_apply);
    for (int i = 0; i < g_npu; i++) {
        cJSON *u = cJSON_GetArrayItem(units, i);
        char tag[48];
        int col, has;
        const char *kind = action_row(u, i, tag, sizeof tag, &col, &has);
        it = ui_add(s, jstrd(u, "name", "?"), tag, col);
        ui_exp(it, tr("Hệ: %s  ·  %s"), jstrd(u, "system", "?"), mbs(jnumd(u, "bytes", 0)));
        if (!strcmp(kind, "install")) {
            int k = 0;
            cJSON_ArrayForEach(x, jarr(u, "dest_paths")) if (cJSON_IsString(x) && k++ < 4) ui_exp(it, tr("Sẽ cài: %s"), x->valuestring);
        } else if (!strcmp(kind, "present")) ui_exp(it, tr("Cùng SHA-256 đã có trong thư viện — không cài lại."));
        else if (!strcmp(kind, "conflict")) {
            ui_exp(it, tr("!! Đã có file cùng tên nhưng KHÁC nội dung:"));
            int k = 0;
            cJSON_ArrayForEach(x, jarr(jget(u, "conflict"), "paths")) if (cJSON_IsString(x) && k++ < 4) ui_exp(it, "• %s", x->valuestring);
            ui_exp(it, tr("Hành động: %s."), tr(ACT_VI[g_pact[i]]));
            ui_exp(it, tr("A — đổi hành động (giữ / thay / bản sao / huỷ)."));
        } else if (!strcmp(kind, "bios")) {
            ui_exp(it, tr("BIOS khớp md5 -> %s."), jstrd(jget(u, "bios"), "dest", "BIOS/"));
            ui_exp(it, tr("A — xác nhận / bỏ qua BIOS này (xác nhận riêng, không bao giờ ghi đè BIOS có sẵn)."));
        } else if (!strcmp(kind, "bios_present")) ui_exp(it, tr("BIOS này đã có đúng nội dung."));
        else if (!strcmp(kind, "choose")) ui_exp(it, tr("Quay lại và chọn hệ máy."));
        cJSON_ArrayForEach(x, jarr(u, "reasons")) if (cJSON_IsString(x)) ui_exp(it, "!! %s", x->valuestring);
        cJSON_ArrayForEach(x, jarr(u, "warnings")) if (cJSON_IsString(x)) ui_exp(it, "! %s", x->valuestring);
        it->iarg = i;
        if (has) ui_act(it, 0, !strcmp(kind, "bios") ? tr("XÁC NHẬN/BỎ") : tr("ĐỔI HÀNH ĐỘNG"), cycle_act);
    }
}

/* ============================================================ thực hiện */
static void apply_done(lm_job *j, const char *arg) {
    (void)arg;
    int code = lm_job_exit_code(j);
    cJSON *r = app_json_or_error(j, tr("Nhập game"));
    if (!r) return;
    cJSON *res = jarr(r, "results"), *x;
    lines_t L = {0};
    if (code == 3 || !res) {
        lines_add(&L, "!! %s", code == 3 ? tr("Kế hoạch đã cũ hoặc không hợp lệ — CHƯA làm gì cả. Hãy xem trước lại.") : tr("Chưa thực hiện được:"));
        lines_add(&L, "%s", jstrd(r, "message", tr("engine không trả kết quả thực hiện")));
        cJSON_Delete(r);
        ui_info(tr("Nhập game"), &L);
        return;
    }
    lines_add(&L, "%s%s", jnumd(r, "installed", 0) > 0 ? "+ " : "", jstrd(r, "message", ""));
    cJSON_ArrayForEach(x, res) {
        const char *st = jstrd(x, "status", "?");
        const char *mark = (!strcmp(st, "installed") || !strcmp(st, "already_present")) ? "+" : (!strcmp(st, "blocked") || !strcmp(st, "failed")) ? "!!" : "!";
        lines_add(&L, "%s %s: %s — %s", mark, jstrd(x, "name", "?"), st, jstrd(x, "message", ""));
    }
    cJSON_ArrayForEach(x, jarr(r, "source_cleanup")) lines_add(&L, tr("Nguồn %s: %s"), jstrd(x, "action", "?"), jstrd(x, "path", "?"));
    app_add_rescan_lines(&L, jget(r, "rescan"));
    cJSON_Delete(r);
    snprintf(g_pending_title, sizeof g_pending_title, tr("Kết quả nhập game"));
    lines_free(&g_pending);
    g_pending = L;
    free_plan();
    ui_pop();                         /* đóng màn kế hoạch, về danh sách */
    for (int i = 0; i < g_nu; i++) g_u[i].mark = 0;
    cli_task(tr("Đang quét lại Import/"), SHORT_TIMEOUT_MS, scan_done, "0", "import", "scan", NULL);
    app_refresh();
}

static void do_apply(const char *arg) {
    (void)arg;
    sv_t a = {0};
    sv_add(&a, "import"); sv_add(&a, "apply"); sv_add(&a, "--plan"); sv_add(&a, "%s", plan_file("import")); sv_add(&a, "--yes");
    cJSON *units = jarr(g_plan, "units");
    for (int i = 0; i < g_npu; i++) {
        cJSON *u = cJSON_GetArrayItem(units, i);
        const char *act = jstrd(u, "action", ""), *id = jstrd(u, "id", "");
        if (!strcmp(act, "conflict") && g_pact[i]) { sv_add(&a, "--conflict"); sv_add(&a, "%s=%s", id, ACT_KEY[g_pact[i]]); }
        if (!strcmp(act, "bios_confirm") && g_pbios[i]) { sv_add(&a, "--bios-ok"); sv_add(&a, "%s", id); }
    }
    if (g_delete_src) sv_add(&a, "--delete-source");
    cli_task_v(tr("Đang nhập game"), LONG_TIMEOUT_MS, apply_done, "", (const char *const *)a.v, a.n);
    sv_free(&a);
}

static void ask_apply(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    lines_t L = {0};
    cJSON *units = jarr(g_plan, "units");
    lines_add(&L, tr("Thẻ đích: %s"), jstrd(jget(g_plan, "card"), "root", "?"));
    lines_add(&L, "%s", "");
    for (int i = 0; i < g_npu; i++) {
        cJSON *u = cJSON_GetArrayItem(units, i);
        const char *a = jstrd(u, "action", "");
        if (!strcmp(a, "install")) lines_add(&L, tr("+ Cài %s -> %s (%s)"), jstrd(u, "name", "?"), jstrd(u, "system", "?"), mbs(jnumd(u, "bytes", 0)));
        else if (!strcmp(a, "conflict") && (g_pact[i] == 1 || g_pact[i] == 2)) lines_add(&L, "! %s: %s", jstrd(u, "name", "?"), tr(ACT_VI[g_pact[i]]));
        else if (!strcmp(a, "bios_confirm") && g_pbios[i]) lines_add(&L, "+ BIOS %s -> BIOS/", jstrd(jget(u, "bios"), "name", "?"));
    }
    lines_add(&L, "%s", "");
    lines_add(&L, tr("File gốc: %s"), g_delete_src ? tr("sẽ bị XOÁ sau khi cài thành công.") : tr("chuyển vào Import/.done/ sau khi cài thành công."));
    ui_confirm(tr("Xác nhận nhập game"), &L, tr("Nhập"), do_apply, "");
}
