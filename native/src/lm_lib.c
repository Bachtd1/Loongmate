/* LoongMate native — Library > Thư viện riêng (agent PocketBridge của BẠN). Mọi việc đi qua `cli.py library ... --json`; native không nói
 * chuyện mạng, không đọc token, không ghi file người dùng. Luồng:
 *   Thư viện riêng (library overview: node + job tải dở + bundle chờ ghép đôi)
 *     -> node (library roots) -> thư mục (library list: tóm tắt hệ máy) -> hệ máy (library list --system S --offline: danh sách game)
 *     -> X chọn nhiều, Y sắp xếp, mục Lọc -> "Tải & cài" (library fetch --dry-run: hỏi server size+hash, chọn thẻ như importer, HỎI khi phải dùng
 *        thẻ Leaf) -> hộp xác nhận mặc định Huỷ -> library fetch --yes --progress (tiến độ theo byte do CLI báo trên stderr; B = tạm dừng)
 *     -> kết quả của importer, đứng yên tại danh sách (đã tải lại, mục đã cài hiện "đã có").
 *   "Ghép đôi thư viện": bundle trong <thẻ>/PocketBridge-import/ hoặc Import/ -> library pair --yes (đổi mã một lần, lưu token 0600, xoá bundle).
 * Chỉ tải từ máy BẠN đã ghép đôi: không có danh mục ROM nào, không tải từ internet.
 * (Cuối file: Library > Homebrew, tải TRỰC TIẾP qua Internet, chỉ game có giấy phép rõ ràng — xem docs/HOMEBREW.md.) */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static cJSON *g_ov;              /* `library overview` (sở hữu) */
static cJSON *g_sys;             /* `library list NODE ROOT` (tóm tắt hệ máy, sở hữu) */
static cJSON *g_games;           /* `library list NODE ROOT --system S --offline` (entries, sở hữu) */
static unsigned char *g_mark;    /* dấu chọn theo chỉ số entries của g_games */
static int g_ng;
static char g_node[48], g_root[40], g_system[40];
static int g_sort, g_flt;        /* 0 tên / 1 dung lượng; 0 tất cả / 1 chưa có / 2 đã có */
static lines_t g_pending;        /* kết quả đã dựng, chờ hiện sau khi danh sách được tải lại */
static char g_pending_title[64];
static char **g_fpaths;          /* các đường dẫn đang chờ xác nhận tải (strdup) */
static int g_nfp;

static const char *mbs(double bytes) { static char b[4][32]; static int i; i = (i + 1) & 3; ui_fmt_bytes(b[i], sizeof b[i], bytes); return b[i]; }
static const char *const FLT_LABEL[3] = { "tất cả", "chưa có trên máy", "đã có trên máy" };

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

static void fpaths_free(void) { for (int i = 0; i < g_nfp; i++) free(g_fpaths[i]); free(g_fpaths); g_fpaths = NULL; g_nfp = 0; }

static void show_pending(void) {
    if (g_pending.n) { lines_t L = g_pending; g_pending = (lines_t){0}; ui_info(g_pending_title, &L); }
}
static void set_pending(const char *title, lines_t L) {
    snprintf(g_pending_title, sizeof g_pending_title, "%s", title);
    lines_free(&g_pending);
    g_pending = L;
}

/* JSON của CLI thư viện: lỗi nghiệp vụ (success=false) vẫn là JSON hợp lệ kèm message. Trả r nếu success, ngược lại hiện hộp lỗi (kèm gợi ý) và trả NULL. */
static cJSON *lib_ok_json(lm_job *j, const char *title) {
    cJSON *r = app_json_or_error(j, title);
    if (!r) return NULL;
    if (jbool_d(r, "success", 0)) return r;
    lines_t L = {0};
    const char *k = jstrd(r, "error_kind", "");
    lines_add(&L, "!! %s", jstrd(r, "message", tr("Engine báo lỗi không kèm lý do.")));
    if (!strcmp(k, "pin")) lines_add(&L, tr("Kiểm tra lại máy đích, hoặc ghép đôi lại bằng bundle mới."));
    else if (!strcmp(k, "unreachable") || !strcmp(k, "timeout") || !strcmp(k, "network")) lines_add(&L, tr("Kiểm tra Tailscale đang chạy trên máy này và máy đích đang bật."));
    else if (!strcmp(k, "auth")) lines_add(&L, tr("Ghép đôi lại bằng bundle mới (pocketbridge-agent pair --scopes library:read)."));
    cJSON_Delete(r);
    ui_info(title, &L);
    return NULL;
}

/* ============================================================ Thư viện riêng: node, job dở, ghép đôi */
static void build_nodes(ui_screen *s);
static void ov_done(lm_job *j, const char *arg) {
    int push = atoi(arg);
    cJSON *r = lib_ok_json(j, tr("Thư viện riêng"));
    if (!r) return;
    cJSON_Delete(g_ov);
    g_ov = r;
    if (push == 1) ui_push(ui_screen_new("THƯ VIỆN RIÊNG", build_nodes)); else ui_rebuild_all();
    show_pending();
    if (push == 2) app_refresh();      /* sau khi tải & cài: đọc lại readiness (dung lượng thẻ, ...) */
}
/* mode 0: chỉ đọc lại; 2: đọc lại rồi làm mới readiness */
static void lib_reload(int mode) { cli_task(tr("Đang đọc thư viện riêng"), SHORT_TIMEOUT_MS, ov_done, mode == 2 ? "2" : "0", "library", "overview", NULL); }

void open_lib(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc thư viện riêng"), SHORT_TIMEOUT_MS, ov_done, "1", "library", "overview", NULL);
}

static void simple_done(lm_job *j, const char *arg) {
    cJSON *r = app_json_or_error(j, arg);
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "%s%s", jbool_d(r, "success", 0) ? "+ " : "!! ", jstrd(r, "message", "?"));
    cJSON_Delete(r);
    set_pending(arg, L);
    lib_reload(0);
}

static void do_unpair(const char *arg) { cli_task(tr("Đang gỡ ghép đôi"), SHORT_TIMEOUT_MS, simple_done, tr("Gỡ ghép đôi"), "library", "unpair", arg, "--yes", NULL); }
static void ask_unpair(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Gỡ '%s' khỏi máy này? Token và cache danh sách bị xoá."), it->label);
    lines_add(&L, tr("Token trên agent vẫn còn: thu hồi bằng `token revoke` trên máy agent."));
    ui_confirm(tr("Gỡ ghép đôi"), &L, tr("Gỡ"), do_unpair, it->arg);
}

static void do_cancel_job(const char *arg) { cli_task(tr("Đang huỷ job"), SHORT_TIMEOUT_MS, simple_done, tr("Huỷ job tải"), "library", "cancel", arg, "--yes", NULL); }
static void ask_cancel_job(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Xoá job %s và mọi file tải dở?"), it->arg);
    ui_confirm(tr("Huỷ job tải"), &L, tr("Huỷ job"), do_cancel_job, it->arg);
}

static void fetch_done(lm_job *j, const char *arg);
static void do_resume(const char *arg) {
    cli_task(tr("Đang tải & cài"), LONG_TIMEOUT_MS, fetch_done, arg, "library", "resume", arg, "--yes", "--progress", NULL);
    ui_task_cancelable(1);
}
static void ask_resume(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Tiếp tục job %s: %s."), it->arg, it->arg2);
    lines_add(&L, tr("Tải tiếp phần còn lại (Range + If-Range), kiểm SHA-256 rồi cài."));
    lines_add(&L, tr("B trong lúc tải = tạm dừng (tải tiếp được sau)."));
    ui_confirm(tr("Tiếp tục tải"), &L, tr("Tiếp tục"), do_resume, it->arg);
}

static void open_roots(ui_screen *s, ui_item *it);
static void open_pair(ui_screen *s, ui_item *it);

