/* LoongMate native — SaveVault → "Khôi phục save một game" (cli.py saves list | history KEY | restore KEY --snapshot ID).
 * Native không tự đọc kho: danh sách game, phiên bản, xem trước và khôi phục đều do engine làm (SaveVault.plan_game_restore /
 * restore_game_files: pre-restore snapshot, băm lại sau khi ghi). Luồng: chọn game -> chọn phiên bản -> XEM TRƯỚC (không --yes)
 * -> hộp xác nhận (mặc định Huỷ) -> `restore --yes` -> kết quả. Khoá game và id snapshot luôn là MỘT phần tử argv riêng
 * (không qua shell); id dạng --snapshot=ID và khoá đứng sau `--` để khoá/ id bắt đầu bằng '-' không bị hiểu là cờ. */
#define _GNU_SOURCE
#include "lm_app.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_base;                 /* độ sâu ngăn xếp của màn SaveVault: khôi phục xong thì quay về đó (danh sách game/phiên bản đã cũ) */
static char g_rkey[UI_ARGLEN];     /* game và snapshot đang xem trước / xác nhận (hộp xác nhận chỉ giữ MỘT chuỗi arg) */
static char g_rsid[160];
static char g_rname[200];

static const char *mbs(double bytes) { static char b[4][32]; static int i; i = (i + 1) & 3; ui_fmt_bytes(b[i], sizeof b[i], bytes); return b[i]; }

static const char *card_vi(const char *c) {
    if (!c) return "-";
    if (!strcmp(c, "primary")) return "SD1";
    if (!strcmp(c, "secondary")) return "SD2";
    if (!strcmp(c, "both")) return "SD1 + SD2";
    return c;
}

/* hộp lỗi/thông tin từ JSON {"error":CODE,"message":...} (hoặc JSON không có kết quả mong đợi) */
static void show_error(const char *title, const cJSON *r) {
    lines_t L = {0};
    lines_add(&L, "!! %s", jstrd(r, "message", jstrd(r, "error", "engine không trả kết quả mong đợi")));
    if (jstr(r, "error")) lines_add(&L, "Mã lỗi: %s", jstr(r, "error"));
    ui_info(title, &L);
}

/* ============================================================ 3. kết quả khôi phục */
static void restore_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả khôi phục save");
    if (!r) return;
    cJSON *restored = jarr(r, "restored");
    if (jstr(r, "error") || (!restored && !jbool_d(r, "nothing_to_do", 0))) {
        lines_t L = {0};
        lines_add(&L, "!! %s", jstrd(r, "message", jstrd(r, "error", "engine không trả kết quả mong đợi")));
        if (jstr(r, "error")) lines_add(&L, "Mã lỗi: %s", jstr(r, "error"));
        lines_add(&L, "Nếu đã ghi dở, mở lại SaveVault: sẽ có mục Rollback.");
        cJSON_Delete(r);
        ui_info("Khôi phục thất bại", &L);
        app_refresh();
        return;
    }
    lines_t L = {0};
    const char *title;
    if (jbool_d(r, "nothing_to_do", 0)) {
        lines_add(&L, "+ Save hiện tại của %s đã giống bản này — không ghi gì.", g_rname);
        title = "Không cần khôi phục";
    } else {
        int ok = jbool_d(r, "success", 0), verified = jbool_d(r, "verified", 0);
        int nr = restored ? cJSON_GetArraySize(restored) : 0;
        const char *pre = jstr(r, "pre_restore_id");
        cJSON *failed = jarr(r, "failed"), *x;
        lines_add(&L, "%sĐã khôi phục %d file của %s%s.", ok ? "+ " : "!! ", nr, g_rname, verified ? " (đã băm lại, khớp snapshot)" : "");
        cJSON_ArrayForEach(x, restored) if (cJSON_IsString(x)) lines_add(&L, "  %s", x->valuestring);
        if (pre) {
            lines_add(&L, "Đường lùi: snapshot %s (nhãn pre_restore).", pre);
            lines_add(&L, "Để quay lại: khôi phục snapshot %s (mở lại Khôi phục save một game, chọn game này rồi chọn snapshot đó).", pre);
        }
        if (failed && cJSON_GetArraySize(failed)) {
            lines_add(&L, "!! %d file KHÔNG khớp sau khi ghi:", cJSON_GetArraySize(failed));
            cJSON_ArrayForEach(x, failed) if (cJSON_IsString(x)) lines_add(&L, "!!   %s", x->valuestring);
        }
        cJSON *miss = jarr(r, "missing_files");
        if (miss && cJSON_GetArraySize(miss)) {
            lines_add(&L, "!! %d file THIẾU trong kho nên chưa khôi phục được:", cJSON_GetArraySize(miss));
            cJSON_ArrayForEach(x, miss) if (cJSON_IsString(x)) lines_add(&L, "!!   %s", x->valuestring);
        }
        cJSON *pc = jarr(r, "preserved_conflicts");
        if (pc && cJSON_GetArraySize(pc)) lines_add(&L, "Bản đang chơi (mới hơn) được cất ở: %s", jstrd(r, "conflicts_dir", "SaveVault/conflicts/"));
        cJSON *rej = jarr(r, "rejected_paths"), *ro = jarr(r, "skipped_readonly");
        cJSON_ArrayForEach(x, rej) if (cJSON_IsString(x)) lines_add(&L, "!! Bị từ chối: %s", x->valuestring);
        cJSON_ArrayForEach(x, ro) if (cJSON_IsString(x)) lines_add(&L, "!! Bỏ qua (read-only/gần đầy): %s", x->valuestring);
        if (!ok && jstr(r, "message")) lines_add(&L, "!! %s", jstr(r, "message"));
        title = ok ? "Kết quả khôi phục save" : "Khôi phục chưa trọn vẹn";
    }
    cJSON_Delete(r);
    ui_pop_to(g_base);              /* về màn SaveVault: danh sách game/phiên bản đã cũ */
    ui_info(title, &L);
    app_refresh();                  /* có thêm snapshot pre_restore */
}

