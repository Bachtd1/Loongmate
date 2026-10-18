/* LoongMate native — Cấu hình Leaf/RetroArch (cli.py leafconfig list|snapshot|diff|restore --json).
 * Native không đọc file cấu hình: engine/leafconfig.py làm hết. Khôi phục luôn qua hộp xác nhận (mặc định Huỷ), chọn nhóm trước. */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <stdio.h>
#include <string.h>

static cJSON *g_lc;     /* `leafconfig list` mới nhất */

static const char *group_vi(const char *g) {
    if (!strcmp(g, "retroarch")) return "RetroArch chung";
    if (!strcmp(g, "overrides")) return tr("Override và remap");
    if (!strcmp(g, "standalone")) return tr("Emulator độc lập");
    if (!strcmp(g, "leaf_db")) return tr("Cơ sở dữ liệu Leaf");
    return g;
}

static void lc_snapshot(ui_screen *s, ui_item *it);
static void lc_diff(ui_screen *s, ui_item *it);
static void lc_restore_pick(ui_screen *s, ui_item *it);

static void build_leafcfg(ui_screen *s) {
    cJSON *snaps = jarr(g_lc, "snapshots"), *x;
    cJSON *upd = jget(g_lc, "leaf_updated");
    ui_hdr(s, tr("Leaf hiện tại: %s · %d bản sao lưu cấu hình"), jstrd(g_lc, "current_leaf_version", "?"), jarr_n(g_lc, "snapshots"));
    ui_hdr(s, tr("Giữ cấu hình bạn đã chỉnh (phím, shader, core option, remap...). Save/state do SaveVault giữ."));

    ui_item *it;
    if (upd && jstr(upd, "before_id")) {
        it = ui_add(s, tr("Leaf vừa cập nhật"), "", ST_YELLOW);
        ui_exp(it, tr("Leaf %s -> %s. Bản chụp trước: %s; sau: %s."), jstrd(upd, "from", "?"), jstrd(upd, "to", "?"),
               jstrd(upd, "before_id", "?"), jstrd(upd, "after_id", "?"));
        ui_exp(it, tr("A — xem cấu hình nào đã đổi so với trước khi cập nhật."));
        ui_arg(it, jstr(upd, "before_id"));
        ui_act(it, 0, tr("SO SÁNH"), lc_diff);
    }
    it = ui_add(s, tr("Sao lưu ngay"), "", ST_UNKNOWN);
    ui_exp(it, tr("Lưu retroarch.cfg, override/core option/remap theo core-game, cấu hình Mupen64Plus và cơ sở dữ liệu Leaf."));
    ui_exp(it, tr("Không lưu wifi.conf (có mật khẩu Wi-Fi). Không đổi gì so với bản gần nhất thì không tạo bản mới."));
    ui_exp(it, tr("LoongMate Agent tự sao lưu mỗi ngày, và chụp thêm một bản ngay khi phát hiện Leaf đổi phiên bản."));
    ui_act(it, 0, tr("SAO LƯU"), lc_snapshot);

    ui_head(s, tr("Bản sao lưu (mới nhất trước)"));
    if (!jarr_n(g_lc, "snapshots")) {
        it = ui_add(s, tr("Chưa có bản nào"), "", ST_UNKNOWN);
        ui_exp(it, tr("Chọn Sao lưu ngay, hoặc chờ Agent tự sao lưu."));
        return;
    }
    cJSON_ArrayForEach(x, snaps) {
        char lbl[160], tag[48];
        snprintf(lbl, sizeof lbl, "%s", jstrd(x, "label", "")[0] ? jstrd(x, "label", "") : jstrd(x, "id", "?"));
        snprintf(tag, sizeof tag, "Leaf %s", jstrd(x, "leaf_version", "?"));
        it = ui_add(s, lbl, tag, ST_UNKNOWN);
        if (!it) continue;
        ui_exp(it, tr("Bản %s · %d file · %s"), jstrd(x, "id", "?"), (int)jnumd(x, "files", 0), jstrd(x, "created_at", ""));
        ui_exp(it, tr("A — so với cấu hình hiện tại (khoá nào đổi, giá trị cũ -> mới)."));
        ui_exp(it, tr("X — khôi phục (chọn nhóm; tự sao lưu hiện trạng trước; không xoá file nào)."));
        ui_arg(it, jstr(x, "id"));
        ui_act(it, 0, tr("SO SÁNH"), lc_diff);
        ui_act(it, 1, tr("KHÔI PHỤC"), lc_restore_pick);
    }
}