static void build_nodes(ui_screen *s) {
    cJSON *n, *j;
    ui_hdr(s, tr("Chỉ nói chuyện với máy BẠN đã ghép đôi (agent PocketBridge của bạn). LoongMate không có danh mục ROM nào."));
    ui_hdr(s, tr("%d thư viện đã ghép đôi."), jarr_n(g_ov, "nodes"));
    cJSON_ArrayForEach(n, jarr(g_ov, "nodes")) {
        char tag[120];
        int has = jbool_d(n, "has_token", 0);
        snprintf(tag, sizeof tag, "%s:%.0f", jstrd(n, "host", "?"), jnumd(n, "port", 0));
        ui_item *it = ui_add(s, jstrd(n, "name", "?"), tag, has ? ST_GREEN : ST_RED);
        ui_exp(it, tr("Mã: %s"), jstrd(n, "id", "?"));
        ui_exp(it, tr("Máy đích: %s"), tag);
        ui_exp(it, "Fingerprint ghim: %.23s…", jstrd(n, "fingerprint", ""));
        ui_exp(it, "%s", has ? tr("Token: đã lưu (/userdata, quyền 0600)") : tr("!! Token: THIẾU — ghép đôi lại"));
        ui_exp(it, tr("A — mở thư viện.  X — gỡ ghép đôi (xoá token + cache trên máy này)."));
        ui_arg(it, jstrd(n, "id", ""));
        snprintf(it->arg2, sizeof it->arg2, "%s", jstrd(n, "name", ""));
        if (has) ui_act(it, 0, tr("MỞ"), open_roots);
        ui_act(it, 1, tr("GỠ GHÉP ĐÔI"), ask_unpair);
    }
    cJSON_ArrayForEach(j, jarr(g_ov, "jobs")) {
        char lab[200], tag[24], st[96];
        cJSON *nm = cJSON_GetArrayItem(jarr(j, "names"), 0);
        snprintf(lab, sizeof lab, tr("Tải dở: %s%s"), cJSON_IsString(nm) ? nm->valuestring : "?", jarr_n(j, "names") > 1 ? "…" : "");
        snprintf(tag, sizeof tag, "%.0f%%", jnumd(j, "percent", 0));
        ui_item *it = ui_add(s, lab, tag, ST_YELLOW);
        const char *stt = jstrd(j, "state", "?");
        stt = !strcmp(stt, "downloading") ? tr("đang tải dở") : !strcmp(stt, "interrupted") ? tr("tải dở") : !strcmp(stt, "installing") ? tr("chờ cài") : !strcmp(stt, "unreadable") ? tr("hỏng") : stt;
        ui_exp(it, tr("Job %s (%s) trên thẻ %s"), jstrd(j, "job_id", "?"), stt, !strcmp(jstrd(j, "card", ""), "primary") ? "SD1" : "SD2");
        snprintf(st, sizeof st, "%.0f/%.0f file xong, %s", jnumd(j, "files_done", 0), jnumd(j, "files", 0), mbs(jnumd(j, "bytes_done", 0)));
        ui_exp(it, "%s / %s.", st, mbs(jnumd(j, "bytes_total", 0)));
        ui_exp(it, "%s", jstr(j, "message") && jstr(j, "message")[0] ? jstr(j, "message") : tr("Mất mạng/ngủ máy giữa chừng: tiếp tục được, file đã tải được giữ."));
        ui_exp(it, tr("A — tiếp tục tải rồi cài.  X — huỷ job và xoá file tải dở."));
        ui_arg(it, jstrd(j, "job_id", ""));
        snprintf(it->arg2, sizeof it->arg2, "%.0f/%.0f file (%.0f%%)", jnumd(j, "files_done", 0), jnumd(j, "files", 0), jnumd(j, "percent", 0));
        if (strcmp(jstrd(j, "state", ""), "unreadable")) ui_act(it, 0, tr("TIẾP TỤC"), ask_resume);
        ui_act(it, 1, tr("HUỶ JOB"), ask_cancel_job);
    }
    char tag[32];
    snprintf(tag, sizeof tag, "%d bundle", jarr_n(g_ov, "bundles"));
    ui_item *it = ui_add(s, tr("Ghép đôi thư viện"), tag, ST_UNKNOWN);
    ui_exp(it, tr("Nhập bundle ghép đôi (.json) do `pocketbridge-agent pair --scopes library:read` tạo ra."));
    ui_exp(it, tr("Chép bundle vào <thẻ Leaf>/PocketBridge-import/ (hoặc Import/) trước."));
    ui_exp(it, tr("Ghim fingerprint của bundle, đổi mã một lần lấy token dài hạn, lưu token (0600) rồi XOÁ bundle."));
    ui_act(it, 0, tr("MỞ"), open_pair);
}

/* ---- ghép đôi */
static void build_pair(ui_screen *s);
static void open_pair(ui_screen *s, ui_item *it) { (void)s; (void)it; ui_push(ui_screen_new("GHÉP ĐÔI THƯ VIỆN", build_pair)); }

static void do_pair(const char *arg) { cli_task(tr("Đang ghép đôi"), SHORT_TIMEOUT_MS, simple_done, tr("Ghép đôi thư viện"), "library", "pair", arg, "--yes", NULL); }
static void ask_pair(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Ghép đôi với '%s' (%s)?"), it->arg2, it->label);
    lines_add(&L, "%s", "");
    lines_add(&L, tr("Mã trong bundle chỉ dùng được MỘT lần: đổi lấy token dài hạn, lưu ở /userdata (quyền 0600)."));
    lines_add(&L, tr("Fingerprint trong bundle được ghim: máy lạ không nhận được token."));
    lines_add(&L, tr("Bundle bị XOÁ sau khi ghép đôi thành công."));
    ui_confirm(tr("Ghép đôi thư viện"), &L, tr("Ghép đôi"), do_pair, it->arg);
}

static void build_pair(ui_screen *s) {
    cJSON *b;
    ui_hdr(s, tr("%d bundle trong PocketBridge-import/ và Import/ của cả hai thẻ."), jarr_n(g_ov, "bundles"));
    cJSON_ArrayForEach(b, jarr(g_ov, "bundles")) {
        const char *card = !strcmp(jstrd(b, "card", ""), "primary") ? "SD1" : "SD2";
        if (jbool_d(b, "valid", 0)) {
            ui_item *it = ui_add(s, jstrd(b, "file", "?"), card, ST_GREEN);
            ui_exp(it, tr("Thư viện: %s (%s)"), jstrd(b, "node_name", "?"), jstrd(b, "node_id", "?"));
            ui_exp(it, tr("Máy đích: %s"), jstrd(b, "host", "?"));
            ui_exp(it, tr("Kiểu mã: %s"), jstrd(b, "type", "?"));
            ui_exp(it, "File: %s", jstrd(b, "path", "?"));
            ui_exp(it, tr("A — ghép đôi (xác nhận trước). Bundle bị xoá sau khi ghép đôi thành công."));
            ui_arg(it, jstrd(b, "path", ""));
            snprintf(it->arg2, sizeof it->arg2, "%s", jstrd(b, "node_name", ""));
            ui_act(it, 0, tr("GHÉP ĐÔI"), ask_pair);
        } else {
            ui_item *it = ui_add(s, jstrd(b, "file", "?"), tr("không hợp lệ"), ST_RED);
            ui_exp(it, "File: %s", jstrd(b, "path", "?"));
            ui_exp(it, "!! %s", jstrd(b, "reason", "?"));
            ui_exp(it, tr("File không bị đụng tới."));
        }
    }
    if (!jarr_n(g_ov, "bundles")) {
        ui_item *it = ui_add(s, tr("Không có bundle nào"), "", ST_UNKNOWN);
        ui_exp(it, tr("Chép file bundle (.json) vào <thẻ Leaf>/PocketBridge-import/ hoặc <thẻ>/Import/, rồi mở lại màn này."));
        ui_exp(it, tr("Tạo bundle trên máy agent: pocketbridge-agent pair --id pocket1 --scopes library:read"));
    }
}

/* ============================================================ thư mục (roots) */
static void open_systems(ui_screen *s, ui_item *it);
static void build_roots(ui_screen *s) {
    cJSON *r;
    ui_hdr(s, tr("%d thư mục thư viện."), jarr_n(s->d, "roots"));
    cJSON_ArrayForEach(r, jarr(s->d, "roots")) {
        char tag[40];
        snprintf(tag, sizeof tag, "%.0f file", jnumd(r, "files", 0));
        ui_item *it = ui_add(s, jstrd(r, "name", "?"), tag, jbool_d(r, "indexing", 0) ? ST_YELLOW : ST_UNKNOWN);
        ui_exp(it, tr("Mã thư mục: %s"), jstrd(r, "id", "?"));
        ui_exp(it, "%.0f file, %s", jnumd(r, "files", 0), mbs(jnumd(r, "bytes", 0)));
        if (jbool_d(r, "indexing", 0)) ui_exp(it, tr("Server đang quét lại danh sách."));
        else ui_exp(it, tr("Quét lúc: %s"), jstrd(r, "indexed_at", "?"));
        ui_exp(it, tr("A — duyệt theo hệ máy."));
        ui_arg(it, jstrd(r, "id", ""));
        ui_act(it, 0, tr("DUYỆT"), open_systems);
    }
    if (!jarr_n(s->d, "roots")) {
        ui_item *it = ui_add(s, tr("Agent chưa khai báo thư viện"), "", ST_UNKNOWN);
        ui_exp(it, tr("Khai báo library.roots trong config của agent (xem docs/THU-VIEN-RIENG.md)."));
    }
}
static void build_systems(ui_screen *s);
static void sys_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = lib_ok_json(j, tr("Thư viện riêng"));
    if (!r) return;
    cJSON_Delete(g_sys);
    g_sys = r;
    ui_push(ui_screen_new("THƯ VIỆN — hệ máy", build_systems));
}
static void open_systems(ui_screen *s, ui_item *it) {
    (void)s;
    snprintf(g_root, sizeof g_root, "%s", it->arg);
    cli_task(tr("Đang đọc danh sách thư viện"), LONG_TIMEOUT_MS, sys_done, "", "library", "list", g_node, g_root, NULL);
}
static void roots_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = lib_ok_json(j, tr("Thư viện riêng"));
    if (!r) return;
    ui_screen *s = ui_screen_new("THƯ VIỆN — thư mục", build_roots);
    if (!s) { cJSON_Delete(r); return; }
    s->d = r;
    ui_push(s);
}
static void open_roots(ui_screen *s, ui_item *it) {
    (void)s;
    snprintf(g_node, sizeof g_node, "%s", it->arg);
    cli_task(tr("Đang hỏi thư viện"), SHORT_TIMEOUT_MS, roots_done, "", "library", "roots", g_node, NULL);
}