static void do_restore(const char *arg) {
    (void)arg;
    /* --yes chỉ sau khi người dùng đã xác nhận trong hộp thoại (mặc định Huỷ) */
    char snap[200];
    snprintf(snap, sizeof snap, "--snapshot=%s", g_rsid);
    cli_task("Đang khôi phục save", LONG_TIMEOUT_MS, restore_done, "", "saves", "restore", snap, "--yes", "--", g_rkey, NULL);
}

/* ============================================================ 2b. xem trước */
static void preview_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Xem trước khôi phục save");
    if (!r) return;
    if (jstr(r, "error")) { show_error("Không thể khôi phục", r); cJSON_Delete(r); return; }
    const char *name = jstrd(r, "name", g_rname);
    snprintf(g_rname, sizeof g_rname, "%s", name);
    if (jbool_d(r, "nothing_to_do", 0)) {
        lines_t L = {0};
        lines_add(&L, "+ Save hiện tại của %s đã giống bản này (snapshot %s) — không cần khôi phục.", name, g_rsid);
        ui_info("Không cần khôi phục", &L);
        cJSON_Delete(r);
        return;
    }
    cJSON *files = jarr(r, "files");
    if (!jbool_d(r, "preview", 0) || !files) { show_error("Không thể khôi phục", r); cJSON_Delete(r); return; }
    lines_t L = {0};
    cJSON *f;
    int nw = 0, nu = 0;
    cJSON_ArrayForEach(f, files) { if (!strcmp(jstrd(f, "action", ""), "unchanged")) nu++; else nw++; }
    lines_add(&L, "!! Khôi phục CHỈ file save/state của %s từ snapshot %s.", name, g_rsid);
    lines_add(&L, "• Sẽ ghi %d file%s.", nw, nu ? "" : " (không có file nào giữ nguyên)");
    if (nu) lines_add(&L, "• %d file đã giống hệt bản trong snapshot nên được giữ nguyên.", nu);
    lines_add(&L, "• LoongMate tự tạo pre-restore snapshot trước khi ghi (đường lùi, chứa cả bản đang chơi của game).");
    lines_add(&L, "• Game khác không bị đụng.");
    lines_add(&L, "%s", "");
    cJSON_ArrayForEach(f, files) {
        const char *act = jstrd(f, "action", "");
        const char *tag = !strcmp(act, "create") ? "+ [TẠO MỚI]" : !strcmp(act, "overwrite") ? "[GHI ĐÈ]" : "[GIỮ NGUYÊN]";
        cJSON *cs = jget(f, "current_size");
        if (cJSON_IsNumber(cs)) lines_add(&L, "%s %s  (%s, đang có %s)", tag, jstrd(f, "rel", "?"), mbs(jnumd(f, "size", 0)), mbs(cs->valuedouble));
        else lines_add(&L, "%s %s  (%s)", tag, jstrd(f, "rel", "?"), mbs(jnumd(f, "size", 0)));
        if (jbool_d(f, "newer_than_snapshot", 0))
            lines_add(&L, "! bản đang chơi MỚI HƠN snapshot — sẽ được cất vào SaveVault/conflicts/");
    }
    cJSON_Delete(r);
    ui_confirm("Xác nhận khôi phục save", &L, "Khôi phục", do_restore, "");
}

