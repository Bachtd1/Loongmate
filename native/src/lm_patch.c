/* LoongMate native — Library > Patch ROM (áp file patch .ips/.bps/.ups lên ROM gốc, tạo ROM MỚI cạnh bản gốc). Mọi việc đi qua
 * `cli.py patch ... --json`; native không đọc/ghi/băm file. Luồng:
 *   `patch list` -> màn PATCH ROM; A = ÁP DỤNG -> `patch candidates ID` (BPS/UPS: ROM gốc khớp CRC; IPS: chọn hệ máy rồi
 *   `--system S` chọn ROM bằng tay) -> hộp xác nhận mặc định Huỷ -> `patch apply ID --rom PATH [--header-skipped] --yes`.
 * Đường dẫn ROM có thể dài hơn UI_ARGLEN (640) nên không đi qua ui_item.arg/tham số callback: giữ trong biến toàn cục (g_rom...),
 * hoặc tra lại trong JSON của màn (iarg = chỉ số). */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>

static ui_screen *g_pl;        /* màn "PATCH ROM" đang mở (NULL khi chưa/đã đóng); s->d = `patch list` */
static int g_ldepth;           /* độ sâu ngăn xếp của màn PATCH ROM */
static char *g_pid, *g_pname;  /* patch đang xử lý (id, tên) */
static int g_ips;              /* patch đang xử lý là IPS (không có mã kiểm tra) */
static char *g_rom;            /* đường dẫn ROM gốc đã chọn (dài tuỳ ý) */
static char g_rom_name[200], g_rom_sys[64], g_rom_card[24];
static int g_hs;               /* ROM khớp sau khi bỏ header (--header-skipped) */
static lines_t g_pend;         /* kết quả đã dựng, chờ hiện sau khi danh sách được làm mới */
static char g_pend_title[64];

static const char *mbs(double bytes) { static char b[4][32]; static int i; i = (i + 1) & 3; ui_fmt_bytes(b[i], sizeof b[i], bytes); return b[i]; }
static const char *card_vi(const char *c) { return !strcmp(c, "primary") ? "SD1" : !strcmp(c, "secondary") ? "SD2" : c; }
static void sset(char **p, const char *v) { char *n = strdup(v ? v : ""); free(*p); *p = n; }

static void set_pending(const char *title, lines_t L) {
    lines_free(&g_pend);
    g_pend = L;
    snprintf(g_pend_title, sizeof g_pend_title, "%s", title);
}
static void show_pending(void) {
    if (!g_pend.n) { lines_free(&g_pend); return; }
    lines_t L = g_pend;
    g_pend = (lines_t){0};
    ui_info(g_pend_title, &L);
}

/* JSON thành công, hoặc hiện nguyên văn message của engine rồi trả NULL */
static cJSON *patch_ok_json(lm_job *j, const char *title) {
    cJSON *r = app_json_or_error(j, title);
    if (!r) return NULL;
    if (jbool_d(r, "success", 0)) return r;
    lines_t L = {0};
    lines_add(&L, "!! %s", jstrd(r, "message", tr("Engine báo lỗi không kèm lý do.")));
    cJSON_Delete(r);
    ui_info(title, &L);
    return NULL;
}

/* ============================================================ danh sách patch */
static void list_done(lm_job *j, const char *arg) {
    (void)arg;
    char err[300] = "";
    cJSON *r = ui_job_json(j, err, sizeof err);
    if (r && !jbool_d(r, "success", 0)) { snprintf(err, sizeof err, "%s", jstrd(r, "message", tr("engine báo lỗi"))); cJSON_Delete(r); r = NULL; }
    if (r && g_pl) { cJSON_Delete(g_pl->d); g_pl->d = r; ui_rebuild(g_pl); }
    else if (r) cJSON_Delete(r);
    else {
        if (!g_pend.n) snprintf(g_pend_title, sizeof g_pend_title, "Patch ROM");
        lines_add(&g_pend, tr("! Không làm mới được danh sách patch: %s"), err);
    }
    show_pending();
}
static void list_reload(void) {
    if (!g_pl) { show_pending(); return; }
    cli_task(tr("Đang đọc danh sách patch"), SHORT_TIMEOUT_MS, list_done, "", "patch", "list", NULL);
}

