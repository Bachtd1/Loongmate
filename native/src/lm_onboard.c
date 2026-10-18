/* LoongMate native — Bắt đầu (lần đầu mở app): mỗi bước có trạng thái thật (cli.py onboarding status --json) và làm
 * ngay tại chỗ. Bấm Xong hoặc B thì `onboarding done`, không tự hiện nữa; mở lại ở Máy > Hướng dẫn bắt đầu. */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <stdio.h>
#include <string.h>

static cJSON *g_ob;     /* `onboarding status` mới nhất */

static void refresh(void);
static void st_toggle_ar(ui_screen *s, ui_item *it);
static void st_toggle_pl(ui_screen *s, ui_item *it);
static void st_recheck(ui_screen *s, ui_item *it);
static void st_backup(ui_screen *s, ui_item *it);
static void st_scan(ui_screen *s, ui_item *it);
static void st_finish(ui_screen *s, ui_item *it);

static void build_onboard(ui_screen *s) {
    int agent = jbool_d(g_ob, "agent_running", 0), ar = jbool_d(g_ob, "autoresume", 0), pl = jbool_d(g_ob, "pocketlock", 0);
    int scanned = jbool_d(g_ob, "library_scanned", 0);
    double snaps = jnumd(g_ob, "snapshots", 0);
    int left = !agent + !(snaps > 0) + !scanned;
    if (left) ui_hdr(s, tr("Còn %d bước nên làm để LoongMate bảo vệ máy. Chọn từng bước, bấm A."), left);
    else ui_hdr(s, tr("+ Xong các bước chính. Bấm \"Vào LoongMate\" (hoặc B)."));
    ui_hdr(s, tr("Mở lại màn này: Máy > Hướng dẫn bắt đầu."));

    add_lang_item(s);
    ui_head(s, tr("Nên làm"));
    char tag[32];
    ui_item *it = ui_add(s, tr("1. Bật Agent"), agent ? tr("Đang chạy") : tr("Chưa chạy"), agent ? ST_GREEN : ST_YELLOW);
    ui_exp(it, tr("Agent chạy nền: tự sao lưu save sau mỗi lần thoát game, tự tiếp tục, khoá khi thức dậy, lịch sử pin."));
    ui_exp(it, tr("Cách bật (một lần): B thoát LoongMate > Settings > Services > LoongMate Agent:"));
    ui_exp(it, tr("bật \"Start with Leaf\" rồi \"Run\". Quay lại LoongMate, chọn dòng này bấm A để kiểm tra lại."));
    if (!jbool_d(g_ob, "agent_installed", 1)) ui_exp(it, tr("! Không thấy Apps/mlp1/LoongMateAgent.pak trên thẻ: chép lại thư mục THE-NHO của gói cài."));
    ui_act(it, 0, tr("KIỂM TRA"), st_recheck);

    if (snaps > 0) snprintf(tag, sizeof tag, "%.0f snapshot", snaps); else snprintf(tag, sizeof tag, tr("chưa có"));
    it = ui_add(s, tr("2. Sao lưu lần đầu"), tag, snaps > 0 ? ST_GREEN : ST_YELLOW);
    ui_exp(it, tr("Chụp toàn bộ save/state của hai thẻ vào SaveVault (chỉ chép, không sửa gì). Lần sau Agent tự làm."));
    ui_exp(it, tr("Có thể mất vài chục giây lần đầu."));
    ui_act(it, 0, tr("SAO LƯU"), st_backup);

    it = ui_add(s, tr("3. Quét thư viện"), scanned ? tr("đã quét") : tr("chưa quét"), scanned ? ST_GREEN : ST_YELLOW);
    ui_exp(it, tr("Tìm game Leaf không hiện, ảnh bìa lệch tên, .cue hỏng, BIOS thiếu, ROM trùng (~15 giây). Chỉ đọc."));
    ui_exp(it, tr("Xem và sửa sau ở Library > Danh sách vấn đề."));
    ui_act(it, 0, tr("QUÉT"), st_scan);

    ui_head(s, tr("Tuỳ chọn (cần Agent)"));
    it = ui_add(s, tr("Tự tiếp tục game"), ar ? tr("Bật") : tr("Tắt"), ar ? ST_GREEN : ST_UNKNOWN);
    ui_exp(it, tr("Mở game từ Games/Favorites thì vào thẳng chỗ đang chơi dở như tab Recents. Không sửa file nào của Leaf."));
    ui_act(it, 0, ar ? tr("TẮT") : tr("BẬT"), st_toggle_ar);
    it = ui_add(s, tr("Khoá khi thức dậy"), pl ? tr("Bật") : tr("Tắt"), pl ? ST_GREEN : ST_UNKNOWN);
    ui_exp(it, tr("Để máy trong túi không tự lướt menu, mở game: máy thức thì khoá phím tới khi giữ Select + Start 1 giây."));
    ui_act(it, 0, pl ? tr("TẮT") : tr("BẬT"), st_toggle_pl);

    ui_head(s, "Xong");
    it = ui_add(s, tr("Vào LoongMate"), "", ST_UNKNOWN);
    ui_exp(it, tr("Đóng màn này; không tự hiện lại. Mở lại ở Máy > Hướng dẫn bắt đầu."));
    ui_act(it, 0, tr("VÀO"), st_finish);
}

