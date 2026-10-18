/* LoongMate native — sức khoẻ pin (cli.py battery --json) và cập nhật LoongMate (cli.py update status|list|verify|apply|rollback).
 * Gói cập nhật được liệt kê bằng `update list` và chọn theo đường dẫn; native chỉ dùng đúng hình dạng JSON mà CLI đang trả. */
#define _GNU_SOURCE
#include "lm_app.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AGENT_HINT "Bật service LoongMate Agent trong màn Services của Leaf: Enable rồi Start."
#define RESTART_NOTE "Thoát và mở lại LoongMate để dùng bản mới"
#define RESTART_NOTE_ROLLBACK "Thoát và mở lại LoongMate để dùng bản đã khôi phục"

static void fmt_num(char *buf, size_t n, const cJSON *v, const char *unit) {
    if (!v || cJSON_IsNull(v)) { snprintf(buf, n, "không có"); return; }
    char t[64];
    if (cJSON_IsNumber(v)) snprintf(t, sizeof t, "%g", v->valuedouble); else app_scalar(v, t, sizeof t);
    snprintf(buf, n, "%s%s", t, unit);
}

static void join_strings(char *buf, size_t n, const cJSON *arr) {
    buf[0] = 0;
    cJSON *p;
    cJSON_ArrayForEach(p, arr) {
        if (!cJSON_IsString(p)) continue;
        size_t l = strlen(buf);
        snprintf(buf + l, n - l, "%s%s", l ? ", " : "", p->valuestring);
    }
}

/* ============================================================ sức khoẻ pin */
static void build_battery(ui_screen *s) {
    cJSON *bh = s->d;
    char b[96];
    ui_hdr(s, "ƯỚC LƯỢNG từ mẫu telemetry (90 ngày gần nhất), không phải số đo chính hãng.");
    fmt_num(b, sizeof b, jget(bh, "samples"), "");
    ui_hdr(s, "Số mẫu dùng: %s", b);
    if (!jbool_d(bh, "available", 0)) {
        ui_item *it = ui_add(s, "Chưa tính được", "", ST_UNKNOWN);
        ui_exp(it, "!! %s", jstrd(bh, "reason", "không rõ lý do"));
        ui_exp(it, AGENT_HINT);
        ui_exp(it, "LoongMate không đoán số khi thiếu dữ liệu.");
        return;
    }
    cJSON *pctv = jget(bh, "estimated_pct");
    double pct = cJSON_IsNumber(pctv) ? pctv->valuedouble : -1;
    ui_item *it = ui_add(s, "Dung lượng đầy hiện tại", "", ST_UNKNOWN);
    fmt_num(it->tag, sizeof it->tag, jget(bh, "charge_full_mah"), " mAh");
    ui_exp(it, "Dung lượng pin sạc đầy hiện nay mà máy ghi nhận (charge_full).");
    it = ui_add(s, "Dung lượng thiết kế", "", ST_UNKNOWN);
    fmt_num(it->tag, sizeof it->tag, jget(bh, "design_mah"), " mAh");
    ui_exp(it, "Nguồn: %s", jstrd(bh, "design_source", "None"));
    ui_exp(it, "Đây là mốc để so sánh, không do LoongMate đo.");
    fmt_num(b, sizeof b, pctv, "%");
    char tg[110];
    snprintf(tg, sizeof tg, "%s (ước lượng)", b);
    it = ui_add(s, "Sức khoẻ ước lượng", tg, pct < 0 ? ST_UNKNOWN : pct >= 80 ? ST_GREEN : pct >= 60 ? ST_YELLOW : ST_RED);
    ui_exp(it, "= dung lượng đầy hiện tại / dung lượng thiết kế.");
    ui_exp(it, "Ngưỡng màu của LoongMate: từ 80%% xanh, từ 60%% vàng, dưới đó đỏ.");
    ui_exp(it, "Nguồn dung lượng thiết kế: %s", jstrd(bh, "design_source", "None"));
    it = ui_add(s, "Sức khoẻ theo driver", "", ST_UNKNOWN);
    fmt_num(it->tag, sizeof it->tag, jget(bh, "driver_health"), "");
    ui_exp(it, "Trường 'health' do driver pin của kernel báo; không phải con số của LoongMate.");
    it = ui_add(s, "Xu hướng", "", ST_UNKNOWN);
    fmt_num(it->tag, sizeof it->tag, jget(bh, "trend_mah_per_30d"), " mAh/30 ngày");
    ui_exp(it, "Thay đổi dung lượng đầy ước tính theo 30 ngày; chưa đủ dữ liệu thì 'không có'.");
    ui_exp(it, "%s", jstrd(bh, "reason", ""));
}