/* ============================================================ áp dụng: xác nhận -> patch apply */
static void apply_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = patch_ok_json(j, tr("Áp dụng patch"));
    if (!r) return;     /* lỗi (kể cả CRC không khớp): message nguyên văn, đứng yên để thử ROM khác */
    lines_t L = {0};
    lines_add(&L, tr("+ Đã tạo ROM mới: %s"), jstrd(r, "name", "?"));
    lines_add(&L, tr("Dung lượng: %s"), mbs(jnumd(r, "size", 0)));
    if (jbool_d(r, "already", 0)) lines_add(&L, tr("! File này đã có sẵn (đã áp dụng trước đó) — không tạo lại."));
    if (jbool_d(r, "verified", 0)) lines_add(&L, tr("+ Đã kiểm CRC kết quả: khớp"));
    const char *w = jstr(r, "warning");
    if (w && w[0]) lines_add(&L, "! %s", w);
    lines_add(&L, tr("Bản gốc giữ nguyên. Save của ROM mới nằm riêng."));
    app_add_rescan_lines(&L, jget(r, "rescan"));
    cJSON_Delete(r);
    set_pending(tr("Kết quả áp dụng patch"), L);
    ui_pop_to(g_ldepth);
    list_reload();
}

static void do_apply(const char *arg) {
    (void)arg;
    if (g_hs) cli_task(tr("Đang áp dụng patch"), LONG_TIMEOUT_MS, apply_done, "", "patch", "apply", g_pid, "--rom", g_rom, "--header-skipped", "--yes", NULL);
    else cli_task(tr("Đang áp dụng patch"), LONG_TIMEOUT_MS, apply_done, "", "patch", "apply", g_pid, "--rom", g_rom, "--yes", NULL);
}

static void ask_apply(void) {
    lines_t L = {0};
    lines_add(&L, "Patch: %s", g_pname ? g_pname : "?");
    lines_add(&L, tr("ROM gốc: %s"), g_rom_name);
    lines_add(&L, tr("Hệ máy: %s · Thẻ: %s"), g_rom_sys[0] ? g_rom_sys : "?", g_rom_card[0] ? card_vi(g_rom_card) : "?");
    lines_add(&L, "%s", "");
    lines_add(&L, tr("File mới sẽ nằm cạnh bản gốc; bản gốc không bị sửa."));
    if (g_ips) lines_add(&L, tr("! IPS không có mã kiểm tra — nếu sai bản ROM gốc, game có thể lỗi."));
    lines_add(&L, "%s", "");
    lines_add(&L, tr("Đường dẫn ROM gốc: %s"), g_rom ? g_rom : "?");   /* cuối cùng: có thể rất dài, không được đẩy cảnh báo ra ngoài tầm nhìn */
    ui_confirm(tr("Áp dụng patch"), &L, tr("Áp dụng"), do_apply, "");
}

/* ============================================================ chọn ROM gốc (khớp CRC, hoặc IPS chọn tay) */
static void act_rom(ui_screen *s, ui_item *it) {
    cJSON *m = cJSON_GetArrayItem(jarr(s->d, s->key), it->iarg);
    const char *p = jstr(m, "path");
    if (!p) return;
    sset(&g_rom, p);
    snprintf(g_rom_name, sizeof g_rom_name, "%s", jstrd(m, "name", "?"));
    snprintf(g_rom_sys, sizeof g_rom_sys, "%s", jstrd(m, "system", ""));
    snprintf(g_rom_card, sizeof g_rom_card, "%s", jstrd(m, "card", ""));
    g_hs = jbool_d(m, "header_skipped", 0);
    ask_apply();
}

static void build_roms(ui_screen *s) {     /* s->key = "matches" (BPS/UPS) hoặc "roms" (IPS) */
    cJSON *m;
    int i = 0, crc = !strcmp(s->key, "matches");
    if (crc) ui_hdr(s, tr("ROM gốc khớp CRC32 %s (%.0f byte). Đã quét %.0f file."), jstrd(s->d, "source_crc32", "?"), jnumd(s->d, "source_size", 0), jnumd(s->d, "scanned", 0));
    else ui_hdr(s, tr("IPS không kiểm được bản ROM: hãy chọn đúng bản ROM gốc mà patch này làm cho."));
    cJSON_ArrayForEach(m, jarr(s->d, s->key)) {
        const char *sys = jstrd(m, "system", ""), *card = jstrd(m, "card", "");
        char tag[96];
        snprintf(tag, sizeof tag, "%s · %s", sys, card_vi(card));
        ui_item *it = ui_add(s, jstrd(m, "name", "?"), tag, ST_UNKNOWN);
        if (!it) return;
        ui_exp(it, tr("Dung lượng: %s"), mbs(jnumd(m, "size", 0)));
        if (jbool_d(m, "header_skipped", 0)) ui_exp(it, tr("Khớp sau khi bỏ header của ROM."));
        ui_exp(it, "%s", jstrd(m, "path", ""));
        it->iarg = i++;
        ui_act(it, 0, tr("CHỌN"), act_rom);
    }
}

