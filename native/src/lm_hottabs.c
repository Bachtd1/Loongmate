/* LoongMate native — Leaf Hot Tabs (cli.py hottabs status|on|off|install|uninstall --json).
 * Native không đọc file cờ hay md5 launcher: mọi thứ do engine làm. Cài/gỡ luôn xem trước (CLI không --yes, chỉ chạy
 * hottabs.sh --dry-run) rồi hộp xác nhận mặc định Huỷ, sau đó mới gọi lại với --yes. */
#define _GNU_SOURCE
#include "lm_app.h"
#include <stdio.h>
#include <string.h>

#define HT_RESTART "Khởi động lại máy để có hiệu lực"

static cJSON *g_ht;     /* `hottabs status` mới nhất */

static const char *inst(void) { return jstrd(g_ht, "installed", "unknown"); }

static void ht_toggle(ui_screen *s, ui_item *it);
static void ar_toggle(ui_screen *s, ui_item *it);
static void ht_install(ui_screen *s, ui_item *it);
static void ht_uninstall(ui_screen *s, ui_item *it);

static void build_hottabs(ui_screen *s) {
    const char *in = inst();
    int matches = jbool_d(g_ht, "bundle_matches", 0), on = jbool_d(g_ht, "resume_from_lists", 1);
    const char *bver = jstr(g_ht, "bundle_version");
    const char *line, *tag;
    int tc;
    if (!matches && bver) { line = "Không khớp phiên bản Leaf — cần build lại bộ cài"; tag = "không khớp"; tc = ST_RED; }
    else if (!strcmp(in, "patched")) { line = "Đang dùng bản Hot Tabs"; tag = "Hot Tabs"; tc = ST_GREEN; }
    else if (!strcmp(in, "stock")) { line = "Bản gốc của Leaf (chưa cài Hot Tabs)"; tag = "bản gốc"; tc = ST_YELLOW; }
    else if (!strcmp(in, "absent")) { line = "Không thấy launcher của Leaf trên thẻ"; tag = "không có"; tc = ST_RED; }
    else { line = "Không nhận diện được launcher (không có bộ cài trên thẻ hoặc md5 lạ)"; tag = "không rõ"; tc = ST_YELLOW; }
    ui_hdr(s, "Trạng thái: %s", line);
    ui_hdr(s, "Leaf: %s; bộ cài: %s", jstrd(g_ht, "leaf_version", "không đọc được"), bver ? bver : "không có trên thẻ");
    ui_hdr(s, "Leaf vừa cập nhật? Hot Tabs bị thay về bản gốc — xem docs/CAP-NHAT-LEAF.md");

    ui_head(s, "Trạng thái");
    ui_item *it = ui_add(s, "Launcher", tag, tc);
    ui_exp(it, "%s", jstrd(g_ht, "message", ""));
    ui_exp(it, "Phiên bản Leaf: %s  (commit launcher %s)", jstrd(g_ht, "leaf_version", "?"), jstrd(g_ht, "launcher_commit", "?"));
    ui_exp(it, "Bộ cài trên thẻ: %s%s", bver ? bver : "không có", matches ? " — khớp Leaf này." : " — KHÔNG khớp Leaf này.");
    ui_exp(it, "Bản gốc đã sao lưu: %s", jbool_d(g_ht, "backup_present", 0) ? "có" : "không");
    ui_exp(it, "Cập nhật Leaf thay cả thư mục launcher nên Hot Tabs biến mất (máy về bản gốc, vô hại). Cài lại theo docs/CAP-NHAT-LEAF.md.");

    ui_head(s, "Tính năng");
    it = ui_add(s, "Tự tiếp tục khi mở từ Games/Favorites", on ? "Bật" : "Tắt", on ? ST_GREEN : ST_YELLOW);
    ui_exp(it, "Bật: mở game từ Games/Favorites thì vào thẳng chỗ đang chơi dở như Recents (game chưa có state thì chạy mới).");
    ui_exp(it, "Tắt: Games/Favorites luôn chạy mới như Leaf gốc.");
    ui_exp(it, "A đổi ngay; launcher kiểm mỗi lần mở game nên không cần khởi động lại.");
    ui_exp(it, "Không tốn pin: không có gì chạy nền, chỉ khác lúc mở game.");
    ui_exp(it, strcmp(in, "patched") ? "! Chỉ có tác dụng khi đang dùng bản Hot Tabs." : "Ghi một file cờ nhỏ trên thẻ; bật = xoá file cờ.");
    ui_act(it, 0, on ? "TẮT" : "BẬT", ht_toggle);

    int ar = jbool_d(g_ht, "agent_resume", 0);
    it = ui_add(s, "Tự tiếp tục qua Agent (không vá launcher)", ar ? "Bật" : "Tắt", ar ? ST_GREEN : ST_UNKNOWN);
    ui_exp(it, "Cùng tác dụng nhưng KHÔNG thay file nào của Leaf: LoongMate Agent thấy game mở từ Games/Favorites thì");
    ui_exp(it, "nạp state mới nhất (ưu tiên state của bộ chuyển game) ngay khi RetroArch sẵn sàng, như tab Recents.");
    ui_exp(it, "Nên vẫn chạy sau khi cập nhật Leaf, không cần build lại. Chỉ game RetroArch; game chưa có state thì chạy mới.");
    ui_exp(it, "! Cần LoongMate Agent đang chạy (Settings > Services). Đang dùng bản Hot Tabs thì launcher tự làm, Agent bỏ qua.");
    ui_act(it, 0, ar ? "TẮT" : "BẬT", ar_toggle);

    ui_head(s, "Cài / gỡ");
    int can = jstr(g_ht, "installer_path") != NULL && matches;
    const char *why = can ? NULL : jstr(g_ht, "installer_path") ? "bộ cài không khớp phiên bản Leaf — cần build lại (docs/CAP-NHAT-LEAF.md)." : "không có hottabs/hottabs.sh trên thẻ.";
    int patched = !strcmp(in, "patched");
    it = ui_add(s, patched ? "Gỡ Hot Tabs" : "Cài lại Hot Tabs", "", ST_UNKNOWN);
    if (patched) ui_exp(it, "Trả jawaka-launcher về bản gốc từ bản sao lưu (kiểm md5). Xem trước rồi hỏi xác nhận (mặc định Huỷ).");
    else {
        ui_exp(it, "Thay jawaka-launcher bằng bản Hot Tabs (chỉ khi launcher hiện tại đúng bản gốc của Leaf; sao lưu trước, kiểm md5).");
        ui_exp(it, "Xem trước rồi hỏi xác nhận (mặc định Huỷ).");
    }
    ui_exp(it, "%s sau khi %s.", HT_RESTART, patched ? "gỡ" : "cài");
    if (why) ui_exp(it, "!! Không dùng được: %s", why);
    else ui_act(it, 0, patched ? "GỠ" : "CÀI", patched ? ht_uninstall : ht_install);
}