static void battery_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *bh = app_json_or_error(j, "Sức khoẻ pin");
    if (!bh) return;
    if (!jget(bh, "available") && jstr(bh, "message")) {      /* lỗi dùng/môi trường của CLI */
        ui_info1("Sức khoẻ pin", "!! %s", jstr(bh, "message"));
        cJSON_Delete(bh);
        return;
    }
    ui_screen *sc = ui_screen_new("Sức khoẻ pin", build_battery);
    sc->d = bh;
    ui_push(sc);
}
void open_battery(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang tính sức khoẻ pin", SHORT_TIMEOUT_MS, battery_done, "", "battery", "--days", "90", NULL);
}

/* ============================================================ cập nhật LoongMate */
static cJSON *g_upd;     /* `update status` mới nhất */
static cJSON *g_ulist;   /* `update list` mới nhất (chỉ đọc), hoặc NULL */

/* status/apply/rollback nhận --apps-dir (thử trên sandbox); verify thì không */
#define APPS_ARGS() (P.apps_dir[0] ? (const char *)"--apps-dir" : NULL), (P.apps_dir[0] ? (const char *)P.apps_dir : NULL)

static void upd_verify_act(ui_screen *s, ui_item *it);
static void upd_ask_rollback(ui_screen *s, ui_item *it);
static void upd_list_verify(ui_screen *s, ui_item *it);

