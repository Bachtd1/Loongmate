/* LoongMate native — Khoá khi thức dậy (cli.py pocketlock status|on|off --json).
 * Native không tự giữ tay cầm: LoongMate Agent làm (engine/pocketlock.py). Màn này chỉ bật/tắt và báo Agent có chạy không. */
#define _GNU_SOURCE
#include "lm_app.h"
#include <stdio.h>
#include <string.h>

static cJSON *g_pl;     /* `pocketlock status` mới nhất */

static void pl_toggle(ui_screen *s, ui_item *it);

static void build_lock(ui_screen *s) {
    int on = jbool_d(g_pl, "enabled", 0), agent = jbool_d(g_pl, "agent_running", 0);
    if (on && agent) ui_hdr(s, "Đang BẬT: mỗi lần máy thức, phím bị khoá tới khi giữ Select + Start 1 giây.");
    else if (on) ui_hdr(s, "! Đã bật nhưng LoongMate Agent chưa chạy: Settings > Services > LoongMate Agent > Run.");
    else ui_hdr(s, "Đang TẮT: máy thức là dùng được ngay (như Leaf gốc).");
    ui_hdr(s, "Chống bấm nhầm khi để máy trong túi.");

    ui_head(s, "Khoá");
    ui_item *it = ui_add(s, "Khoá khi thức dậy", on ? "Bật" : "Tắt", on ? (agent ? ST_GREEN : ST_YELLOW) : ST_UNKNOWN);
    ui_exp(it, "Để trong túi, nút nguồn bị cấn là máy thức, rồi các nút khác bị đè làm máy lướt menu, mở game.");
    ui_exp(it, "Bật: mỗi lần máy thức sau khi ngủ, mọi phím bị chặn (màn vẫn sáng, không có chữ báo khoá).");
    ui_exp(it, "Mở khoá: giữ Select + Start 1 giây. Không mở trong 10 giây thì máy tự ngủ lại.");
    ui_exp(it, "Nút nguồn vẫn dùng bình thường: bấm để ngủ, giữ 2 giây để tắt máy.");
    ui_exp(it, "Không sửa file nào của Leaf. Agent dừng hay lỗi thì phím tự được trả lại.");
    ui_exp(it, "Đang sạc: Leaf chỉ tắt màn chứ không ngủ; bấm nút bật màn cũng bị khoá như trên.");
    ui_exp(it, "LoongMate Agent: %s", agent ? "đang chạy." : "CHƯA CHẠY — bật ở Settings > Services > LoongMate Agent (Start with Leaf + Run).");
    ui_act(it, 0, on ? "TẮT" : "BẬT", pl_toggle);
}

/* arg: "1" = mở màn, "0" = dựng lại màn đang mở */
static void status_done(lm_job *j, const char *arg) {
    cJSON *st = app_json_or_error(j, "Khoá khi thức dậy");
    if (!st) return;
    if (!jbool_d(st, "success", 0)) {
        ui_info1("Khoá khi thức dậy", "!! %s", jstrd(st, "message", "engine không trả trạng thái"));
        cJSON_Delete(st);
        return;
    }
    cJSON_Delete(g_pl);
    g_pl = st;
    if (!strcmp(arg, "1")) ui_push(ui_screen_new("Khoá khi thức dậy", build_lock)); else ui_rebuild_all();
}
void open_pocketlock(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang đọc trạng thái", SHORT_TIMEOUT_MS, status_done, "1", "pocketlock", "status", NULL);
}

static void toggle_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Bật/tắt khoá");
    if (!r) return;
    if (!jbool_d(r, "success", 0)) ui_info1("Bật/tắt khoá", "!! %s", jstrd(r, "message", "không rõ nguyên nhân"));
    cJSON_Delete(r);
    cli_task("Đang đọc trạng thái", SHORT_TIMEOUT_MS, status_done, "0", "pocketlock", "status", NULL);
}
static void pl_toggle(ui_screen *s, ui_item *it) {
    (void)s; (void)it;     /* bật/tắt vô hại và đảo được: không cần hộp xác nhận */
    int on = jbool_d(g_pl, "enabled", 0);
    cli_task(on ? "Đang tắt" : "Đang bật", SHORT_TIMEOUT_MS, toggle_done, "", "pocketlock", on ? "off" : "on", NULL);
}