/* arg: "1" = mở màn, "0" = dựng lại màn đang mở */
static void status_done(lm_job *j, const char *arg) {
    cJSON *st = app_json_or_error(j, "Leaf Hot Tabs");
    if (!st) return;
    if (!jstr(st, "installed")) {      /* lỗi dùng/môi trường của CLI: {"success":false,"message":...} */
        ui_info1("Leaf Hot Tabs", "!! %s", jstrd(st, "message", "engine không trả trạng thái"));
        cJSON_Delete(st);
        return;
    }
    cJSON_Delete(g_ht);
    g_ht = st;
    if (!strcmp(arg, "1")) ui_push(ui_screen_new("Leaf Hot Tabs", build_hottabs)); else ui_rebuild_all();
}
void open_hottabs(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang đọc trạng thái Hot Tabs", SHORT_TIMEOUT_MS, status_done, "1", "hottabs", "status", NULL);
}
static void refresh_status(void) {
    cli_task("Đang đọc trạng thái Hot Tabs", SHORT_TIMEOUT_MS, status_done, "0", "hottabs", "status", NULL);
}

static void toggle_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Bật/tắt Hot Tabs");
    if (!r) return;
    if (!jbool_d(r, "success", 0)) ui_info1("Bật/tắt Hot Tabs", "!! %s", jstrd(r, "message", "không rõ nguyên nhân"));
    cJSON_Delete(r);
    refresh_status();
}
static void ht_toggle(ui_screen *s, ui_item *it) {
    (void)s; (void)it;     /* bật/tắt vô hại và đảo được: không cần hộp xác nhận */
    int on = jbool_d(g_ht, "resume_from_lists", 1);
    cli_task(on ? "Đang tắt tự tiếp tục" : "Đang bật tự tiếp tục", SHORT_TIMEOUT_MS, toggle_done, "", "hottabs", on ? "off" : "on", NULL);
}