static void build_update(ui_screen *s) {
    cJSON *inst = jget(g_upd, "installed"), *backs = jarr(g_upd, "backups");
    const char *ver = jstr(inst, "version"), *at = jstr(inst, "installed_at");
    if (ver) ui_hdr(s, "Phiên bản đang chạy: %s%s%s%s", ver, at ? " (cài lúc " : "", at ? at : "", at ? ")" : "");
    else ui_hdr(s, "Phiên bản đang chạy: chưa ghi nhận (bản cài tay, chưa từng cập nhật bằng LoongMate).");
    cJSON *bl = jarr(g_ulist, "bundles"), *b;
    int nbl = bl ? cJSON_GetArraySize(bl) : 0;
    if (g_ulist) ui_hdr(s, "Thư mục gói: %s", jstrd(g_ulist, "update_dir", "?"));
    ui_head(s, "Gói cập nhật trên thẻ (%d)", nbl);
    ui_item *it;
    if (!g_ulist) {
        it = ui_add(s, "Không liệt kê được gói", "", ST_UNKNOWN);
        ui_exp(it, "!! Không đọc được `update list` từ engine (engine cũ?).");
    } else if (!nbl) {
        it = ui_add(s, "Không có gói nào", "", ST_UNKNOWN);
        ui_exp(it, "Chép thư mục gói (có manifest.json) vào %s trên thẻ chính rồi mở lại màn này.", jstrd(g_ulist, "update_dir", "LoongMate-update/"));
    }
    cJSON_ArrayForEach(b, bl) {
        const char *err = jstr(b, "error"), *bver = jstr(b, "version");
        int installed = jbool_d(b, "installed", 0);
        char tag[96];
        if (err) snprintf(tag, sizeof tag, "LỖI manifest");
        else if (installed) snprintf(tag, sizeof tag, "ĐANG CÀI");
        else snprintf(tag, sizeof tag, "v%s", bver ? bver : "?");
        it = ui_add(s, jstrd(b, "name", "?"), tag, err ? ST_RED : installed ? ST_GREEN : ST_YELLOW);
        ui_exp(it, "Gói: %s", jstrd(b, "name", "?"));
        ui_exp(it, "Đường dẫn: %s", jstrd(b, "path", "?"));
        if (err) {
            ui_exp(it, "!! %s", err);
            ui_exp(it, "Gói này không cập nhật được.");
        } else {
            ui_exp(it, "Phiên bản: %s", bver ? bver : "?");
            ui_exp(it, "Tạo lúc: %s", jstrd(b, "created_at", "không rõ"));
            ui_exp(it, "Số file: %.0f", jnumd(b, "files", 0));
            ui_exp(it, installed ? "+ Đây là bản ĐANG CÀI (cùng phiên bản và ngày tạo)." : "Khác bản đang cài.");
            cJSON *v = jget(b, "verify");
            if (cJSON_IsObject(v)) {
                if (jbool_d(v, "ok", 0)) ui_exp(it, "+ Đã kiểm SHA-256 từng file: HỢP LỆ.");
                else {
                    ui_exp(it, "!! Kiểm SHA-256: KHÔNG hợp lệ (%d lỗi):", jarr_n(v, "errors"));
                    cJSON *e;
                    int k = 0;
                    cJSON_ArrayForEach(e, jarr(v, "errors")) if (cJSON_IsString(e) && k++ < 4) ui_exp(it, "  %s", e->valuestring);
                }
            } else ui_exp(it, "Chưa kiểm SHA-256 (mục 'Kiểm SHA-256 mọi gói' bên dưới, hoặc A để kiểm rồi cập nhật).");
            ui_exp(it, "A — kiểm tra gói này bằng SHA-256 rồi hỏi xác nhận mới cập nhật.");
            ui_exp(it, "Cập nhật chỉ ghi vào các thư mục pak LoongMate và LoongMateAgent (và gỡ LoongMateNative cũ nếu còn); không đụng ROM/save/state.");
            ui_exp(it, "Bản đang chạy được sao lưu trước; lỗi giữa chừng thì tự trả về bản cũ.");
            ui_arg(it, jstrd(b, "path", ""));
            ui_act(it, 0, "KIỂM TRA & CẬP NHẬT", upd_verify_act);
        }
    }
    if (nbl) {
        it = ui_add(s, "Kiểm SHA-256 mọi gói", "chậm", ST_UNKNOWN);
        ui_exp(it, "Băm lại từng file của từng gói và hiện kết quả trên mỗi gói. Chỉ đọc, có thể mất vài chục giây.");
        ui_act(it, 0, "KIỂM", upd_list_verify);
    }
    ui_head(s, "Quay về");
    int nb = backs ? cJSON_GetArraySize(backs) : 0;
    if (nb) {
        cJSON *bk = cJSON_GetArrayItem(backs, nb - 1);
        it = ui_add(s, "Quay về bản trước", jstrd(bk, "version_before", "bản cài tay"), ST_YELLOW);
        ui_exp(it, "Bản sao lưu mới nhất: %s", jstrd(bk, "name", "?"));
        ui_exp(it, "Phiên bản trong bản sao lưu: %s", jstrd(bk, "version_before", "không rõ (bản cài tay trước khi cập nhật)"));
        ui_exp(it, "Có %d bản sao lưu; quay về luôn dùng bản mới nhất.", nb);
        ui_exp(it, "Có hộp xác nhận (mặc định Huỷ).");
        ui_act(it, 0, "QUAY VỀ", upd_ask_rollback);
    } else {
        it = ui_add(s, "Quay về bản trước", "không có", ST_UNKNOWN);
        ui_exp(it, "Chưa có bản sao lưu nào (chưa từng cập nhật bằng LoongMate).");
    }
}