static void act_version(ui_screen *s, ui_item *it) {
    snprintf(g_rkey, sizeof g_rkey, "%s", s->key);
    snprintf(g_rsid, sizeof g_rsid, "%s", it->arg);
    snprintf(g_rname, sizeof g_rname, "%s", jstrd(s->d, "name", s->key));
    char snap[200];
    snprintf(snap, sizeof snap, "--snapshot=%s", g_rsid);
    /* không --yes: engine chỉ trả `preview` (exit 1 là bình thường), chưa ghi gì và chưa tạo snapshot */
    cli_task("Đang so sánh với save hiện tại", LONG_TIMEOUT_MS, preview_done, "", "saves", "restore", snap, "--", g_rkey, NULL);
}

/* ============================================================ 2. danh sách phiên bản của một game */
static void build_versions(ui_screen *s) {
    const char *name = jstrd(s->d, "name", s->key);
    ui_hdr(s, "%s: các phiên bản save/state trong snapshot (mới nhất trên cùng).", name);
    ui_hdr(s, "A — xem trước rồi khôi phục. Chỉ file của game này được ghi; game khác không bị đụng.");
    cJSON *v;
    cJSON_ArrayForEach(v, jarr(s->d, "versions")) {
        const char *sid = jstr(v, "snapshot_id");
        cJSON *files = jarr(v, "files"), *f;
        if (!sid || !files) continue;
        int nf = cJSON_GetArraySize(files), same = nf > 0;
        double total = 0;
        cJSON_ArrayForEach(f, files) {
            total += jnumd(f, "size", 0);
            if (!(jget(f, "same_as_current") && cJSON_IsTrue(jget(f, "same_as_current")))) same = 0;   /* null (chưa có file hiện tại) và false đều là "không giống" */
        }
        char label[200];
        const char *lb = jstrd(v, "label", "");
        if (*lb && strcmp(lb, "manual")) snprintf(label, sizeof label, "%s  %s", jstrd(v, "created_at", sid), lb);   /* "manual" là mặc định, chỉ chiếm chỗ: nhãn khác mới hiện */
        else snprintf(label, sizeof label, "%s", jstrd(v, "created_at", sid));
        ui_item *it = ui_add(s, label, same ? "giống hiện tại" : mbs(total), same ? ST_GREEN : ST_UNKNOWN);
        ui_arg(it, sid);
        ui_exp(it, "Snapshot: %s", sid);
        ui_exp(it, "Nhãn: %s", jstr(v, "label") && *jstr(v, "label") ? jstr(v, "label") : "-");
        ui_exp(it, "Tạo lúc: %s", jstrd(v, "created_at", "không rõ"));
        ui_exp(it, "%d file, %s", nf, mbs(total));
        ui_exp(it, "%s", "");
        cJSON_ArrayForEach(f, files) {
            cJSON *sc = jget(f, "same_as_current");
            const char *rel = jstrd(f, "rel", "?"), *base = strrchr(rel, '/');
            ui_exp(it, "• %s — %s — %s", base ? base + 1 : rel, mbs(jnumd(f, "size", 0)),
                   !cJSON_IsBool(sc) ? "chưa có file hiện tại" : cJSON_IsTrue(sc) ? "GIỐNG bản hiện tại" : "khác bản hiện tại");
        }
        if (same) ui_exp(it, "+ Mọi file đã giống save đang chơi — khôi phục bản này không đổi gì.");
        ui_act(it, 0, "XEM TRƯỚC & KHÔI PHỤC", act_version);
    }
    if (!s->n) ui_head(s, "(không có phiên bản nào)");
}