static void ar_toggle(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    int ar = jbool_d(g_ht, "agent_resume", 0);
    cli_task(ar ? "Đang tắt" : "Đang bật", SHORT_TIMEOUT_MS, toggle_done, "", "autoresume", ar ? "off" : "on", NULL);
}

/* Thêm các dòng không rỗng của out; tail > 0 thì chỉ giữ tail dòng cuối. */
static void add_output(lines_t *L, const char *out, int tail, const char *prefix) {
    int n = 0;
    for (const char *p = out; *p; ) {
        const char *e = strchr(p, '\n');
        size_t l = e ? (size_t)(e - p) : strlen(p);
        if (l) n++;
        p = e ? e + 1 : p + l;
    }
    int skip = tail > 0 && n > tail ? n - tail : 0, i = 0;
    for (const char *p = out; *p; ) {
        const char *e = strchr(p, '\n');
        size_t l = e ? (size_t)(e - p) : strlen(p);
        if (l && i++ >= skip) lines_add(L, "%s%.*s", prefix, (int)l, p);
        p = e ? e + 1 : p + l;
    }
}

static void run_done(lm_job *j, const char *arg) {
    int un = !strcmp(arg, "uninstall");
    cJSON *r = app_json_or_error(j, "Kết quả Hot Tabs");
    if (!r) return;
    lines_t L = {0};
    const char *out = jstrd(r, "output", "");
    if (jbool_d(r, "success", 0)) {
        lines_add(&L, "+ Đã %s Hot Tabs.", un ? "gỡ" : "cài");
        lines_add(&L, "%s", "");
        lines_add(&L, "!! %s.", HT_RESTART);
        lines_add(&L, "%s", "");
        add_output(&L, out, 6, "");
    } else {
        lines_add(&L, "!! %s thất bại (bộ cài tự trả về như trước nếu đã ghi dở):", un ? "Gỡ" : "Cài");
        if (!out[0]) lines_add(&L, "!! %s", jstrd(r, "message", "không rõ nguyên nhân"));
        add_output(&L, out, 8, "!! ");
    }
    cJSON_Delete(r);
    ui_info(un ? "Kết quả gỡ Hot Tabs" : "Kết quả cài Hot Tabs", &L);
    refresh_status();
}
static void do_run(const char *arg) {     /* --yes chỉ sau khi người dùng xác nhận trong hộp thoại */
    int un = !strcmp(arg, "uninstall");
    cli_task(un ? "Đang gỡ Hot Tabs" : "Đang cài Hot Tabs", LONG_TIMEOUT_MS, run_done, un ? "uninstall" : "install",
             "hottabs", un ? "uninstall" : "install", "--yes", NULL);
}

static void preview_done(lm_job *j, const char *arg) {
    int un = !strcmp(arg, "uninstall");
    cJSON *r = app_json_or_error(j, "Xem trước Hot Tabs");
    if (!r) return;
    lines_t L = {0};
    const char *out = jstrd(r, "output", "");
    if (jbool_d(r, "preview", 0) && jbool_d(r, "installer_ok", 0)) {
        lines_add(&L, "Bản xem trước (chưa ghi gì):");
        lines_add(&L, "%s", "");
        add_output(&L, out, 0, "");
        lines_add(&L, "%s", "");
        lines_add(&L, "!! %s sau khi %s.", HT_RESTART, un ? "gỡ" : "cài");
        cJSON_Delete(r);
        ui_confirm(un ? "Xác nhận gỡ Hot Tabs" : "Xác nhận cài Hot Tabs", &L, un ? "Gỡ" : "Cài", do_run, un ? "uninstall" : "install");
        return;
    }
    lines_add(&L, "!! Không đổi gì.");
    lines_add(&L, "%s", "");
    if (out[0]) add_output(&L, out, 8, "!! "); else lines_add(&L, "!! %s", jstrd(r, "message", "không rõ nguyên nhân"));
    cJSON_Delete(r);
    ui_info("Bộ cài từ chối", &L);
}
static void ht_install(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang xem trước", SHORT_TIMEOUT_MS, preview_done, "install", "hottabs", "install", NULL);
}
static void ht_uninstall(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang xem trước", SHORT_TIMEOUT_MS, preview_done, "uninstall", "hottabs", "uninstall", NULL);
}