/* ============================================================ hệ máy */
static void open_games(ui_screen *s, ui_item *it);
static void build_systems(ui_screen *s) {
    cJSON *x;
    ui_hdr(s, tr("%.0f game trong %d hệ máy (chỉ hiện file có đuôi hệ máy trong systems.json hoặc có hint)."), jnumd(g_sys, "total", 0), jarr_n(g_sys, "systems"));
    if (jbool_d(g_sys, "stale", 0))
        ui_hdr(s, tr("! DANH SÁCH CŨ (lấy lúc %s): không nối được server — %s"), jstrd(g_sys, "fetched_at", "?"), jstrd(g_sys, "error", tr("chỉ đọc cache")));
    cJSON_ArrayForEach(x, jarr(g_sys, "systems")) {
        const char *sy = jstrd(x, "system", "?");
        char tag[32];
        snprintf(tag, sizeof tag, "%.0f file", jnumd(x, "count", 0));
        ui_item *it = ui_add(s, !strcmp(sy, "?") ? tr("Chưa rõ hệ") : sy, tag, ST_UNKNOWN);
        ui_exp(it, tr("Hệ: %s"), sy);
        ui_exp(it, "%.0f file, %s", jnumd(x, "count", 0), mbs(jnumd(x, "bytes", 0)));
        if (!strcmp(sy, "?")) ui_exp(it, tr("Chưa rõ hệ: đuôi mơ hồ (.bin/.zip...) và thư mục cấp 1 không phải tên hệ — importer nhận diện bằng header, không được thì báo, không đoán."));
        else ui_exp(it, tr("A — duyệt danh sách game."));
        ui_arg(it, sy);
        ui_act(it, 0, tr("DUYỆT"), open_games);
    }
    if (!jarr_n(g_sys, "systems")) {
        ui_item *it = ui_add(s, tr("Không có game nào"), "", ST_UNKNOWN);
        ui_exp(it, tr("Thư viện không có file nào có đuôi thuộc hệ máy nào."));
    }
}

/* ============================================================ game */
static void build_games(ui_screen *s);
static cJSON *entries(void) { return jarr(g_games, "entries"); }
static cJSON *entry(int i) { return cJSON_GetArrayItem(entries(), i); }
static int n_marked(double *bytes) {
    int n = 0;
    double b = 0;
    for (int i = 0; i < g_ng; i++) if (g_mark[i]) { n++; b += jnumd(entry(i), "size", 0); }
    if (bytes) *bytes = b;
    return n;
}

/* nhận danh sách game mới; giữ dấu chọn theo đường dẫn nếu keep */
static void adopt_games(cJSON *r, int keep) {
    cJSON *old = g_games;
    unsigned char *om = g_mark;
    int on = g_ng;
    g_games = r;
    g_ng = jarr_n(r, "entries");
    g_mark = calloc((size_t)(g_ng ? g_ng : 1), 1);
    if (keep && g_mark && om)
        for (int i = 0; i < g_ng; i++) {
            const char *p = jstrd(entry(i), "path", "");
            for (int k = 0; k < on; k++)
                if (om[k] && !strcmp(jstrd(cJSON_GetArrayItem(jarr(old, "entries"), k), "path", "\1"), p)) { g_mark[i] = 1; break; }
        }
    free(om);
    cJSON_Delete(old);
}

static void games_done(lm_job *j, const char *arg) {
    int mode = atoi(arg);                     /* 1 = mở màn mới; 0 = tải lại tại chỗ; 2 = tải lại rồi đọc lại overview */
    cJSON *r = lib_ok_json(j, tr("Thư viện riêng"));
    if (!r) return;
    adopt_games(r, mode != 1);
    if (mode == 1) { g_sort = 0; g_flt = 0; ui_push(ui_screen_new("THƯ VIỆN — game", build_games)); }
    else ui_rebuild_all();
    if (mode == 2) lib_reload(2); else show_pending();
}
static void open_games(ui_screen *s, ui_item *it) {
    (void)s;
    snprintf(g_system, sizeof g_system, "%s", it->arg);
    cli_task(tr("Đang đọc danh sách game"), LONG_TIMEOUT_MS, games_done, "1", "library", "list", g_node, g_root, "--system", g_system, "--offline", NULL);
}
static void reload_games(int mode) {
    char a[4];
    snprintf(a, sizeof a, "%d", mode);
    cli_task(tr("Đang đọc danh sách game"), LONG_TIMEOUT_MS, games_done, a, "library", "list", g_node, g_root, "--system", g_system, "--offline", NULL);
}

static int cmp_entry(const void *pa, const void *pb) {
    const cJSON *a = entry(*(const int *)pa), *b = entry(*(const int *)pb);
    if (g_sort == 1) {
        double d = jnumd(b, "size", 0) - jnumd(a, "size", 0);
        if (d) return d < 0 ? -1 : 1;
    }
    int c = strcasecmp(jstrd(a, "path", ""), jstrd(b, "path", ""));
    return c ? c : strcmp(jstrd(a, "path", ""), jstrd(b, "path", ""));
}

static void make_plan(const char *pref, int only);
static void plan_marked(ui_screen *s, ui_item *it) { (void)s; (void)it; make_plan("auto", -1); }
static void plan_one(ui_screen *s, ui_item *it) { (void)s; make_plan("auto", it->iarg); }

static void select_path(ui_screen *s, const char *path) {
    for (int i = 0; i < s->n; i++)
        if (!s->items[i]->head && s->items[i]->iarg >= 0 && !strcmp(s->items[i]->arg, path)) { s->sel = i; s->exp_scroll = 0; return; }
}
static void select_first_game(ui_screen *s) {
    for (int i = 0; i < s->n; i++) if (!s->items[i]->head && s->items[i]->iarg >= 0) { s->sel = i; s->exp_scroll = 0; return; }
}
/* dựng lại rồi đặt con trỏ về đúng mục trước đó: game (theo đường dẫn) hoặc mục điều khiển (iarg âm) */
static void rebuild_keep(ui_screen *s) {
    char path[UI_ARGLEN] = "";
    int ctl = 0;
    if (s->n && !s->items[s->sel]->head) {
        ui_item *cur = s->items[s->sel];
        if (cur->iarg >= 0) snprintf(path, sizeof path, "%s", cur->arg); else ctl = cur->iarg;
    }
    ui_rebuild(s);
    if (path[0]) select_path(s, path);
    else if (ctl)
        for (int i = 0; i < s->n; i++) if (!s->items[i]->head && s->items[i]->iarg == ctl) { s->sel = i; s->exp_scroll = 0; break; }
}
static void act_sort(ui_screen *s, ui_item *it) { (void)it; g_sort = !g_sort; rebuild_keep(s); }
static void act_filter(ui_screen *s, ui_item *it) { (void)it; g_flt = (g_flt + 1) % 3; rebuild_keep(s); }
static void act_toggle(ui_screen *s, ui_item *it) {
    if (it->iarg < 0 || it->iarg >= g_ng) return;
    char path[UI_ARGLEN];
    snprintf(path, sizeof path, "%s", it->arg);
    g_mark[it->iarg] ^= 1;
    ui_rebuild(s);                             /* mục "Tải & cài N" hiện/ẩn làm lệch dòng: giữ con trỏ theo đường dẫn */
    select_path(s, path);
}
static void act_clear(ui_screen *s, ui_item *it) {
    (void)it;
    memset(g_mark, 0, (size_t)(g_ng ? g_ng : 1));
    s->sel = 0;
    ui_rebuild(s);
    select_first_game(s);
}