static void push_roms(cJSON *r, const char *key) {
    ui_screen *s = ui_screen_new("CHỌN ROM GỐC", build_roms);
    if (!s) { cJSON_Delete(r); return; }
    snprintf(s->key, sizeof s->key, "%s", key);
    s->d = r;
    ui_push(s);
}

static void roms_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = patch_ok_json(j, tr("Chọn ROM gốc"));
    if (!r) return;
    if (!jarr_n(r, "roms")) {
        cJSON_Delete(r);
        ui_info1(tr("Chọn ROM gốc"), tr("Không có ROM nào của hệ máy này."));
        return;
    }
    push_roms(r, "roms");
}

/* màn hệ máy (IPS): A -> `patch candidates ID --system S` */
static void act_system(ui_screen *s, ui_item *it) {
    const char *sys = jstr(cJSON_GetArrayItem(jarr(s->d, "systems"), it->iarg), "system");
    if (!sys) return;
    cli_task(tr("Đang liệt kê ROM"), LONG_TIMEOUT_MS, roms_done, "", "patch", "candidates", g_pid, "--system", sys, NULL);
}
static void build_systems(ui_screen *s) {
    cJSON *m;
    int i = 0;
    ui_hdr(s, tr("IPS không có mã kiểm tra nên không tự tìm được ROM gốc. Chọn hệ máy của ROM, rồi chọn đúng bản ROM gốc."));
    cJSON_ArrayForEach(m, jarr(s->d, "systems")) {
        char tag[32];
        snprintf(tag, sizeof tag, "%.0f ROM", jnumd(m, "count", 0));
        ui_item *it = ui_add(s, jstrd(m, "system", "?"), tag, ST_UNKNOWN);
        if (!it) return;
        ui_exp(it, tr("A — xem các ROM của hệ máy này."));
        it->iarg = i++;
        ui_act(it, 0, tr("MỞ"), act_system);
    }
}

static void cand_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = patch_ok_json(j, tr("Tìm ROM gốc"));
    if (!r) return;
    const char *v = jstrd(r, "verify", "");
    if (!strcmp(v, "crc")) {
        if (!jarr_n(r, "matches")) {
            lines_t L = {0};
            lines_add(&L, tr("Không tìm thấy ROM gốc khớp (cần CRC32 %s, %.0f byte). Patch thường dành cho một bản cụ thể (vd. bản USA chưa sửa). Chép đúng bản ROM gốc vào thẻ rồi thử lại."),
                      jstrd(r, "source_crc32", "?"), jnumd(r, "source_size", 0));
            cJSON_Delete(r);
            ui_info(tr("Không có ROM gốc khớp"), &L);
            return;
        }
        push_roms(r, "matches");
    } else if (!strcmp(v, "none")) {
        if (!jarr_n(r, "systems")) {
            cJSON_Delete(r);
            ui_info1(tr("Chọn ROM gốc"), tr("Không tìm thấy ROM nào trên thẻ để áp patch."));
            return;
        }
        ui_screen *s = ui_screen_new("CHỌN HỆ MÁY", build_systems);
        if (!s) { cJSON_Delete(r); return; }
        s->d = r;
        ui_push(s);
    } else {
        cJSON_Delete(r);
        ui_info1(tr("Tìm ROM gốc"), tr("Engine trả kiểu kiểm lạ: %s"), v);
    }
}

/* ============================================================ màn PATCH ROM */
static void act_apply(ui_screen *s, ui_item *it) {
    cJSON *p = cJSON_GetArrayItem(jarr(s->d, "patches"), it->iarg);
    const char *id = jstr(p, "id");
    if (!id) return;
    sset(&g_pid, id);
    sset(&g_pname, jstrd(p, "name", id));
    g_ips = !strcasecmp(jstrd(p, "format", ""), "ips");
    cli_task(tr("Đang tìm ROM gốc khớp CRC…"), LONG_TIMEOUT_MS, cand_done, "", "patch", "candidates", g_pid, NULL);
}

static void act_broken(ui_screen *s, ui_item *it) {
    cJSON *p = cJSON_GetArrayItem(jarr(s->d, "patches"), it->iarg);
    lines_t L = {0};
    lines_add(&L, "!! %s", jstrd(p, "error", tr("Patch hỏng.")));
    lines_add(&L, tr("Không áp dụng được patch này."));
    ui_info(jstrd(p, "name", tr("Patch hỏng")), &L);
}