/* `update status` rồi `update list` (chỉ đọc); arg: "1" = mở màn, "0" = dựng lại, "v" = dựng lại + kiểm SHA-256 mọi gói */
static void update_list_done(lm_job *j, const char *arg) {
    cJSON *l = cJSON_Parse(lm_job_stdout(j));
    cJSON_Delete(g_ulist);
    g_ulist = (cJSON_IsObject(l) && jarr(l, "bundles")) ? l : NULL;
    if (!g_ulist) cJSON_Delete(l);
    if (!strcmp(arg, "1")) ui_push(ui_screen_new("Cập nhật LoongMate", build_update));
    else ui_rebuild_all();
}
static void update_status_done(lm_job *j, const char *arg) {
    cJSON *st = app_json_or_error(j, "Cập nhật LoongMate");
    if (!st) return;
    cJSON_Delete(g_upd);
    g_upd = st;
    if (!cli_task("Đang liệt kê gói cập nhật", LONG_TIMEOUT_MS, update_list_done, arg, "update", "list", NULL)) {
        cJSON_Delete(g_ulist);
        g_ulist = NULL;
        if (!strcmp(arg, "1")) ui_push(ui_screen_new("Cập nhật LoongMate", build_update)); else ui_rebuild_all();
    }
}
void open_update(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang đọc trạng thái cập nhật", SHORT_TIMEOUT_MS, update_status_done, "1", "update", "status", APPS_ARGS(), NULL);
}
static void refresh_update_status(void) {
    cli_task("Đang đọc trạng thái cập nhật", SHORT_TIMEOUT_MS, update_status_done, "0", "update", "status", APPS_ARGS(), NULL);
}
static void upd_list_verify(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang kiểm SHA-256 các gói", LONG_TIMEOUT_MS, update_list_done, "v", "update", "list", "--verify", NULL);
}

static void update_apply_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả cập nhật");
    if (!r) return;
    lines_t L = {0};
    if (jbool_d(r, "ok", 0)) {
        char paks[300], n[24];
        join_strings(paks, sizeof paks, jarr(r, "paks"));
        app_scalar(jget(r, "files"), n, sizeof n);
        lines_add(&L, "+ Đã cập nhật lên %s (trước đó: %s).", jstrd(r, "version", "?"), jstrd(r, "previous_version", "chưa ghi nhận"));
        lines_add(&L, "Thay: %s  (%s file)", paks, n);
        lines_add(&L, "Bản sao lưu: %s", jstrd(r, "backup", "None"));
        lines_add(&L, "%s", "");
        lines_add(&L, "!! %s.", RESTART_NOTE);
    } else {
        lines_add(&L, "!! Cập nhật thất bại ở bước '%s': %s", jstrd(r, "stage", "?"), jstrd(r, "error", jstrd(r, "message", "không rõ")));
        lines_add(&L, "%s", jbool_d(r, "rolled_back", 0) ? "+ Đã trả về bản cũ." : "Chưa thay gì.");
        int k = 0;
        cJSON *e;
        cJSON_ArrayForEach(e, jarr(r, "errors")) if (cJSON_IsString(e) && k++ < 6) lines_add(&L, "!! %s", e->valuestring);
    }
    cJSON_Delete(r);
    ui_info("Kết quả cập nhật", &L);
    refresh_update_status();
}
static void do_update_apply(const char *arg) {       /* arg = đường dẫn gói đã chọn */
    /* --yes chỉ sau khi người dùng đã xác nhận trong hộp thoại */
    cli_task("Đang cập nhật", LONG_TIMEOUT_MS, update_apply_done, "", "update", "apply", arg, "--yes", APPS_ARGS(), NULL);
}