/* arg: "1" = mở màn, "0" = dựng lại màn đang mở */
static void list_done(lm_job *j, const char *arg) {
    cJSON *r = app_json_or_error(j, tr("Cấu hình Leaf"));
    if (!r) return;
    if (!jbool_d(r, "success", 0)) {
        ui_info1(tr("Cấu hình Leaf"), "!! %s", jstrd(r, "message", tr("engine không trả danh sách")));
        cJSON_Delete(r);
        return;
    }
    cJSON_Delete(g_lc);
    g_lc = r;
    if (!strcmp(arg, "1")) ui_push(ui_screen_new("CẤU HÌNH LEAF / RETROARCH", build_leafcfg)); else ui_rebuild_all();
}
void open_leafcfg(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc bản sao lưu cấu hình"), SHORT_TIMEOUT_MS, list_done, "1", "leafconfig", "list", NULL);
}
static void refresh(void) {
    cli_task(tr("Đang đọc bản sao lưu cấu hình"), SHORT_TIMEOUT_MS, list_done, "0", "leafconfig", "list", NULL);
}

static void result_done(lm_job *j, const char *arg) {
    cJSON *r = app_json_or_error(j, arg);
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "%s%s", jbool_d(r, "success", 0) ? "+ " : "!! ", jstrd(r, "message", tr("không rõ kết quả")));
    cJSON *x;
    cJSON_ArrayForEach(x, jarr(r, "restored")) lines_add(&L, "  %s", x->valuestring ? x->valuestring : "?");
    cJSON_Delete(r);
    ui_info(arg, &L);
    refresh();
}
static void lc_snapshot(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang sao lưu cấu hình"), LONG_TIMEOUT_MS, result_done, tr("Sao lưu cấu hình"), "leafconfig", "snapshot", "--label", tr("thủ công"), NULL);
}

static void diff_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("So sánh cấu hình"));
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "%s%s", jbool_d(r, "success", 1) ? "" : "!! ", jstrd(r, "message", ""));
    lines_add(&L, tr("Leaf lúc sao lưu: %s · hiện tại: %s"), jstrd(r, "snapshot_leaf_version", "?"), jstrd(r, "current_leaf_version", "?"));
    cJSON *c, *k;
    cJSON_ArrayForEach(c, jarr(r, "changed")) {
        lines_add(&L, "%s", "");
        lines_add(&L, "! %s  [%s]", jstrd(c, "path", "?"), group_vi(jstrd(c, "group", "")));
        cJSON_ArrayForEach(k, jarr(c, "keys"))
            lines_add(&L, "   %s: %s -> %s", jstrd(k, "key", "?"), jstrd(k, "old", tr("(không có)")), jstrd(k, "new", tr("(không có)")));
        if (jnumd(c, "more_keys", 0) > 0) lines_add(&L, tr("   ... và %d khoá khác"), (int)jnumd(c, "more_keys", 0));
    }
    cJSON_ArrayForEach(c, jarr(r, "added")) lines_add(&L, tr("+ mới: %s"), jstrd(c, "path", "?"));
    cJSON_ArrayForEach(c, jarr(r, "removed")) lines_add(&L, tr("!! không còn: %s"), jstrd(c, "path", "?"));
    cJSON_Delete(r);
    ui_info(tr("So với cấu hình hiện tại"), &L);
}
static void lc_diff(ui_screen *s, ui_item *it) {
    (void)s;
    cli_task(tr("Đang so sánh"), LONG_TIMEOUT_MS, diff_done, "", "leafconfig", "diff", it->arg, NULL);
}