static void build_games(ui_screen *s) {
    int *idx = calloc((size_t)(g_ng ? g_ng : 1), sizeof *idx), nidx = 0;
    if (!idx) return;
    for (int i = 0; i < g_ng; i++) {
        int have = jbool_d(entry(i), "present", 0);
        if (g_flt == 1 && have) continue;
        if (g_flt == 2 && !have) continue;
        idx[nidx++] = i;
    }
    qsort(idx, (size_t)nidx, sizeof *idx, cmp_entry);
    double mbytes = 0;
    int nm = n_marked(&mbytes);
    if (nidx == g_ng) ui_hdr(s, tr("Hệ %s: %d file."), g_system, g_ng);
    else ui_hdr(s, tr("Hệ %s: hiện %d / %d file."), g_system, nidx, g_ng);
    if (jbool_d(g_sys, "stale", 0)) ui_hdr(s, tr("! DANH SÁCH CŨ (%s): %s"), jstrd(g_sys, "fetched_at", "?"), jstrd(g_sys, "error", tr("chỉ đọc cache")));   /* cũ = lần đọc server ở màn hệ máy thất bại */
    ui_hdr(s, tr("Sắp xếp: %s · Lọc: %s."), g_sort ? tr("Dung lượng (lớn trước)") : tr("Tên"), tr(FLT_LABEL[g_flt]));
    if (nm) {
        int vis = 0;
        for (int k = 0; k < nidx; k++) if (g_mark[idx[k]]) vis++;
        if (nm > vis) ui_hdr(s, tr("Đã chọn %d file · %s, %d đang bị bộ lọc ẩn."), nm, mbs(mbytes), nm - vis);
        else ui_hdr(s, tr("Đã chọn %d file · %s."), nm, mbs(mbytes));
    } else ui_hdr(s, tr("Đã chọn 0 file."));
    ui_hdr(s, tr("Y: đổi sắp xếp. A ở mục Lọc: đổi lọc. X: chọn nhiều file. A ở game: tải & cài riêng game đó."));
    const char *ylbl = tr("SẮP XẾP");
    ui_item *it;
    if (nm) {
        char lab[64];
        snprintf(lab, sizeof lab, tr("Tải & cài %d file đã chọn"), nm);
        it = ui_add(s, lab, mbs(mbytes), ST_GREEN);
        it->iarg = -1;
        ui_exp(it, tr("Đã chọn %d file, %s."), nm, mbs(mbytes));
        ui_exp(it, tr("A — hỏi server (kích thước + SHA-256), chọn thẻ đích và xem kế hoạch (chưa tải gì)."));
        ui_exp(it, tr("Gồm cả file đang bị bộ lọc ẩn (dấu chọn không mất khi đổi lọc/sắp xếp). X: bỏ chọn tất cả."));
        ui_act(it, 0, tr("KIỂM & XEM KẾ HOẠCH"), plan_marked);
        ui_act(it, 1, tr("BỎ CHỌN HẾT"), act_clear);
        ui_act(it, 2, ylbl, act_sort);
    }
    {
        char lab[48], tag[24];
        snprintf(lab, sizeof lab, tr("Lọc: %s"), tr(FLT_LABEL[g_flt]));
        snprintf(tag, sizeof tag, "%d file", nidx);
        it = ui_add(s, lab, tag, ST_UNKNOWN);
        it->iarg = -2;
        ui_exp(it, tr("Lọc theo việc game đã có trong Roms/ của máy (so tên file) hay chưa."));
        ui_exp(it, tr("Dấu chọn (X) được giữ nguyên khi đổi lọc hay đổi sắp xếp."));
        ui_exp(it, tr("A: đổi sang \"%s\". Y: đổi cách sắp xếp (Tên / Dung lượng)."), tr(FLT_LABEL[(g_flt + 1) % 3]));
        ui_act(it, 0, tr("ĐỔI LỌC"), act_filter);
        ui_act(it, 2, ylbl, act_sort);
    }
    for (int k = 0; k < nidx; k++) {
        int i = idx[k];
        cJSON *e = entry(i);
        int mk = g_mark[i], have = jbool_d(e, "present", 0), ok = jbool_d(e, "installable", 0);
        char tag[64], label[260];
        snprintf(tag, sizeof tag, "%s%s", have ? tr("đã có ") : "", mbs(jnumd(e, "size", 0)));
        snprintf(label, sizeof label, "%s%s", mk ? "[x] " : "[ ] ", jstrd(e, "name", "?"));
        it = ui_add(s, label, tag, mk ? ST_GREEN : (!ok ? ST_YELLOW : ST_UNKNOWN));
        ui_arg(it, jstrd(e, "path", ""));
        it->iarg = i;
        if (mk) ui_exp(it, tr("+ ĐÃ CHỌN để tải & cài (X để bỏ chọn)."));
        ui_exp(it, "File: %s", jstrd(e, "path", "?"));
        ui_exp(it, tr("Dung lượng: %s"), mbs(jnumd(e, "size", 0)));
        ui_exp(it, "SHA-256 server: %s", jstr(e, "sha256") ? jstr(e, "sha256") : tr("chưa băm xong — chưa tải được"));
        ui_exp(it, tr("Trên máy: %s"), have ? tr("ĐÃ CÓ file cùng tên trong Roms/") : tr("chưa có"));
        if (!ok) ui_exp(it, "!! %s", jstrd(e, "problem", tr("không cài được")));
        ui_exp(it, tr("A — kiểm với server và xem kế hoạch tải & cài game này."));
        ui_exp(it, tr("X — chọn / bỏ chọn để tải nhiều file.  Y — đổi cách sắp xếp."));
        if (ok) {
            ui_act(it, 0, tr("TẢI & CÀI…"), plan_one);
            ui_act(it, 1, mk ? tr("BỎ CHỌN") : tr("CHỌN"), act_toggle);
        }
        ui_act(it, 2, ylbl, act_sort);
    }
    free(idx);
    if (!nidx) ui_head(s, g_ng ? tr("Không có game nào khớp bộ lọc") : tr("Không có game nào"));
    if (s->sel == 0) select_first_game(s);
}

/* ============================================================ kế hoạch -> xác nhận -> tải & cài */
static void confirm_primary(const char *arg);
static void do_fetch(const char *arg);
static int g_last_only = -1;

static void plan_done(lm_job *j, const char *arg) {
    cJSON *r = lib_ok_json(j, tr("Tải & cài"));
    if (!r) { fpaths_free(); return; }
    cJSON *card = jget(r, "card"), *f, *p, *o;
    if (jbool_d(r, "needs_card_choice", 0)) {      /* thẻ phụ không dùng được: HỎI, không tự dùng thẻ Leaf */
        lines_t L = {0};
        lines_add(&L, "!! %s", jstrd(card, "reason", ""));
        lines_add(&L, "%s", "");
        lines_add(&L, tr("Dùng thẻ Leaf (thẻ chính) thì game nằm cùng thẻ với hệ thống; thẻ này thường gần đầy."));
        lines_add(&L, tr("LoongMate KHÔNG tự chuyển sang thẻ chính — bạn quyết định."));
        int usable = 0, nprob = jarr_n(r, "problems");
        cJSON_ArrayForEach(o, jarr(card, "options")) if (!strcmp(jstrd(o, "card", ""), "primary") && jbool_d(o, "usable", 0)) usable = 1;
        cJSON_ArrayForEach(p, jarr(r, "problems")) lines_add(&L, "!! %s: %s", jstrd(p, "path", "?"), jstrd(p, "reason", "?"));
        cJSON_Delete(r);
        if (usable && !nprob) ui_confirm(tr("Thẻ phụ không dùng được"), &L, tr("Dùng thẻ Leaf"), confirm_primary, "primary");
        else { fpaths_free(); ui_info(usable ? tr("Không tải được") : tr("Không có thẻ nào dùng được"), &L); }
        return;
    }
    if (!jbool_d(r, "ok", 0)) {
        lines_t L = {0};
        cJSON_ArrayForEach(p, jarr(r, "problems")) lines_add(&L, "!! %s: %s", jstrd(p, "path", "?"), jstrd(p, "reason", "?"));
        if (!L.n) lines_add(&L, "!! %s", jstrd(r, "message", "?"));
        cJSON_Delete(r);
        fpaths_free();
        ui_info(tr("Không tải được"), &L);
        return;
    }
    if (ui_card_pick(card, arg, confirm_primary)) { cJSON_Delete(r); return; }     /* hai thẻ dùng được: hỏi */
    lines_t L = {0};
    lines_add(&L, tr("Thư viện: %s/%s"), g_node, g_root);
    lines_add(&L, tr("Thẻ đích: %s"), jstrd(card, "root", "?"));
    lines_add(&L, "%s", "");
    cJSON_ArrayForEach(f, jarr(r, "files"))
        lines_add(&L, "+ %s -> %s (%s)%s", jstrd(f, "path", "?"), jstrd(f, "system", "?"), mbs(jnumd(f, "size", 0)),
                  jbool_d(f, "present", 0) ? tr("  [đã có file cùng tên trong Roms/: importer bỏ qua nếu giống hệt]") : "");
    lines_add(&L, "%s", "");
    lines_add(&L, tr("Tổng %s; cần ~%s trống (file tải + staging + dự phòng)."), mbs(jnumd(r, "total_bytes", 0)), mbs(jnumd(r, "needed_bytes", 0)));
    lines_add(&L, tr("SHA-256 file tải về phải khớp hash server công bố, sai thì KHÔNG cài."));
    lines_add(&L, tr("Cài bằng đúng trình Nhập game (kiểm an toàn, nhận hệ bằng header, giao dịch chống mất điện)."));
    lines_add(&L, tr("Trong lúc tải: B = tạm dừng. Mất mạng: tải tiếp được ở Thư viện riêng."));
    char pref[16];
    snprintf(pref, sizeof pref, "%s", !strcmp(jstrd(card, "selected", ""), "primary") ? "primary" : (arg[0] ? arg : "auto"));
    int n = jarr_n(r, "files");
    char title[64];
    snprintf(title, sizeof title, tr("Tải & cài %d file"), n);
    cJSON_Delete(r);
    ui_confirm(title, &L, tr("Tải & cài"), do_fetch, pref);
}