static void history_done(lm_job *j, const char *key) {
    cJSON *r = app_json_or_error(j, "Phiên bản save");
    if (!r) return;
    if (jstr(r, "error") || !jarr(r, "versions")) { show_error("Phiên bản save", r); cJSON_Delete(r); return; }
    char title[200];
    snprintf(title, sizeof title, "Khôi phục save — %s", jstrd(r, "name", key));
    ui_screen *s = ui_screen_new(title, build_versions);
    if (!s) { cJSON_Delete(r); return; }
    snprintf(s->key, sizeof s->key, "%s", key);
    s->d = r;                       /* màn sở hữu JSON, giải phóng khi đóng */
    ui_push(s);
}

static void act_game(ui_screen *s, ui_item *it) {
    (void)s;
    if (it->iarg <= 0) {
        lines_t L = {0};
        lines_add(&L, "Chưa có snapshot nào chứa save của game này.");
        lines_add(&L, "Chọn Sao lưu ngay ở màn SaveVault trước.");
        ui_info("Chưa có phiên bản", &L);
        return;
    }
    cli_task("Đang đọc lịch sử save của game", LONG_TIMEOUT_MS, history_done, it->arg, "saves", "history", "--", it->arg, NULL);
}

/* ============================================================ 1. danh sách game */
static void build_games(ui_screen *s) {
    cJSON *games = jarr(s->d, "games"), *g;
    ui_hdr(s, "%d game có save/state hiện tại hoặc trong snapshot.", games ? cJSON_GetArraySize(games) : 0);
    ui_hdr(s, "Chọn game để xem các phiên bản save đã sao lưu.");
    cJSON_ArrayForEach(g, games) {
        const char *key = jstr(g, "key"), *name = jstrd(g, "name", key);
        if (!key) continue;
        int nv = (int)jnumd(g, "versions", 0);
        const char *core = jstr(g, "core");
        char tag[96];
        /* số bản trước, core cuối: tag bị cắt "..." thì chỉ mất core (đã có ở khung giải thích) */
        if (nv) snprintf(tag, sizeof tag, "%d bản · %s%s%s", nv, card_vi(jstr(g, "card")), core && *core ? " · " : "", core && *core ? core : "");
        else snprintf(tag, sizeof tag, "chưa có bản · %s", card_vi(jstr(g, "card")));
        ui_item *it = ui_add(s, name ? name : key, tag, nv ? ST_GREEN : ST_UNKNOWN);
        ui_arg(it, key);
        it->iarg = nv;
        cJSON *files = jarr(g, "files"), *f;
        int nf = files ? cJSON_GetArraySize(files) : 0, shown = 0;
        double total = 0;
        cJSON_ArrayForEach(f, files) total += jnumd(f, "size", 0);
        ui_exp(it, "Game: %s", name ? name : key);
        ui_exp(it, "Hệ máy: %s", jstrd(g, "system", "-"));
        ui_exp(it, "Emulator/core: %s", core && *core ? core : "-");
        ui_exp(it, "Thẻ: %s", card_vi(jstr(g, "card")));
        if (nf) ui_exp(it, "File hiện tại: %d (%s)", nf, mbs(total)); else ui_exp(it, "File hiện tại: 0");
        ui_exp(it, "Phiên bản trong snapshot: %d", nv);
        ui_exp(it, "%s", "");
        cJSON_ArrayForEach(f, files) { if (shown++ >= 8) break; ui_exp(it, "• %s", jstrd(f, "rel", "?")); }
        if (nf > 8) ui_exp(it, "… và %d file nữa", nf - 8);
        if (!nv) ui_exp(it, "!! Chưa có snapshot nào chứa save của game này — chọn Sao lưu ngay trước.");
        ui_act(it, 0, "XEM PHIÊN BẢN", act_game);
    }
    if (!s->n) ui_head(s, "(chưa có save nào)");
}

static void games_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Khôi phục save một game");
    if (!r) return;
    if (jstr(r, "error") || !jarr(r, "games")) { show_error("Khôi phục save một game", r); cJSON_Delete(r); return; }
    ui_screen *s = ui_screen_new("Khôi phục save — chọn game", build_games);
    if (!s) { cJSON_Delete(r); return; }
    s->d = r;
    g_base = ui_depth();            /* màn SaveVault đang là màn trên cùng */
    ui_push(s);
}

void open_game_saves(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang đọc save và snapshot", LONG_TIMEOUT_MS, games_done, "", "saves", "list", NULL);
}