/* ---- khôi phục: chọn nhóm -> xác nhận (arg "<id>|<nhóm|all>") -> chạy */
static void do_restore(const char *arg) {
    char id[64], grp[32];
    const char *bar = strchr(arg, '|');
    if (!bar) return;
    snprintf(id, sizeof id, "%.*s", (int)(bar - arg), arg);
    snprintf(grp, sizeof grp, "%s", bar + 1);
    if (!strcmp(grp, "all"))
        cli_task(tr("Đang khôi phục cấu hình"), LONG_TIMEOUT_MS, result_done, tr("Khôi phục cấu hình"), "leafconfig", "restore", id,
                 "--group", "retroarch", "--group", "overrides", "--group", "standalone", "--yes", NULL);
    else
        cli_task(tr("Đang khôi phục cấu hình"), LONG_TIMEOUT_MS, result_done, tr("Khôi phục cấu hình"), "leafconfig", "restore", id,
                 "--group", grp, "--yes", NULL);
}
static void ask_restore(ui_screen *s, ui_item *it) {
    char arg[128];
    snprintf(arg, sizeof arg, "%s|%s", s->key, it->arg);
    lines_t L = {0};
    lines_add(&L, tr("Ghi lại từ bản %s: %s."), s->key, strcmp(it->arg, "all") ? group_vi(it->arg) : tr("mọi nhóm khôi phục được"));
    lines_add(&L, "%s", "");
    lines_add(&L, tr("+ Cấu hình hiện tại được sao lưu tự động trước (quay lại được)."));
    lines_add(&L, tr("+ Không xoá file nào; cơ sở dữ liệu Leaf không bị đụng."));
    lines_add(&L, tr("! Có hiệu lực từ lần mở game kế tiếp. Từ chối nếu đang có game chạy."));
    ui_confirm(tr("Khôi phục cấu hình"), &L, tr("Khôi phục"), do_restore, arg);
}
static void build_pick(ui_screen *s) {
    ui_hdr(s, tr("Khôi phục từ bản %s: chọn nhóm cấu hình."), s->key);
    static const char *const groups[] = { "retroarch", "overrides", "standalone", "all" };
    for (int i = 0; i < 4; i++) {
        ui_item *it = ui_add(s, i < 3 ? group_vi(groups[i]) : tr("Tất cả các nhóm trên"), "", ST_UNKNOWN);
        if (!it) continue;
        if (i == 1) ui_exp(it, tr("Override theo core/game (.cfg), core option (.opt), remap phím (.rmp) trong .umrk/<máy>/retroarch/.config/retroarch/config."));
        if (i == 0) ui_exp(it, tr("File cấu hình RetroArch chung của Leaf (.umrk/<máy>/retroarch/retroarch.cfg). Leaf tự giữ ~70 khoá hệ thống."));
        if (i == 2) ui_exp(it, tr("Cấu hình Mupen64Plus trong .userdata/<máy>/mupen64plus/config."));
        ui_exp(it, tr("Cơ sở dữ liệu Leaf không khôi phục ở đây (Leaf luôn mở nó khi LoongMate chạy)."));
        ui_arg(it, groups[i]);
        ui_act(it, 0, tr("CHỌN"), ask_restore);
    }
}
static void lc_restore_pick(ui_screen *s, ui_item *it) {
    (void)s;
    ui_screen *p = ui_screen_new("KHÔI PHỤC CẤU HÌNH", build_pick);
    if (!p) return;
    snprintf(p->key, sizeof p->key, "%s", it->arg);
    ui_push(p);
}