static void make_plan(const char *pref, int only) {
    g_last_only = only;
    fpaths_free();
    sv_t a = {0};
    sv_add(&a, "library"); sv_add(&a, "fetch"); sv_add(&a, "%s", g_node); sv_add(&a, "%s", g_root);
    for (int i = 0; i < g_ng; i++) {
        if (!(only >= 0 ? i == only : g_mark[i])) continue;
        const char *p = jstrd(entry(i), "path", "");
        sv_add(&a, "%s", p);
        g_fpaths = realloc(g_fpaths, (size_t)(g_nfp + 1) * sizeof *g_fpaths);
        g_fpaths[g_nfp++] = strdup(p);
    }
    if (g_nfp) {
        sv_add(&a, "--dry-run"); sv_add(&a, "--card"); sv_add(&a, "%s", pref);
        cli_task_v(tr("Đang kiểm với server"), LONG_TIMEOUT_MS, plan_done, pref, (const char *const *)a.v, a.n);
    }
    sv_free(&a);
}
static void confirm_primary(const char *arg) { make_plan(arg, g_last_only); }

static void do_fetch(const char *pref) {
    if (!g_nfp) return;
    sv_t a = {0};
    sv_add(&a, "library"); sv_add(&a, "fetch"); sv_add(&a, "%s", g_node); sv_add(&a, "%s", g_root);
    for (int i = 0; i < g_nfp; i++) sv_add(&a, "%s", g_fpaths[i]);
    sv_add(&a, "--yes"); sv_add(&a, "--progress"); sv_add(&a, "--card"); sv_add(&a, "%s", pref);
    if (cli_task_v(tr("Đang tải & cài"), LONG_TIMEOUT_MS, fetch_done, "", (const char *const *)a.v, a.n)) ui_task_cancelable(1);
    sv_free(&a);
}

static void fetch_done(lm_job *j, const char *arg) {
    (void)arg;
    int paused = ui_task_cancelled();
    lines_t L = {0};
    int clear = 0;
    if (paused) {
        lines_add(&L, tr("! Đã tạm dừng. File đã tải được giữ lại — mở Thư viện riêng > Tải dở để tiếp tục (Range + If-Range)."));
        lines_add(&L, tr("Chưa cài gì cả."));
    } else {
        cJSON *r = app_json_or_error(j, tr("Tải & cài"));
        if (!r) { fpaths_free(); return; }
        int ok = jbool_d(r, "success", 0), started = jbool_d(r, "started", 1);
        lines_add(&L, "%s%s", ok ? "+ " : "!! ", jstrd(r, "message", "?"));
        cJSON *x;
        cJSON_ArrayForEach(x, jarr(r, "results")) {
            const char *st = jstrd(x, "status", "?");
            const char *mark = (!strcmp(st, "installed") || !strcmp(st, "already_present")) ? "+" : (!strcmp(st, "blocked") || !strcmp(st, "failed")) ? "!!" : "!";
            lines_add(&L, "%s %s: %s — %s", mark, jstrd(x, "name", "?"), st, jstrd(x, "message", ""));
        }
        if (jbool_d(r, "resumable", 0))
            lines_add(&L, tr("! Job %s còn nguyên: mở Thư viện riêng > Tải dở để tiếp tục (file đã tải được giữ, tiếp bằng Range + If-Range)."), jstrd(r, "job_id", "?"));
        if (!started && jbool_d(r, "needs_card_choice", 0)) lines_add(&L, tr("!! Chưa chọn được thẻ đích."));
        app_add_rescan_lines(&L, jget(r, "rescan"));
        clear = ok;
        cJSON_Delete(r);
    }
    set_pending(paused ? tr("Đã tạm dừng") : tr("Kết quả tải & cài"), L);
    fpaths_free();
    if (clear && g_mark) memset(g_mark, 0, (size_t)(g_ng ? g_ng : 1));
    /* đứng yên tại chỗ: tải lại danh sách game (mục đã cài hiện "đã có"), rồi đọc lại overview (job dở), rồi hiện kết quả */
    if (ui_depth() >= 2 && g_system[0] && ui_top() && !strcmp(ui_top()->title, tr("THƯ VIỆN — game"))) reload_games(2);
    else lib_reload(2);
}

/* ============================================================ Library > Homebrew (tải trực tiếp, engine/homebrew.py)
 * Luồng: `homebrew list` (tóm tắt: hệ máy + job dở) -> "Cập nhật danh mục" (`homebrew sync --progress`, cần Internet) hoặc hệ máy
 *   -> `homebrew list --system S` (game; X chọn nhiều, Y sắp xếp Tên/Mới thêm, mục Lọc đã có/chưa có; khung giải thích = chi tiết: mô tả, tác giả,
 *   giấy phép, nguồn) -> `homebrew fetch --dry-run` (hỏi máy chủ kích thước, chọn thẻ như importer, HỎI khi phải dùng thẻ Leaf) -> hộp xác nhận
 *   mặc định Huỷ -> `homebrew fetch --yes --progress` (B = tạm dừng) -> kết quả của importer, đứng yên tại danh sách.
 * Chỉ game có giấy phép rõ ràng. Native không nói chuyện mạng: chỉ gọi CLI. */
static cJSON *g_hbov;            /* `homebrew list` (sở hữu); success=false + error_kind=not_found = chưa có danh mục */
static cJSON *g_hbg;             /* `homebrew list --system S` (sở hữu) */
static unsigned char *g_hbmark;
static int g_nhb;
static char g_hbsys[16];
static int g_hbsort, g_hbflt;    /* 0 tên / 1 mới thêm; 0 tất cả / 1 chưa có / 2 đã có */
static char **g_hbslugs;
static int g_nhbs;

static void hbslugs_free(void) { for (int i = 0; i < g_nhbs; i++) free(g_hbslugs[i]); free(g_hbslugs); g_hbslugs = NULL; g_nhbs = 0; }
static cJSON *hb_entries(void) { return jarr(g_hbg, "entries"); }
static cJSON *hb_entry(int i) { return cJSON_GetArrayItem(hb_entries(), i); }

/* như lib_ok_json nhưng gợi ý theo Internet/chứng thư (không phải Tailscale) */
static cJSON *hb_ok_json(lm_job *j, const char *title) {
    cJSON *r = app_json_or_error(j, title);
    if (!r) return NULL;
    if (jbool_d(r, "success", 0)) return r;
    lines_t L = {0};
    const char *k = jstrd(r, "error_kind", "");
    lines_add(&L, "!! %s", jstrd(r, "message", tr("Engine báo lỗi không kèm lý do.")));
    if (!strcmp(k, "tls")) lines_add(&L, tr("Máy chủ không xác minh được (chứng thư/bộ CA) nên KHÔNG tải. Không tắt kiểm tra."));
    else if (!strcmp(k, "unreachable") || !strcmp(k, "timeout") || !strcmp(k, "network")) lines_add(&L, tr("Kiểm tra Wi-Fi/Internet của máy (Homebrew tải thẳng qua Wi-Fi, không qua Tailscale)."));
    cJSON_Delete(r);
    ui_info(title, &L);
    return NULL;
}

/* ---- màn đầu: danh mục + hệ máy + job dở */
static void build_hb(ui_screen *s);
static void hb_ov_done(lm_job *j, const char *arg) {
    int mode = atoi(arg);                    /* 1 = mở màn mới; 0 = đọc lại tại chỗ; 2 = đọc lại rồi làm mới readiness */
    cJSON *r = app_json_or_error(j, "Homebrew");
    if (!r) return;
    if (!jbool_d(r, "success", 0) && strcmp(jstrd(r, "error_kind", ""), "not_found")) {   /* lỗi thật (không phải "chưa có danh mục") */
        lines_t L = {0};
        lines_add(&L, "!! %s", jstrd(r, "message", tr("Engine báo lỗi không kèm lý do.")));
        cJSON_Delete(r);
        ui_info("Homebrew", &L);
        return;
    }
    cJSON_Delete(g_hbov);
    g_hbov = r;
    if (mode == 1) ui_push(ui_screen_new("HOMEBREW (tải trực tiếp)", build_hb)); else ui_rebuild_all();
    show_pending();
    if (mode == 2) app_refresh();
}
static void hb_reload(int mode) { cli_task(tr("Đang đọc danh mục Homebrew"), SHORT_TIMEOUT_MS, hb_ov_done, mode == 2 ? "2" : "0", "homebrew", "list", NULL); }
void open_hb(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc danh mục Homebrew"), SHORT_TIMEOUT_MS, hb_ov_done, "1", "homebrew", "list", NULL);
}