static int onboard_key(ui_screen *s, cat_input_event *ev) {
    (void)s;
    if (ev->button == CAT_BTN_B && !ev->repeated) {    /* B = để sau: cũng coi là xong, không làm phiền lần sau */
        st_finish(NULL, NULL);
        return 1;
    }
    return 0;
}

/* arg: "1" = mở màn, "0" = dựng lại màn đang mở, "auto" = chỉ mở khi chưa xong (lúc khởi động) */
static void status_done(lm_job *j, const char *arg) {
    cJSON *st = app_json_or_error(j, tr("Bắt đầu"));
    if (!st) return;
    if (!jbool_d(st, "success", 0)) {
        if (strcmp(arg, "auto")) ui_info1(tr("Bắt đầu"), "!! %s", jstrd(st, "message", tr("engine không trả trạng thái")));
        cJSON_Delete(st);
        return;
    }
    if (!strcmp(arg, "auto") && jbool_d(st, "done", 0)) { cJSON_Delete(st); return; }
    cJSON_Delete(g_ob);
    g_ob = st;
    if (strcmp(arg, "0")) {
        ui_screen *sc = ui_screen_new("Bắt đầu", build_onboard);
        sc->key_fn = onboard_key;
        ui_push(sc);
    } else ui_rebuild_all();
}
void onboard_maybe(void) {
    cli_task(tr("Đang đọc trạng thái"), SHORT_TIMEOUT_MS, status_done, "auto", "onboarding", "status", NULL);
}
void open_onboarding(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc trạng thái"), SHORT_TIMEOUT_MS, status_done, "1", "onboarding", "status", NULL);
}
static void refresh(void) {
    cli_task(tr("Đang đọc trạng thái"), SHORT_TIMEOUT_MS, status_done, "0", "onboarding", "status", NULL);
}

static void simple_done(lm_job *j, const char *title) {
    cJSON *r = app_json_or_error(j, title);
    if (!r) return;
    if (!jbool_d(r, "success", 0)) ui_info1(title, "!! %s", jstrd(r, "message", tr("không rõ nguyên nhân")));
    cJSON_Delete(r);
    refresh();
}
static void toggle_done(lm_job *j, const char *arg) { (void)arg; simple_done(j, tr("Bật/tắt")); }
static void st_toggle_ar(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    int on = jbool_d(g_ob, "autoresume", 0);
    cli_task(on ? tr("Đang tắt") : tr("Đang bật"), SHORT_TIMEOUT_MS, toggle_done, "", "autoresume", on ? "off" : "on", NULL);
}
static void st_toggle_pl(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    int on = jbool_d(g_ob, "pocketlock", 0);
    cli_task(on ? tr("Đang tắt") : tr("Đang bật"), SHORT_TIMEOUT_MS, toggle_done, "", "pocketlock", on ? "off" : "on", NULL);
}
static void st_recheck(ui_screen *s, ui_item *it) { (void)s; (void)it; refresh(); }

static void backup_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("Sao lưu"));
    if (!r) return;
    if (jbool_d(r, "success", 0)) ui_info1(tr("Sao lưu"), tr("+ Đã sao lưu: %s"), jstrd(r, "snapshot_id", "?"));
    else ui_info1(tr("Sao lưu"), "!! %s", jstrd(r, "message", tr("không rõ nguyên nhân")));
    cJSON_Delete(r);
    app_refresh();
    refresh();
}
static void st_backup(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang sao lưu"), LONG_TIMEOUT_MS, backup_done, "", "backup", NULL);
}
static void scan_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("Quét thư viện"));
    if (!r) return;
    cJSON *sev = jget(r, "severity_counts");
    if (jget(r, "total_issues"))
        ui_info1(tr("Quét thư viện"), tr("+ Xong: %.0f vấn đề (nghiêm trọng %.0f, cảnh báo %.0f). Xem ở Library > Danh sách vấn đề."),
                 jnumd(r, "total_issues", 0), jnumd(sev, "critical", 0), jnumd(sev, "warning", 0));
    else ui_info1(tr("Quét thư viện"), "!! %s", jstrd(r, "message", tr("không rõ nguyên nhân")));
    cJSON_Delete(r);
    app_refresh();
    refresh();
}
static void st_scan(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang quét thư viện"), LONG_TIMEOUT_MS, scan_done, "", "scan", NULL);
}

static void finish_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("Bắt đầu"));
    cJSON_Delete(r);
    ui_pop();
}
static void st_finish(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang lưu"), SHORT_TIMEOUT_MS, finish_done, "", "onboarding", "done", NULL);
}
