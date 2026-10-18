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
    int patched = !strcmp(in, "patched");
    int ar = jbool_d(g_ht, "agent_resume", 0), agent = jbool_d(g_ht, "agent_running", 0);
    cJSON *compat = jget(g_ht, "agent_compat"), *last = jget(g_ht, "agent_last");
    int cok = 1, cknown = jbool(compat, "ok", &cok);      /* ok null = chưa đủ dữ liệu */
    int broken = cknown && !cok;
    /* Hot Tabs chỉ hiện khi có trên máy/thẻ, hoặc khi Leaf đổi cấu trúc khiến cách qua Agent không chạy được. */
    int hot = patched || bver || jstr(g_ht, "installer_path") || broken;

    if (patched) ui_hdr(s, "Đang dùng Hot Tabs: launcher tự tiếp tục, Agent bỏ qua.");
    else if (ar && broken) ui_hdr(s, "!! Bản Leaf này đã đổi cấu trúc: tự tiếp tục qua Agent không chạy được. Cập nhật LoongMate hoặc dùng Hot Tabs.");
    else if (ar && agent) ui_hdr(s, "Đang BẬT: mở game từ Games/Favorites sẽ vào chỗ đang chơi dở.");
    else if (ar) ui_hdr(s, "! Đã bật nhưng LoongMate Agent chưa chạy: Settings > Services > LoongMate Agent > Run.");
    else ui_hdr(s, "Đang TẮT: mở game từ Games/Favorites luôn chạy mới (như Leaf gốc).");
    ui_hdr(s, "Tab Recents của Leaf vốn đã tự tiếp tục; màn này thêm cho Games/Favorites.");

    ui_head(s, "Tự tiếp tục");
    ui_item *it = ui_add(s, "Qua Agent", ar ? "Bật" : "Tắt", ar ? (agent ? ST_GREEN : ST_YELLOW) : ST_UNKNOWN);
    ui_exp(it, "Khuyên dùng. Không thay file nào của Leaf: LoongMate Agent thấy game mở từ Games/Favorites thì nạp state mới nhất");
    ui_exp(it, "(ưu tiên state của bộ chuyển game) ngay khi RetroArch sẵn sàng, như tab Recents.");
    ui_exp(it, "Vẫn chạy sau khi cập nhật Leaf, không cần làm gì thêm. Chỉ game RetroArch; game chưa có state thì chạy mới.");
    ui_exp(it, "LoongMate Agent: %s", agent ? "đang chạy." : "CHƯA CHẠY — bật ở Settings > Services > LoongMate Agent (Start with Leaf + Run).");
    if (patched) ui_exp(it, "Đang dùng Hot Tabs nên launcher tự làm, Agent bỏ qua.");
    ui_act(it, 0, ar ? "TẮT" : "BẬT", ar_toggle);

    it = ui_add(s, "Kiểm với Leaf này", broken ? "lệch" : cknown ? "tương thích" : "chưa rõ",
                broken ? ST_RED : cknown ? ST_GREEN : ST_UNKNOWN);
    ui_exp(it, "%s", jstrd(compat, "message", "Không đọc được trạng thái."));
    ui_exp(it, "LoongMate đọc log của Leaf và socket jawakad; sau mỗi lần cập nhật Leaf, mục này cho biết cách qua Agent còn chạy không.");
    if (broken) ui_exp(it, "Chỉ khi lệch như vậy mới cần Hot Tabs (vá launcher, cần bản dựng đúng phiên bản Leaf) hoặc chờ bản LoongMate mới.");
    if (cJSON_IsObject(last)) {
        const char *o = jstrd(last, "outcome", "");
        const char *what = !strcmp(o, "loaded") ? "đã nạp lại chỗ đang chơi" : !strcmp(o, "no_state") ? "game chưa có state, chạy mới"
                         : !strcmp(o, "other_game") ? "RetroArch báo game khác, bỏ qua" : !strcmp(o, "timeout") ? "RetroArch chưa sẵn sàng kịp, chạy mới"
                         : !strcmp(o, "not_started") ? "game không chạy, bỏ qua" : o;
        const char *rom = jstrd(last, "rom", "");
        const char *base = strrchr(rom, '/');
        ui_exp(it, "Lần gần nhất (%s): %s — %s", jstrd(last, "at", "?"), base ? base + 1 : rom, what);
    } else ui_exp(it, "Chưa có lần tự tiếp tục nào được ghi lại.");
    if (!hot) return;

    const char *line, *tag;
    int tc;
    if (!matches && bver) { line = "Không khớp phiên bản Leaf — cần build lại bộ cài"; tag = "không khớp"; tc = ST_RED; }
    else if (patched) { line = "Đang dùng bản Hot Tabs"; tag = "Hot Tabs"; tc = ST_GREEN; }
    else if (!strcmp(in, "stock")) { line = "Bản gốc của Leaf (chưa cài Hot Tabs)"; tag = "bản gốc"; tc = ST_YELLOW; }
    else if (!strcmp(in, "absent")) { line = "Không thấy launcher của Leaf trên thẻ"; tag = "không có"; tc = ST_RED; }
    else { line = "Không nhận diện được launcher (md5 lạ)"; tag = "không rõ"; tc = ST_YELLOW; }

    ui_head(s, "Hot Tabs (nâng cao, thay launcher)");
    it = ui_add(s, "Launcher", tag, tc);
    ui_exp(it, "%s", line);
    ui_exp(it, "%s", jstrd(g_ht, "message", ""));
    ui_exp(it, "Phiên bản Leaf: %s  (commit launcher %s)", jstrd(g_ht, "leaf_version", "?"), jstrd(g_ht, "launcher_commit", "?"));
    ui_exp(it, "Bộ cài trên thẻ: %s%s", bver ? bver : "không có", matches ? " — khớp Leaf này." : " — KHÔNG khớp Leaf này.");
    ui_exp(it, "Bản gốc đã sao lưu: %s", jbool_d(g_ht, "backup_present", 0) ? "có" : "không");
    ui_exp(it, "Cập nhật Leaf thay cả thư mục launcher nên Hot Tabs biến mất (máy về bản gốc, vô hại). Cài lại theo docs/CAP-NHAT-LEAF.md.");

    if (patched) {
        it = ui_add(s, "Qua Hot Tabs", on ? "Bật" : "Tắt", on ? ST_GREEN : ST_YELLOW);
        ui_exp(it, "Bật: mở game từ Games/Favorites thì vào thẳng chỗ đang chơi dở như Recents (game chưa có state thì chạy mới).");
        ui_exp(it, "Tắt: Games/Favorites luôn chạy mới như Leaf gốc.");
        ui_exp(it, "A đổi ngay; launcher kiểm mỗi lần mở game nên không cần khởi động lại.");
        ui_exp(it, "Ghi một file cờ nhỏ trên thẻ; bật = xoá file cờ.");
        ui_act(it, 0, on ? "TẮT" : "BẬT", ht_toggle);
    }

    int can = jstr(g_ht, "installer_path") != NULL && matches;
    const char *why = can ? NULL : jstr(g_ht, "installer_path") ? "bộ cài không khớp phiên bản Leaf — cần build lại (docs/CAP-NHAT-LEAF.md)." : "không có hottabs/hottabs.sh trên thẻ.";
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
    cJSON *st = app_json_or_error(j, "Tự tiếp tục game");
    if (!st) return;
    if (!jstr(st, "installed")) {      /* lỗi dùng/môi trường của CLI: {"success":false,"message":...} */
        ui_info1("Tự tiếp tục game", "!! %s", jstrd(st, "message", "engine không trả trạng thái"));
        cJSON_Delete(st);
        return;
    }
    cJSON_Delete(g_ht);
    g_ht = st;
    if (!strcmp(arg, "1")) ui_push(ui_screen_new("Tự tiếp tục game", build_hottabs)); else ui_rebuild_all();
}
void open_hottabs(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang đọc trạng thái", SHORT_TIMEOUT_MS, status_done, "1", "hottabs", "status", NULL);
}
static void refresh_status(void) {
    cli_task("Đang đọc trạng thái", SHORT_TIMEOUT_MS, status_done, "0", "hottabs", "status", NULL);
}

static void toggle_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Bật/tắt tự tiếp tục");
    if (!r) return;
    if (!jbool_d(r, "success", 0)) ui_info1("Bật/tắt tự tiếp tục", "!! %s", jstrd(r, "message", "không rõ nguyên nhân"));
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