static void hb_sync_done(lm_job *j, const char *arg) {
    (void)arg;
    if (ui_task_cancelled()) {
        lines_t L = {0};
        lines_add(&L, tr("! Đã dừng cập nhật. Danh mục cũ được giữ nguyên (chỉ thay khi tải xong toàn bộ)."));
        set_pending(tr("Đã dừng"), L);
        hb_reload(0);
        return;
    }
    cJSON *r = hb_ok_json(j, tr("Cập nhật danh mục"));
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "+ %s", jstrd(r, "message", "?"));
    lines_add(&L, tr("%.0f trang trong %.1f giây."), jnumd(r, "pages", 0), jnumd(r, "elapsed_s", 0));
    cJSON_Delete(r);
    set_pending(tr("Cập nhật danh mục"), L);
    hb_reload(0);
}
static void hb_sync(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    if (cli_task(tr("Đang cập nhật danh mục"), LONG_TIMEOUT_MS, hb_sync_done, "", "homebrew", "sync", "--progress", NULL)) ui_task_cancelable(1);
}

/* ---- tiếp tục / huỷ job dở (cùng cơ chế job với Thư viện riêng) */
static void hb_fetch_done(lm_job *j, const char *arg);
static void hb_do_resume(const char *arg) {
    if (cli_task(tr("Đang tải & cài"), LONG_TIMEOUT_MS, hb_fetch_done, "", "homebrew", "resume", arg, "--yes", "--progress", NULL)) ui_task_cancelable(1);
}
static void hb_ask_resume(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Tiếp tục job %s: %s."), it->arg, it->arg2);
    lines_add(&L, tr("Tải tiếp phần còn lại (Range + If-Range), kiểm hash rồi cài."));
    lines_add(&L, tr("B trong lúc tải = tạm dừng (tải tiếp được sau)."));
    ui_confirm(tr("Tiếp tục tải"), &L, tr("Tiếp tục"), hb_do_resume, it->arg);
}
static void hb_simple_done(lm_job *j, const char *arg) {
    cJSON *r = app_json_or_error(j, arg);
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "%s%s", jbool_d(r, "success", 0) ? "+ " : "!! ", jstrd(r, "message", "?"));
    cJSON_Delete(r);
    set_pending(arg, L);
    hb_reload(0);
}
static void hb_do_cancel(const char *arg) { cli_task(tr("Đang huỷ job"), SHORT_TIMEOUT_MS, hb_simple_done, tr("Huỷ job tải"), "homebrew", "cancel", arg, "--yes", NULL); }
static void hb_ask_cancel(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Xoá job %s và mọi file tải dở?"), it->arg);
    ui_confirm(tr("Huỷ job tải"), &L, tr("Huỷ job"), hb_do_cancel, it->arg);
}

static void hb_open_games(ui_screen *s, ui_item *it);
static void build_hb(ui_screen *s) {
    cJSON *x, *j;
    int have = jbool_d(g_hbov, "success", 0);
    ui_hdr(s, tr("Chỉ hiện game có GIẤY PHÉP RÕ RÀNG (Homebrew Hub); mục không giấy phép bị ẩn."));
    ui_hdr(s, tr("Tải qua Internet (HTTPS kiểm chứng thư), cài bằng Nhập game."));
    if (have) {
        char when[24];
        snprintf(when, sizeof when, "%.16s", jstrd(g_hbov, "fetched_at", "?"));
        for (char *c = when; *c; c++) if (*c == 'T') *c = ' ';
        ui_hdr(s, tr("%.0f game · cập nhật %s UTC · %.0f mục bị ẩn."), jnumd(g_hbov, "total", 0), when, jnumd(g_hbov, "rejected", 0));
    } else ui_hdr(s, tr("Chưa có danh mục: chọn Cập nhật danh mục (cần Internet, ~30 giây)."));
    ui_item *it = ui_add(s, tr("Cập nhật danh mục"), "", ST_YELLOW);
    ui_exp(it, tr("Tải toàn bộ danh mục Homebrew Hub (≤ 1 yêu cầu/giây, vài chục trang), giữ lại mục có giấy phép rõ ràng."));
    ui_exp(it, tr("Chỉ ghi cache trong dữ liệu LoongMate; không đụng thẻ nhớ. Lỗi/dừng giữa chừng thì giữ nguyên danh mục cũ."));
    ui_exp(it, tr("A — cập nhật.  B trong lúc tải = dừng."));
    ui_act(it, 0, tr("CẬP NHẬT"), hb_sync);
    if (!have) return;
    cJSON_ArrayForEach(x, jarr(g_hbov, "systems")) {
        char tag[32];
        snprintf(tag, sizeof tag, "%.0f game", jnumd(x, "count", 0));
        it = ui_add(s, jstrd(x, "folder", "?"), tag, ST_UNKNOWN);
        ui_exp(it, tr("Hệ %s: %.0f game có giấy phép rõ ràng."), jstrd(x, "folder", "?"), jnumd(x, "count", 0));
        ui_exp(it, tr("A — duyệt danh sách."));
        ui_arg(it, jstrd(x, "system", ""));
        ui_act(it, 0, tr("DUYỆT"), hb_open_games);
    }
    cJSON_ArrayForEach(j, jarr(g_hbov, "jobs")) {
        char lab[200], tag[24];
        cJSON *nm = cJSON_GetArrayItem(jarr(j, "names"), 0);
        snprintf(lab, sizeof lab, tr("Tải dở: %s%s"), cJSON_IsString(nm) ? nm->valuestring : "?", jarr_n(j, "names") > 1 ? "…" : "");
        snprintf(tag, sizeof tag, "%.0f%%", jnumd(j, "percent", 0));
        it = ui_add(s, lab, tag, ST_YELLOW);
        ui_exp(it, tr("Job %s trên thẻ %s"), jstrd(j, "job_id", "?"), !strcmp(jstrd(j, "card", ""), "primary") ? "SD1" : "SD2");
        ui_exp(it, "%s", jstr(j, "message") && jstr(j, "message")[0] ? jstr(j, "message") : tr("Mất mạng/ngủ máy giữa chừng: tiếp tục được, file đã tải được giữ."));
        ui_exp(it, tr("A — tiếp tục tải rồi cài.  X — huỷ job và xoá file tải dở."));
        ui_arg(it, jstrd(j, "job_id", ""));
        snprintf(it->arg2, sizeof it->arg2, "%.0f/%.0f file (%.0f%%)", jnumd(j, "files_done", 0), jnumd(j, "files", 0), jnumd(j, "percent", 0));
        ui_act(it, 0, tr("TIẾP TỤC"), hb_ask_resume);
        ui_act(it, 1, tr("HUỶ JOB"), hb_ask_cancel);
    }
}

/* ---- danh sách game của một hệ */
static void build_hbgames(ui_screen *s);
static void hb_adopt(cJSON *r, int keep) {
    cJSON *old = g_hbg;
    unsigned char *om = g_hbmark;
    int on = g_nhb;
    g_hbg = r;
    g_nhb = jarr_n(r, "entries");
    g_hbmark = calloc((size_t)(g_nhb ? g_nhb : 1), 1);
    if (keep && g_hbmark && om)
        for (int i = 0; i < g_nhb; i++) {
            const char *p = jstrd(hb_entry(i), "slug", "");
            for (int k = 0; k < on; k++)
                if (om[k] && !strcmp(jstrd(cJSON_GetArrayItem(jarr(old, "entries"), k), "slug", "\1"), p)) { g_hbmark[i] = 1; break; }
        }
    free(om);
    cJSON_Delete(old);
}
static void hb_games_done(lm_job *j, const char *arg) {
    int mode = atoi(arg);                    /* 1 = mở màn mới; 0 = tải lại tại chỗ; 2 = tải lại rồi đọc lại màn đầu */
    cJSON *r = hb_ok_json(j, "Homebrew");
    if (!r) return;
    hb_adopt(r, mode != 1);
    if (mode == 1) { g_hbsort = 0; g_hbflt = 0; ui_push(ui_screen_new("HOMEBREW — game", build_hbgames)); }
    else ui_rebuild_all();
    if (mode == 2) hb_reload(2); else show_pending();
}
static void hb_games_load(int mode) {
    char a[4];
    snprintf(a, sizeof a, "%d", mode);
    cli_task(tr("Đang đọc danh sách game"), LONG_TIMEOUT_MS, hb_games_done, a, "homebrew", "list", "--system", g_hbsys, NULL);
}
static void hb_open_games(ui_screen *s, ui_item *it) {
    (void)s;
    snprintf(g_hbsys, sizeof g_hbsys, "%s", it->arg);
    hb_games_load(1);
}