static void delete_done(lm_job *j, const char *arg) {
    cJSON *r = patch_ok_json(j, arg);
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "+ %s", jstrd(r, "message", tr("Đã xoá patch.")));
    cJSON_Delete(r);
    set_pending(arg, L);
    list_reload();
}
static void do_delete(const char *arg) {
    (void)arg;
    cli_task(tr("Đang xoá patch"), SHORT_TIMEOUT_MS, delete_done, tr("Xoá patch"), "patch", "delete", g_pid, "--yes", NULL);
}
static void ask_delete(ui_screen *s, ui_item *it) {
    cJSON *p = cJSON_GetArrayItem(jarr(s->d, "patches"), it->iarg);
    const char *id = jstr(p, "id");
    if (!id) return;
    sset(&g_pid, id);
    lines_t L = {0};
    lines_add(&L, tr("Xoá patch này khỏi kho patch của LoongMate?"));
    lines_add(&L, "%s", jstrd(p, "name", id));
    lines_add(&L, tr("ROM đã tạo từ patch không bị ảnh hưởng."));
    ui_confirm(tr("Xoá patch"), &L, tr("Xoá"), do_delete, "");
}

static void build_patches(ui_screen *s) {
    cJSON *p;
    int i = 0;
    ui_hdr(s, tr("Áp file patch .ips/.bps/.ups lên ROM gốc của bạn, tạo file ROM MỚI cạnh bản gốc (bản gốc giữ nguyên, save riêng)."));
    ui_hdr(s, tr("BPS/UPS tự tìm đúng ROM gốc bằng CRC; IPS phải chọn tay."));
    if (!jarr_n(s->d, "patches")) {
        ui_item *it = ui_add(s, tr("Chưa có patch nào"), "", ST_UNKNOWN);
        if (!it) return;
        ui_exp(it, tr("Cách thêm patch:"));
        ui_exp(it, tr("- Mở Library > Tải từ web, chọn một link .ips/.bps/.ups hoặc file zip chứa patch."));
        ui_exp(it, tr("- Hoặc chép file patch vào thư mục Import/ trên thẻ nhớ, rồi mở lại màn này."));
        return;
    }
    cJSON_ArrayForEach(p, jarr(s->d, "patches")) {
        int idx = i++, bad = jstr(p, "error") != NULL;
        char tag[32] = "", fmt[8];
        const char *f = jstrd(p, "format", "");
        size_t k = 0;
        for (; f[k] && k < sizeof fmt - 1; k++) fmt[k] = (char)toupper((unsigned char)f[k]);
        fmt[k] = 0;
        if (bad) snprintf(tag, sizeof tag, tr("hỏng"));
        else snprintf(tag, sizeof tag, "%s%s", fmt, jbool_d(p, "verifies", 0) ? " CRC" : "");
        ui_item *it = ui_add(s, jstrd(p, "name", jstrd(p, "id", "?")), tag, bad ? ST_RED : ST_UNKNOWN);
        if (!it) return;
        int store = !strcmp(jstrd(p, "where", ""), "store");
        if (bad) ui_exp(it, "!! %s", jstrd(p, "error", ""));
        const char *md = jstrd(p, "metadata", "");
        if (md[0]) ui_exp(it, "%s", md);
        ui_exp(it, tr("Định dạng: %s · Dung lượng: %s"), fmt, mbs(jnumd(p, "size", 0)));
        if (jstr(p, "source_crc32")) ui_exp(it, tr("ROM gốc cần: CRC32 %s, %.0f byte"), jstr(p, "source_crc32"), jnumd(p, "source_size", 0));
        else if (!bad && !jbool_d(p, "verifies", 0)) ui_exp(it, tr("Không có mã kiểm tra: bạn tự chọn ROM gốc."));
        if (store) ui_exp(it, tr("Nằm trong kho patch của LoongMate."));
        else ui_exp(it, tr("Nằm trong Import/ trên thẻ %s (chưa lưu vào kho)."), jstr(p, "card") ? card_vi(jstr(p, "card")) : "?");
        ui_exp(it, bad ? tr("A — xem lỗi.") : tr("A — áp dụng: tìm ROM gốc, xác nhận rồi tạo ROM mới."));
        if (store) ui_exp(it, tr("X — xoá patch khỏi kho."));
        it->iarg = idx;
        ui_act(it, 0, bad ? tr("XEM LỖI") : tr("ÁP DỤNG"), bad ? act_broken : act_apply);
        if (store) ui_act(it, 1, tr("XOÁ"), ask_delete);
    }
}

static void patch_free(ui_screen *s) { if (g_pl == s) g_pl = NULL; }

static void open_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = patch_ok_json(j, "Patch ROM");
    if (!r) return;
    ui_screen *s = ui_screen_new("PATCH ROM", build_patches);
    if (!s) { cJSON_Delete(r); return; }
    s->d = r;
    s->on_free = patch_free;
    g_pl = s;
    ui_push(s);
    g_ldepth = ui_depth();
}

void open_patch(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc danh sách patch"), SHORT_TIMEOUT_MS, open_done, "", "patch", "list", NULL);
}