static void update_verify_done(lm_job *j, const char *arg) {       /* arg = đường dẫn gói */
    cJSON *v = app_json_or_error(j, "Kiểm tra gói");
    if (!v) return;
    lines_t L = {0};
    if (!jget(v, "ok") && jstr(v, "message")) {      /* lỗi dùng của CLI */
        lines_add(&L, "!! %s", jstr(v, "message"));
        cJSON_Delete(v);
        ui_info("Không kiểm tra được gói", &L);
        return;
    }
    if (!jbool_d(v, "ok", 0)) {
        lines_add(&L, "!! Không cập nhật. Các lỗi:");
        cJSON *e, *errs = jarr(v, "errors");
        int n = errs ? cJSON_GetArraySize(errs) : 0, k = 0;
        if (!n) lines_add(&L, "!! không rõ lý do");
        cJSON_ArrayForEach(e, errs) if (cJSON_IsString(e) && k++ < 8) lines_add(&L, "!! %s", e->valuestring);
        if (n > 8) lines_add(&L, "… và %d lỗi nữa", n - 8);
        cJSON_Delete(v);
        ui_info("Gói KHÔNG hợp lệ", &L);
        return;
    }
    char paks[300], n[24];
    join_strings(paks, sizeof paks, jarr(v, "paks"));
    app_scalar(jget(v, "files"), n, sizeof n);
    lines_add(&L, "+ Gói hợp lệ: %s file khớp SHA-256.", n);
    lines_add(&L, "Gói: %s", arg);
    lines_add(&L, "Lên phiên bản: %s", jstrd(v, "version", "?"));
    lines_add(&L, "Đang chạy: %s", jstrd(jget(g_upd, "installed"), "version", "chưa ghi nhận"));
    lines_add(&L, "Sẽ thay: %s", paks);
    lines_add(&L, "Bản đang chạy được sao lưu trước; lỗi giữa chừng thì tự trả về bản cũ.");
    lines_add(&L, "%s sau khi cập nhật.", RESTART_NOTE);
    lines_add(&L, "! Engine kiểm tra lại gói này ngay khi bạn xác nhận (chỉ ghi nếu vẫn hợp lệ).");
    cJSON_Delete(v);
    ui_confirm("Xác nhận cập nhật LoongMate", &L, "Cập nhật", do_update_apply, arg);
}
static void upd_verify_act(ui_screen *s, ui_item *it) {
    (void)s;
    cli_task("Đang kiểm tra gói", LONG_TIMEOUT_MS, update_verify_done, it->arg, "update", "verify", it->arg, NULL);
}

static void update_rollback_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả quay về");
    if (!r) return;
    lines_t L = {0};
    if (jbool_d(r, "ok", 0)) {
        char paks[300];
        join_strings(paks, sizeof paks, jarr(r, "paks"));
        lines_add(&L, "+ Đã quay về %s (từ %s).", jstrd(r, "version", "bản cài tay"), jstrd(r, "from_version", "chưa ghi nhận"));
        lines_add(&L, "Khôi phục: %s", paks);
        lines_add(&L, "Từ bản sao lưu: %s", jstrd(r, "backup", "None"));
        lines_add(&L, "%s", "");
        lines_add(&L, "!! %s.", RESTART_NOTE_ROLLBACK);
    } else {
        lines_add(&L, "!! Quay về thất bại: %s", jstrd(r, "error", jstrd(r, "message", "không rõ")));
        lines_add(&L, "%s", jbool_d(r, "rolled_back", 0) ? "+ Đã trả về như trước." : "Chưa thay gì.");
    }
    cJSON_Delete(r);
    ui_info("Kết quả quay về", &L);
    refresh_update_status();
}
static void do_update_rollback(const char *arg) {
    (void)arg;
    cli_task("Đang quay về bản trước", LONG_TIMEOUT_MS, update_rollback_done, "", "update", "rollback", "--yes", APPS_ARGS(), NULL);
}
static void upd_ask_rollback(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cJSON *backs = jarr(g_upd, "backups");
    int nb = backs ? cJSON_GetArraySize(backs) : 0;
    if (!nb) return;
    cJSON *b = cJSON_GetArrayItem(backs, nb - 1);
    lines_t L = {0};
    lines_add(&L, "Sẽ khôi phục các pak từ bản sao lưu %s.", jstrd(b, "name", "?"));
    lines_add(&L, "Phiên bản trong bản sao lưu: %s", jstrd(b, "version_before", "không rõ"));
    lines_add(&L, "Bản đang chạy sẽ bị thay (các bản sao lưu được giữ nguyên).");
    lines_add(&L, "%s sau khi quay về.", RESTART_NOTE_ROLLBACK);
    ui_confirm("Xác nhận quay về bản trước", &L, "Quay về", do_update_rollback, "");
}