static int cmp_hb(const void *pa, const void *pb) {
    const cJSON *a = hb_entry(*(const int *)pa), *b = hb_entry(*(const int *)pb);
    if (g_hbsort == 1) {
        int d = strcmp(jstrd(b, "added", ""), jstrd(a, "added", ""));    /* ISO-8601: so chuỗi = so thời gian; mới trước */
        if (d) return d;
    }
    int c = strcasecmp(jstrd(a, "title", ""), jstrd(b, "title", ""));
    return c ? c : strcmp(jstrd(a, "slug", ""), jstrd(b, "slug", ""));
}
static int hb_marked(void) { int n = 0; for (int i = 0; i < g_nhb; i++) if (g_hbmark[i]) n++; return n; }

static void hb_make_plan(const char *pref, int only);
static void hb_plan_marked(ui_screen *s, ui_item *it) { (void)s; (void)it; hb_make_plan("auto", -1); }
static void hb_plan_one(ui_screen *s, ui_item *it) { (void)s; hb_make_plan("auto", it->iarg); }
static void hb_select(ui_screen *s, const char *slug) {
    for (int i = 0; i < s->n; i++)
        if (!s->items[i]->head && s->items[i]->iarg >= 0 && !strcmp(s->items[i]->arg, slug)) { s->sel = i; s->exp_scroll = 0; return; }
}
static void hb_first(ui_screen *s) {
    for (int i = 0; i < s->n; i++) if (!s->items[i]->head && s->items[i]->iarg >= 0) { s->sel = i; s->exp_scroll = 0; return; }
}
static void hb_keep(ui_screen *s) {
    char slug[UI_ARGLEN] = "";
    int ctl = 0;
    if (s->n && !s->items[s->sel]->head) {
        ui_item *cur = s->items[s->sel];
        if (cur->iarg >= 0) snprintf(slug, sizeof slug, "%s", cur->arg); else ctl = cur->iarg;
    }
    ui_rebuild(s);
    if (slug[0]) hb_select(s, slug);
    else if (ctl)
        for (int i = 0; i < s->n; i++) if (!s->items[i]->head && s->items[i]->iarg == ctl) { s->sel = i; s->exp_scroll = 0; break; }
}
static void hb_act_sort(ui_screen *s, ui_item *it) { (void)it; g_hbsort = !g_hbsort; hb_keep(s); }
static void hb_act_filter(ui_screen *s, ui_item *it) { (void)it; g_hbflt = (g_hbflt + 1) % 3; hb_keep(s); }
static void hb_act_toggle(ui_screen *s, ui_item *it) {
    if (it->iarg < 0 || it->iarg >= g_nhb) return;
    char slug[UI_ARGLEN];
    snprintf(slug, sizeof slug, "%s", it->arg);
    g_hbmark[it->iarg] ^= 1;
    ui_rebuild(s);
    hb_select(s, slug);
}
static void hb_act_clear(ui_screen *s, ui_item *it) {
    (void)it;
    memset(g_hbmark, 0, (size_t)(g_nhb ? g_nhb : 1));
    s->sel = 0;
    ui_rebuild(s);
    hb_first(s);
}

static void build_hbgames(ui_screen *s) {
    static const char *const SORT_LABEL[2] = { "Tên", "Mới thêm trước" };
    int *idx = calloc((size_t)(g_nhb ? g_nhb : 1), sizeof *idx), nidx = 0;
    if (!idx) return;
    for (int i = 0; i < g_nhb; i++) {
        int have = jbool_d(hb_entry(i), "present", 0);
        if (g_hbflt == 1 && have) continue;
        if (g_hbflt == 2 && !have) continue;
        idx[nidx++] = i;
    }
    qsort(idx, (size_t)nidx, sizeof *idx, cmp_hb);
    int nm = hb_marked();
    if (nidx == g_nhb) ui_hdr(s, tr("Hệ %s: %d game có giấy phép rõ ràng."), g_hbsys, g_nhb);
    else ui_hdr(s, tr("Hệ %s: hiện %d / %d game."), g_hbsys, nidx, g_nhb);
    ui_hdr(s, tr("Sắp xếp: %s · Lọc: %s."), tr(SORT_LABEL[g_hbsort]), tr(FLT_LABEL[g_hbflt]));
    if (nm) {
        int vis = 0;
        for (int k = 0; k < nidx; k++) if (g_hbmark[idx[k]]) vis++;
        if (nm > vis) ui_hdr(s, tr("Đã chọn %d game, %d đang bị bộ lọc ẩn."), nm, nm - vis);
        else ui_hdr(s, tr("Đã chọn %d game."), nm);
    } else ui_hdr(s, tr("Đã chọn 0 game."));
    ui_hdr(s, tr("Y: sắp xếp · X: chọn nhiều · A ở mục Lọc: đổi lọc · A ở game: tải & cài."));
    const char *ylbl = tr("SẮP XẾP");
    ui_item *it;
    if (nm) {
        char lab[64];
        snprintf(lab, sizeof lab, tr("Tải & cài %d game đã chọn"), nm);
        it = ui_add(s, lab, "", ST_GREEN);
        it->iarg = -1;
        ui_exp(it, tr("Đã chọn %d game."), nm);
        ui_exp(it, tr("A — hỏi máy chủ kích thước từng file, chọn thẻ đích và xem kế hoạch (chưa tải gì)."));
        ui_exp(it, tr("Gồm cả game đang bị bộ lọc ẩn. X: bỏ chọn tất cả."));
        ui_act(it, 0, tr("KIỂM & XEM KẾ HOẠCH"), hb_plan_marked);
        ui_act(it, 1, tr("BỎ CHỌN HẾT"), hb_act_clear);
        ui_act(it, 2, ylbl, hb_act_sort);
    }
    {
        char lab[48], tag[24];
        snprintf(lab, sizeof lab, tr("Lọc: %s"), tr(FLT_LABEL[g_hbflt]));
        snprintf(tag, sizeof tag, "%d game", nidx);
        it = ui_add(s, lab, tag, ST_UNKNOWN);
        it->iarg = -2;
        ui_exp(it, tr("Lọc theo việc game đã có trong Roms/ của máy (so tên file) hay chưa."));
        ui_exp(it, tr("Dấu chọn (X) được giữ nguyên khi đổi lọc hay đổi sắp xếp."));
        ui_exp(it, tr("A: đổi sang \"%s\". Y: đổi cách sắp xếp (Tên / Mới thêm)."), tr(FLT_LABEL[(g_hbflt + 1) % 3]));
        ui_act(it, 0, tr("ĐỔI LỌC"), hb_act_filter);
        ui_act(it, 2, ylbl, hb_act_sort);
    }
    for (int k = 0; k < nidx; k++) {
        int i = idx[k];
        cJSON *e = hb_entry(i);
        int mk = g_hbmark[i], have = jbool_d(e, "present", 0);
        char tag[64], label[260];
        snprintf(tag, sizeof tag, "%s%s", have ? tr("đã có ") : "", jstrd(e, "license_id", "?"));
        snprintf(label, sizeof label, "%s%s", mk ? "[x] " : "[ ] ", jstrd(e, "title", "?"));
        it = ui_add(s, label, tag, mk ? ST_GREEN : ST_UNKNOWN);
        ui_arg(it, jstrd(e, "slug", ""));
        it->iarg = i;
        if (mk) ui_exp(it, tr("+ ĐÃ CHỌN để tải & cài (X để bỏ chọn)."));
        ui_exp(it, "%s", jstr(e, "description") && jstr(e, "description")[0] ? jstr(e, "description") : tr("(không có mô tả)"));
        ui_exp(it, tr("Tác giả: %s"), jstrd(e, "author", "?"));
        if (jstr(e, "license_assets") && jstr(e, "license_assets")[0]) ui_exp(it, tr("Giấy phép: %s (tài nguyên: %s)"), jstrd(e, "license", "?"), jstr(e, "license_assets"));
        else ui_exp(it, tr("Giấy phép: %s"), jstrd(e, "license", "?"));
        const char *src = jstr(e, "repository") && jstr(e, "repository")[0] ? jstr(e, "repository") : (jstr(e, "website") && jstr(e, "website")[0] ? jstr(e, "website") : jstrd(e, "url", "?"));
        ui_exp(it, tr("Nguồn: %s"), src);
        ui_exp(it, "File: %s", jstrd(e, "file", "?"));
        ui_exp(it, tr("Trên máy: %s"), have ? tr("ĐÃ CÓ file cùng tên trong Roms/") : tr("chưa có"));
        ui_exp(it, tr("A — kiểm với máy chủ và xem kế hoạch tải & cài game này."));
        ui_exp(it, tr("X — chọn / bỏ chọn để tải nhiều game.  Y — đổi cách sắp xếp."));
        ui_act(it, 0, tr("TẢI & CÀI…"), hb_plan_one);
        ui_act(it, 1, mk ? tr("BỎ CHỌN") : tr("CHỌN"), hb_act_toggle);
        ui_act(it, 2, ylbl, hb_act_sort);
    }
    free(idx);
    if (!nidx) ui_head(s, g_nhb ? tr("Không có game nào khớp bộ lọc") : tr("Không có game nào"));
    if (s->sel == 0) hb_first(s);
}

/* ---- kế hoạch -> xác nhận -> tải & cài */
static void hb_confirm_primary(const char *arg);
static void hb_do_fetch(const char *arg);
static int g_hb_last_only = -1;

static void hb_plan_done(lm_job *j, const char *arg) {
    cJSON *r = hb_ok_json(j, tr("Tải & cài"));
    if (!r) { hbslugs_free(); return; }
    cJSON *card = jget(r, "card"), *f, *p, *o;
    if (jbool_d(r, "needs_card_choice", 0)) {
        lines_t L = {0};
        lines_add(&L, "!! %s", jstrd(card, "reason", ""));
        lines_add(&L, "%s", "");
        lines_add(&L, tr("LoongMate KHÔNG tự chuyển sang thẻ chính — bạn quyết định."));
        int usable = 0, nprob = jarr_n(r, "problems");
        cJSON_ArrayForEach(o, jarr(card, "options")) if (!strcmp(jstrd(o, "card", ""), "primary") && jbool_d(o, "usable", 0)) usable = 1;
        cJSON_ArrayForEach(p, jarr(r, "problems")) lines_add(&L, "!! %s: %s", jstrd(p, "path", "?"), jstrd(p, "reason", "?"));
        cJSON_Delete(r);
        if (usable && !nprob) ui_confirm(tr("Thẻ phụ không dùng được"), &L, tr("Dùng thẻ Leaf"), hb_confirm_primary, "primary");
        else { hbslugs_free(); ui_info(usable ? tr("Không tải được") : tr("Không có thẻ nào dùng được"), &L); }
        return;
    }
    if (!jbool_d(r, "ok", 0)) {
        lines_t L = {0};
        cJSON_ArrayForEach(p, jarr(r, "problems")) lines_add(&L, "!! %s: %s", jstrd(p, "path", "?"), jstrd(p, "reason", "?"));
        if (!L.n) lines_add(&L, "!! %s", jstrd(r, "message", "?"));
        cJSON_Delete(r);
        hbslugs_free();
        ui_info(tr("Không tải được"), &L);
        return;
    }
    if (ui_card_pick(card, arg, hb_confirm_primary)) { cJSON_Delete(r); return; }  /* hai thẻ dùng được: hỏi */
    lines_t L = {0};
    lines_add(&L, tr("Thẻ đích: %s"), jstrd(card, "root", "?"));
    lines_add(&L, "%s", "");
    cJSON_ArrayForEach(f, jarr(r, "files"))
        lines_add(&L, "+ %s (%s) -> %s (%s)%s", jstrd(f, "title", "?"), jstrd(f, "license_id", "?"), jstrd(f, "system", "?"), mbs(jnumd(f, "size", 0)),
                  jbool_d(f, "present", 0) ? tr("  [đã có: importer bỏ qua nếu giống hệt]") : "");
    lines_add(&L, "%s", "");
    lines_add(&L, tr("Tổng %s; cần ~%s trống (file tải + staging + dự phòng)."), mbs(jnumd(r, "total_bytes", 0)), mbs(jnumd(r, "needed_bytes", 0)));
    lines_add(&L, tr("Tải thẳng qua Internet (HTTPS kiểm chứng thư; chỉ máy chủ Homebrew Hub/GitHub). Hash trong danh mục sai thì KHÔNG cài."));
    lines_add(&L, tr("Cài bằng đúng trình Nhập game. Ghi chú giấy phép lưu ngoài Roms/ (dữ liệu LoongMate)."));
    lines_add(&L, tr("Trong lúc tải: B = tạm dừng. Mất mạng: tải tiếp được ở Homebrew > Tải dở."));
    char pref[16];
    snprintf(pref, sizeof pref, "%s", !strcmp(jstrd(card, "selected", ""), "primary") ? "primary" : (arg[0] ? arg : "auto"));
    int n = jarr_n(r, "files");
    char title[64];
    snprintf(title, sizeof title, tr("Tải & cài %d game"), n);
    cJSON_Delete(r);
    ui_confirm(title, &L, tr("Tải & cài"), hb_do_fetch, pref);
}

static void hb_make_plan(const char *pref, int only) {
    g_hb_last_only = only;
    hbslugs_free();
    sv_t a = {0};
    sv_add(&a, "homebrew"); sv_add(&a, "fetch");
    for (int i = 0; i < g_nhb; i++) {
        if (!(only >= 0 ? i == only : g_hbmark[i])) continue;
        const char *p = jstrd(hb_entry(i), "slug", "");
        sv_add(&a, "%s", p);
        g_hbslugs = realloc(g_hbslugs, (size_t)(g_nhbs + 1) * sizeof *g_hbslugs);
        g_hbslugs[g_nhbs++] = strdup(p);
    }
    if (g_nhbs) {
        sv_add(&a, "--dry-run"); sv_add(&a, "--card"); sv_add(&a, "%s", pref);
        cli_task_v(tr("Đang kiểm với máy chủ"), LONG_TIMEOUT_MS, hb_plan_done, pref, (const char *const *)a.v, a.n);
    }
    sv_free(&a);
}
static void hb_confirm_primary(const char *arg) { hb_make_plan(arg, g_hb_last_only); }

static void hb_do_fetch(const char *pref) {
    if (!g_nhbs) return;
    sv_t a = {0};
    sv_add(&a, "homebrew"); sv_add(&a, "fetch");
    for (int i = 0; i < g_nhbs; i++) sv_add(&a, "%s", g_hbslugs[i]);
    sv_add(&a, "--yes"); sv_add(&a, "--progress"); sv_add(&a, "--card"); sv_add(&a, "%s", pref);
    if (cli_task_v(tr("Đang tải & cài"), LONG_TIMEOUT_MS, hb_fetch_done, "", (const char *const *)a.v, a.n)) ui_task_cancelable(1);
    sv_free(&a);
}

static void hb_fetch_done(lm_job *j, const char *arg) {
    (void)arg;
    int paused = ui_task_cancelled();
    lines_t L = {0};
    int clear = 0;
    if (paused) {
        lines_add(&L, tr("! Đã tạm dừng. File đã tải được giữ lại — mở Homebrew > Tải dở để tiếp tục (Range + If-Range)."));
        lines_add(&L, tr("Chưa cài gì cả."));
    } else {
        cJSON *r = app_json_or_error(j, tr("Tải & cài"));
        if (!r) { hbslugs_free(); return; }
        int ok = jbool_d(r, "success", 0), started = jbool_d(r, "started", 1);
        lines_add(&L, "%s%s", ok ? "+ " : "!! ", jstrd(r, "message", "?"));
        cJSON *x;
        cJSON_ArrayForEach(x, jarr(r, "results")) {
            const char *st = jstrd(x, "status", "?");
            const char *mark = (!strcmp(st, "installed") || !strcmp(st, "already_present")) ? "+" : (!strcmp(st, "blocked") || !strcmp(st, "failed")) ? "!!" : "!";
            lines_add(&L, "%s %s: %s — %s", mark, jstrd(x, "name", "?"), st, jstrd(x, "message", ""));
        }
        if (jbool_d(r, "resumable", 0))
            lines_add(&L, tr("! Job %s còn nguyên: mở Homebrew > Tải dở để tiếp tục (file đã tải được giữ, tiếp bằng Range + If-Range)."), jstrd(r, "job_id", "?"));
        if (!started && jbool_d(r, "needs_card_choice", 0)) lines_add(&L, tr("!! Chưa chọn được thẻ đích."));
        app_add_rescan_lines(&L, jget(r, "rescan"));
        clear = ok;
        cJSON_Delete(r);
    }
    set_pending(paused ? tr("Đã tạm dừng") : tr("Kết quả tải & cài"), L);
    hbslugs_free();
    if (clear && g_hbmark) memset(g_hbmark, 0, (size_t)(g_nhb ? g_nhb : 1));
    /* đứng yên tại chỗ: tải lại danh sách (mục đã cài hiện "đã có"), rồi đọc lại màn đầu (job dở), rồi hiện kết quả */
    if (ui_depth() >= 2 && g_hbsys[0] && ui_top() && !strcmp(ui_top()->title, "HOMEBREW — game")) hb_games_load(2);
    else hb_reload(2);
}
