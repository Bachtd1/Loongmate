/* LoongMate native — Library > Tải từ web (duyệt một trang web https bất kỳ, chọn file để tải rồi cài bằng Nhập game). Mọi việc đi qua
 * `cli.py web ... --json`; native không nói chuyện mạng, không ghi file người dùng. Luồng:
 *   `web home` (lịch sử, dấu trang, link trong <thẻ>/Import/links.txt, job tải dở) -> nhập địa chỉ (bàn phím ui_osk) / chọn mục
 *   -> `web open URL` -> màn trang (một màn mỗi trang, B quay lại trang trước): ô tìm kiếm, lưu dấu trang, file tải được, liên kết trong/ngoài trang
 *   -> file: `web fetch URL --dry-run` (hỏi máy chủ tên/kích thước/hệ máy, chọn thẻ, HỎI khi phải dùng thẻ Leaf) -> hộp xác nhận mặc định Huỷ
 *   (có MÁY CHỦ THẬT sau chuyển hướng để nhận ra quảng cáo) -> `web fetch --yes --progress` (B = tạm dừng, tiếp ở "Tải dở") -> kết quả của importer.
 * URL dài hơn UI_ARGLEN (640) nên không đi qua ui_item.arg/tham số callback: giữ trong g_url hoặc tra lại trong JSON của màn (iarg = chỉ số). */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#define PAGE_DEPTH_MAX 24     /* ngăn xếp 36: chừa chỗ cho bàn phím, xác nhận và màn con */
#define WEB_OPEN_TIMEOUT_MS 45000
#define GALLERY_MIN 4         /* trang có từ chừng này link có ảnh bìa trở lên: mở dạng lưới */
#define MAX_LINKS_GRID 400

static ui_screen *g_wh;       /* màn "TẢI TỪ WEB" đang mở (NULL khi chưa/đã đóng); s->d = `web home` */
static char *g_url;           /* URL đang xử lý giữa các bước (mở, tải, xoá dấu trang) */
static char *g_page;          /* trang chứa link file đang tải (engine lấy ảnh bìa khớp game từ trang này); NULL nếu không có */
static char *g_cover;         /* địa chỉ ảnh bìa của game (trang game mà người dùng đi qua để tới link tải); NULL nếu không có */
static lines_t g_pend;        /* kết quả đã dựng, chờ hiện sau khi màn đầu được làm mới */
static char g_pend_title[64];

static const char *mbs(double bytes) { static char b[4][32]; static int i; i = (i + 1) & 3; ui_fmt_bytes(b[i], sizeof b[i], bytes); return b[i]; }
static void set_url(const char *u) { char *n = strdup(u ? u : ""); free(g_url); g_url = n; }
static const char *cur_url(void) { return g_url ? g_url : ""; }

/* cắt ở ranh giới UTF-8, thêm "..." khi bị cắt */
static void cut_utf8(char *dst, size_t n, const char *src, size_t maxb) {
    size_t len = strlen(src);
    if (len <= maxb && len < n) { memcpy(dst, src, len + 1); return; }
    size_t k = maxb < n - 4 ? maxb : n - 4;
    while (k > 0 && ((unsigned char)src[k] & 0xC0) == 0x80) k--;
    memcpy(dst, src, k);
    memcpy(dst + k, "...", 4);
}

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
static cJSON *web_ok_json(lm_job *j, const char *title) {
    cJSON *r = app_json_or_error(j, title);
    if (!r) return NULL;
    if (jbool_d(r, "success", jget(r, "ok") != NULL)) return r;
    lines_t L = {0};
    const char *k = jstrd(r, "error_kind", "");
    lines_add(&L, "!! %s", jstrd(r, "message", tr("Engine báo lỗi không kèm lý do.")));
    if (!strcmp(k, "tls")) lines_add(&L, tr("Máy chủ không xác minh được (chứng thư/bộ CA) nên KHÔNG mở. Không tắt kiểm tra."));
    else if (!strcmp(k, "unreachable") || !strcmp(k, "timeout") || !strcmp(k, "network")) lines_add(&L, tr("Kiểm tra Wi-Fi/Internet của máy."));
    cJSON_Delete(r);
    ui_info(title, &L);
    return NULL;
}

/* ============================================================ màn đầu: làm mới */
static void home_done(lm_job *j, const char *arg) {
    (void)arg;
    char err[300] = "";
    cJSON *r = ui_job_json(j, err, sizeof err);
    if (r && !jbool_d(r, "success", 0)) { snprintf(err, sizeof err, "%s", jstrd(r, "message", tr("engine báo lỗi"))); cJSON_Delete(r); r = NULL; }
    if (r && g_wh) { cJSON_Delete(g_wh->d); g_wh->d = r; ui_rebuild(g_wh); }
    else if (r) cJSON_Delete(r);
    else {
        if (!g_pend.n) snprintf(g_pend_title, sizeof g_pend_title, tr("Tải từ web"));
        lines_add(&g_pend, tr("! Không làm mới được danh sách Tải từ web: %s"), err);
    }
    show_pending();
}
static void home_reload(void) {
    if (!g_wh) { show_pending(); return; }
    cli_task(tr("Đang đọc Tải từ web"), SHORT_TIMEOUT_MS, home_done, "", "web", "home", NULL);
}

/* kết quả đơn giản (dấu trang, huỷ job, xoá lịch sử): hiện message rồi làm mới màn đầu */
static void simple_done(lm_job *j, const char *arg) {
    cJSON *r = app_json_or_error(j, arg);
    if (!r) return;
    lines_t L = {0};
    lines_add(&L, "%s%s", jbool_d(r, "success", 0) ? "+ " : "!! ", jstrd(r, "message", jbool_d(r, "success", 0) ? "Xong." : tr("Engine báo lỗi không kèm lý do.")));
    cJSON_Delete(r);
    set_pending(arg, L);
    home_reload();
}

/* ============================================================ tải file: kế hoạch -> xác nhận -> tải & cài */
static void dl_plan(const char *pref);
static void dl_confirm_primary(const char *arg) { dl_plan(arg); }
static void dl_do_fetch(const char *pref);

static void dl_plan_done(lm_job *j, const char *arg) {
    cJSON *r = web_ok_json(j, tr("Tải từ web"));
    if (!r) return;
    cJSON *card = jget(r, "card"), *f, *p, *o;
    lines_t P = {0};     /* vấn đề, nguyên văn */
    cJSON_ArrayForEach(f, jarr(r, "files")) {
        const char *pr = jstr(f, "problem");
        if (pr && pr[0]) lines_add(&P, "!! %s: %s", jstrd(f, "name", "?"), pr);
    }
    cJSON_ArrayForEach(p, jarr(r, "problems")) lines_add(&P, "!! %s: %s", jstrd(p, "path", "?"), jstrd(p, "reason", "?"));
    if (jbool_d(r, "needs_card_choice", 0)) {
        lines_t L = {0};
        lines_add(&L, "!! %s", jstrd(card, "reason", ""));
        lines_add(&L, "%s", "");
        lines_add(&L, tr("LoongMate KHÔNG tự chuyển sang thẻ chính — bạn quyết định."));
        int usable = 0;
        cJSON_ArrayForEach(o, jarr(card, "options")) if (!strcmp(jstrd(o, "card", ""), "primary") && jbool_d(o, "usable", 0)) usable = 1;
        for (int i = 0; i < P.n; i++) lines_add(&L, "%s", P.v[i]);
        int nprob = P.n;
        lines_free(&P);
        cJSON_Delete(r);
        if (usable && !nprob) ui_confirm(tr("Thẻ phụ không dùng được"), &L, tr("Dùng thẻ Leaf"), dl_confirm_primary, "primary");
        else ui_info(usable ? tr("Không tải được") : tr("Không có thẻ nào dùng được"), &L);
        return;
    }
    if (!jbool_d(r, "ok", 0) || P.n) {
        if (!P.n) lines_add(&P, "!! %s", jstrd(r, "message", tr("Engine không cho tải nhưng không nêu lý do.")));
        cJSON_Delete(r);
        ui_info(tr("Không tải được"), &P);     /* nhận quyền sở hữu P */
        return;
    }
    lines_free(&P);

    if (ui_card_pick(card, arg, dl_confirm_primary)) { cJSON_Delete(r); return; }     /* hai thẻ dùng được: hỏi */

    lines_t L = {0};
    cJSON_ArrayForEach(f, jarr(r, "files")) {
        const char *sys = jstr(f, "system");
        lines_add(&L, tr("Tên file: %s"), jstrd(f, "name", "?"));
        lines_add(&L, tr("Dung lượng: %s"), jnumd(f, "size", -1) >= 0 ? mbs(jnumd(f, "size", 0)) : tr("không rõ"));
        lines_add(&L, tr("Hệ máy: %s"), sys && sys[0] ? sys : tr("chưa nhận diện (trình Nhập game sẽ kiểm tra)"));
        lines_add(&L, tr("Máy chủ thật (sau chuyển hướng): %s"), jstrd(f, "host", jstrd(r, "host", "?")));
        lines_add(&L, "%s", "");
    }
    const char *sel_card = jstrd(card, "selected", "");
    const char *sd_tag = !strcmp(sel_card, "primary") ? "SD1" : !strcmp(sel_card, "secondary") ? "SD2" : "";
    lines_add(&L, tr("Thẻ đích: %s%s%s%s"), jstrd(card, "root", "?"), sd_tag[0] ? " (" : "", sd_tag, sd_tag[0] ? ")" : "");
    lines_add(&L, tr("Tổng %s; cần ~%s trống (file tải + staging + dự phòng)."), mbs(jnumd(r, "total_bytes", 0)), mbs(jnumd(r, "needed_bytes", 0)));
    lines_add(&L, "%s", "");
    lines_add(&L, tr("! Nếu máy chủ thật KHÔNG phải trang bạn đang xem (quảng cáo, trang lạ) hãy chọn Huỷ. Chỉ tải nội dung bạn có quyền sử dụng."));
    lines_add(&L, tr("File được kiểm rồi cài bằng đúng trình Nhập game; không tự chạy gì. Trong lúc tải: B = tạm dừng, tải tiếp ở Tải từ web > Tải dở."));
    char pref[16];
    snprintf(pref, sizeof pref, "%s", !strcmp(jstrd(card, "selected", ""), "primary") ? "primary" : !strcmp(jstrd(card, "selected", ""), "secondary") ? "secondary" : "auto");
    cJSON_Delete(r);
    ui_confirm(tr("Tải & cài từ web"), &L, tr("Tải & cài"), dl_do_fetch, pref);
}

static void dl_plan(const char *pref) {
    cli_task(tr("Đang kiểm với máy chủ"), 2 * SHORT_TIMEOUT_MS, dl_plan_done, pref, "web", "fetch", cur_url(), "--dry-run", "--card", pref, NULL);
}

static void dl_fetch_done(lm_job *j, const char *arg);
static void dl_do_fetch(const char *pref) {
    const char *a[16];
    int n = 0;
    a[n++] = "web"; a[n++] = "fetch"; a[n++] = cur_url(); a[n++] = "--yes"; a[n++] = "--progress"; a[n++] = "--card"; a[n++] = pref;
    if (g_page) { a[n++] = "--from-page"; a[n++] = g_page; }
    if (g_cover) { a[n++] = "--cover-url"; a[n++] = g_cover; }
    if (cli_task_v(tr("Đang tải & cài"), LONG_TIMEOUT_MS, dl_fetch_done, "", a, n)) ui_task_cancelable(1);
}

static void dl_fetch_done(lm_job *j, const char *arg) {
    (void)arg;
    int paused = ui_task_cancelled();
    lines_t L = {0};
    if (paused) {
        lines_add(&L, tr("! Đã tạm dừng. File đã tải được giữ lại — mở Tải từ web > Tải dở để tiếp tục."));
        lines_add(&L, tr("Chưa cài gì cả."));
    } else {
        cJSON *r = app_json_or_error(j, tr("Tải & cài"));
        if (!r) return;
        int ok = jbool_d(r, "success", 0), started = jbool_d(r, "started", 1);
        lines_add(&L, "%s%s", ok ? "+ " : "!! ", jstrd(r, "message", "?"));
        cJSON *x;
        cJSON_ArrayForEach(x, jarr(r, "results")) {
            const char *st = jstrd(x, "status", "?");
            const char *mark = (!strcmp(st, "installed") || !strcmp(st, "already_present")) ? "+" : (!strcmp(st, "blocked") || !strcmp(st, "failed")) ? "!!" : "!";
            lines_add(&L, "%s %s: %s — %s", mark, jstrd(x, "name", "?"), st, jstrd(x, "message", ""));
            if (jstr(x, "cover")) lines_add(&L, tr("+   ảnh bìa: %s"), jstr(x, "cover"));
            else if (jstr(x, "cover_note")) lines_add(&L, tr("    ảnh bìa: %s"), jstr(x, "cover_note"));
        }
        if (jarr_n(r, "patches")) lines_add(&L, tr("Đã lưu %d patch — mở Library → Patch ROM để áp dụng."), jarr_n(r, "patches"));
        if (jbool_d(r, "resumable", 0))
            lines_add(&L, tr("! Job %s còn nguyên: mở Tải từ web > Tải dở để tiếp tục (file đã tải được giữ)."), jstrd(r, "job_id", "?"));
        if (!started && jbool_d(r, "needs_card_choice", 0)) lines_add(&L, tr("!! Chưa chọn được thẻ đích."));
        app_add_rescan_lines(&L, jget(r, "rescan"));
        cJSON_Delete(r);
    }
    set_pending(paused ? tr("Đã tạm dừng") : tr("Kết quả tải & cài"), L);
    home_reload();     /* đứng yên tại chỗ (màn trang giữ nguyên); màn đầu đọc lại job dở rồi hiện kết quả */
}

static void dl_start(const char *url, const char *page, const char *cover) {
    set_url(url);
    free(g_page);
    g_page = page && page[0] ? strdup(page) : NULL;
    free(g_cover);
    g_cover = cover && cover[0] ? strdup(cover) : NULL;
    dl_plan("auto");
}

/* ============================================================ ảnh bìa: `web thumbs` chạy nền (không chặn phím) */
static lm_job *g_thumbs;
static Uint32 g_thumbs_until;   /* vẽ tiếp thêm chút sau khi job xong để ảnh cuối kịp hiện */

static int thumbs_pump(void) {
    if (g_thumbs) {
        if (lm_job_poll(g_thumbs)) { lm_job_free(g_thumbs); g_thumbs = NULL; g_thumbs_until = SDL_GetTicks() + 1500; }
        return 1;
    }
    return g_thumbs_until && (Sint32)(g_thumbs_until - SDL_GetTicks()) > 0;
}
static const void *g_thumbs_for;  /* màn trang đã xin tải ảnh gần nhất (để tự xin lại khi quay về trang thiếu ảnh) */
static void thumbs_start(const void *screen, const char *page_url) {
    const char *ex[] = { "web", "thumbs", "--first", page_url ? page_url : "" };
    if (g_thumbs) {               /* trang cũ chưa tải xong ảnh: dừng, trang mới ưu tiên */
        lm_job_kill(g_thumbs);
        for (int i = 0; i < 100 && !lm_job_poll(g_thumbs); i++) SDL_Delay(5);
        lm_job_free(g_thumbs);
        g_thumbs = NULL;
    }
    g_thumbs = cli_spawn(180000, ex, page_url && page_url[0] ? 4 : 2);
    g_thumbs_for = screen;
    ui_set_idle_pump(thumbs_pump);
}

/* ============================================================ màn trang */
static void build_page(ui_screen *s);
static void act_link(ui_screen *s, ui_item *it);
static int page_key(ui_screen *s, cat_input_event *ev);
static int gallery_count(ui_screen *s);
static int gallery_links(ui_screen *s, int *out, int max);
static void set_mode(ui_screen *s, int gallery);
static void act_gallery(ui_screen *s, ui_item *it);
static void act_next(ui_screen *s, ui_item *it);
static void act_prev(ui_screen *s, ui_item *it);

static cJSON *scr_json(ui_screen *s) { return s->priv ? (cJSON *)s->priv : s->d; }   /* màn con dùng JSON của trang cha (không sở hữu) */

static void open_page(const char *url);
static char g_carry_img[256];   /* ảnh bìa mang sang trang mở bằng link tải (đặt ngay trước open_page, dùng ở page_done) */
static char g_carry_url[1024];  /* địa chỉ gốc của ảnh đó (để engine gắn ảnh bìa cho game sau khi cài) */
static const char *cover_url_of(cJSON *page, cJSON *link) {   /* ảnh của chính link, không có thì ảnh bìa của trang */
    return jstrd(link, "thumb", "")[0] && jstrd(link, "img", "")[0] ? jstrd(link, "img", "") : jstrd(page, "image", "");
}
static void carry_cover(cJSON *page, cJSON *link) {
    const char *img = jstrd(link, "thumb", "")[0] ? jstrd(link, "thumb", "") : jstrd(page, "image_thumb", "");
    snprintf(g_carry_img, sizeof g_carry_img, "%s", img);
    snprintf(g_carry_url, sizeof g_carry_url, "%s", cover_url_of(page, link));
}
static void page_done(lm_job *j, const char *arg) {
    char carry[sizeof g_carry_img], carry_url[sizeof g_carry_url];
    snprintf(carry, sizeof carry, "%s", g_carry_img);
    snprintf(carry_url, sizeof carry_url, "%s", g_carry_url);
    g_carry_img[0] = g_carry_url[0] = 0;                 /* dùng một lần, kể cả khi mở trang lỗi */
    (void)arg;
    cJSON *r = web_ok_json(j, tr("Mở trang"));
    if (!r) return;
    const char *kind = jstrd(r, "kind", "");
    if (!strcmp(kind, "file")) {
        dl_start(jstrd(r, "url", cur_url()), NULL, carry_url);   /* link mở ra thẳng file: không có trang nguồn; ảnh bìa mang theo nếu có */
        cJSON_Delete(r);
        return;
    }
    if (strcmp(kind, "page")) {
        cJSON_Delete(r);
        ui_info1(tr("Mở trang"), tr("Engine trả loại nội dung lạ: %s"), kind);
        return;
    }
    /* bước tải kế tiếp (mở từ ô/link tải) thường không có og:image: giữ ảnh bìa của trang game vừa rời */
    if (carry[0] && !jstrd(r, "image_thumb", "")[0]) {
        cJSON_DeleteItemFromObject(r, "image_thumb");
        cJSON_AddStringToObject(r, "image_thumb", carry);
        cJSON_DeleteItemFromObject(r, "image");
        cJSON_AddStringToObject(r, "image", carry_url);
    }
    const char *t = jstrd(r, "title", "");
    char title[96];
    cut_utf8(title, sizeof title, t[0] ? t : jstrd(r, "host", "Trang web"), 40);
    ui_screen *s = ui_screen_new(title, build_page);
    if (!s) { cJSON_Delete(r); return; }
    s->d = r;
    s->key_fn = page_key;
    ui_push(s);
    if (jnumd(r, "thumbs_queued", 0) > 0) thumbs_start(s, jstrd(r, "url", ""));
    /* lưới khi trang nhiều ảnh bìa, hoặc có link tải (ô tải đứng đầu, đang được chọn: các bước tải chỉ cần bấm A) */
    set_mode(s, gallery_count(s) >= GALLERY_MIN || gallery_links(s, NULL, 0) > gallery_count(s));
}

static void act_close_all_pages(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    if (g_wh) {
        int d = ui_depth();
        for (int i = 0; i < d; i++) {
            if (ui_screen_at(i) == g_wh) {
                ui_pop_to(i + 1);
                return;
            }
        }
    }
    ui_pop_to(2);
}

static void prune_web_stack(void) {
    if (ui_depth() < 14) return;
    int d = ui_depth(), wh_idx = -1;
    for (int i = 0; i < d; i++) {
        if (ui_screen_at(i) == g_wh) { wh_idx = i; break; }
    }
    /* chỉ bỏ trang ngay trên màn Tải từ web (giữ màn đó và trang đang xem); không thấy màn đó thì không đụng gì */
    if (wh_idx >= 0 && wh_idx + 1 < d - 1) ui_drop_at(wh_idx + 1);
}

static void open_page(const char *url) {
    prune_web_stack();
    if (!url || !url[0] || url[0] == '-') { g_carry_img[0] = 0; ui_info1(tr("Tải từ web"), tr("Địa chỉ không hợp lệ.")); return; }
    set_url(url);
    cli_task(tr("Đang mở trang"), WEB_OPEN_TIMEOUT_MS, page_done, "", "web", "open", cur_url(), NULL);
}

static const char *link_url(ui_screen *s, const ui_item *it) {
    return jstr(cJSON_GetArrayItem(jarr(scr_json(s), "links"), it->iarg), "url");
}
static void act_link(ui_screen *s, ui_item *it) {
    cJSON *l = cJSON_GetArrayItem(jarr(scr_json(s), "links"), it->iarg);
    if (jbool_d(l, "download_hint", 0)) carry_cover(scr_json(s), l);
    open_page(link_url(s, it));
}
static void act_file(ui_screen *s, ui_item *it) {
    const char *u = link_url(s, it);
    if (u) dl_start(u, jstr(scr_json(s), "url"), cover_url_of(scr_json(s), cJSON_GetArrayItem(jarr(scr_json(s), "links"), it->iarg)));
}

static void form_done(const char *text, const char *tmpl) {
    static const char SAFE[] = "-_.~";
    size_t n = strlen(text), tl = strlen(tmpl);
    char *q = malloc(n * 3 + 1), *out = NULL;
    if (!q) return;
    char *w = q;
    for (const char *c = text; *c; c++) {     /* như quote_plus: cách -> +, giữ A-Za-z0-9_.-~, còn lại %XX của byte UTF-8 */
        unsigned char u = (unsigned char)*c;
        if (u == ' ') *w++ = '+';
        else if ((u >= 'A' && u <= 'Z') || (u >= 'a' && u <= 'z') || (u >= '0' && u <= '9') || (u && strchr(SAFE, u))) *w++ = (char)u;
        else w += sprintf(w, "%%%02X", u);
    }
    *w = 0;
    size_t ql = strlen(q);
    out = malloc(tl + (ql > 3 ? ql : 3) * (tl / 3 + 1) + 1);    /* mỗi {q} (3 byte) được thay bằng ql byte */
    if (out) {
        char *o = out;
        for (const char *c = tmpl; *c;) {
            if (!strncmp(c, "{q}", 3)) { memcpy(o, q, ql); o += ql; c += 3; }
            else *o++ = *c++;
        }
        *o = 0;
        open_page(out);
    }
    free(out);
    free(q);
}
static void act_form(ui_screen *s, ui_item *it) {
    (void)s;
    char title[96];
    cut_utf8(title, sizeof title, it->arg2, 60);
    ui_osk(title, "", 100, form_done, it->arg);
}

static void bm_add(ui_screen *s, ui_item *it) {
    (void)it;
    const char *url = jstrd(s->d, "url", ""), *t = jstrd(s->d, "title", "");
    cli_task(tr("Đang lưu dấu trang"), SHORT_TIMEOUT_MS, simple_done, tr("Lưu dấu trang"), "web", "bookmark", "add", url, "--title", t[0] ? t : jstrd(s->d, "host", url), NULL);
}

/* Nhóm liên kết trên màn trang. Engine cũ không có "zone" -> coi như "main". Link file luôn thuộc nhóm G_FILE. */
enum { G_FILE, G_PAGE, G_EXT, G_NAV };
static int link_group(cJSON *l) {
    if (jbool_d(l, "file", 0)) return G_FILE;
    if (!strcmp(jstrd(l, "zone", "main"), "nav")) return G_NAV;
    return jbool_d(l, "same_site", 0) ? G_PAGE : G_EXT;
}

/* chứa kim (ASCII không phân biệt hoa thường; byte UTF-8 so nguyên); kim rỗng = khớp */
static int lc(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static int ci_has(const char *hay, const char *needle) {
    size_t nl = strlen(needle);
    if (!nl) return 1;
    for (; *hay; hay++) {
        size_t k = 0;
        while (k < nl && hay[k] && lc((unsigned char)hay[k]) == lc((unsigned char)needle[k])) k++;
        if (k == nl) return 1;
    }
    return 0;
}
static int link_visible(cJSON *l, const char *filter) {
    return ci_has(jstrd(l, "text", ""), filter) || ci_has(jstrd(l, "url", ""), filter);
}

/* Bộ lọc nằm trong s->key của màn trang; màn con chép lại lúc mở (bộ lọc không đổi khi màn con đang mở); s->ia của màn con = nhóm. */
static void open_group(ui_screen *s, ui_item *it);
static void build_links(ui_screen *s, int group) {
    cJSON *l, *links = jarr(scr_json(s), "links");
    int i = 0;
    if (group == G_EXT) ui_hdr(s, tr("Liên kết dẫn sang trang web khác. Kiểm tra địa chỉ trước khi mở."));
    if (group == G_NAV) ui_hdr(s, tr("Menu, đăng nhập, danh mục… của trang. Kiểm tra địa chỉ trước khi mở."));
    if (group != G_PAGE && s->key[0]) ui_hdr(s, tr("Lọc: %s"), s->key);
    cJSON_ArrayForEach(l, links) {
        int idx = i++;
        if (link_group(l) != group || !link_visible(l, s->key)) continue;
        const char *url = jstrd(l, "url", ""), *tx = jstrd(l, "text", "");
        ui_item *it = ui_add(s, tx[0] ? tx : url, jbool_d(l, "download_hint", 0) ? tr("tải?") : "", ST_UNKNOWN);
        if (!it) return;
        snprintf(it->img, sizeof it->img, "%s", jstrd(l, "thumb", ""));
        ui_exp(it, "%s", url);
        ui_exp(it, tr("A — mở trang này."));
        it->iarg = idx;
        ui_act(it, 0, tr("MỞ"), act_link);
        ui_act(it, 1, tr("VỀ ĐẦU"), act_close_all_pages);
    }
}
static void build_group(ui_screen *s) { build_links(s, s->ia); }
static void open_group(ui_screen *s, ui_item *it) {
    prune_web_stack();
    ui_screen *c = ui_screen_new(it->iarg == G_NAV ? tr("MENU & ĐIỀU HƯỚNG") : tr("LIÊN KẾT RA TRANG KHÁC"), build_group);
    if (!c) return;
    c->d = cJSON_Duplicate(scr_json(s), 1);
    c->ia = it->iarg;
    snprintf(c->key, sizeof c->key, "%s", s->key);
    ui_push(c);
}

static void build_page(ui_screen *s);
static void filter_done(const char *text, const char *arg) {
    (void)arg;
    ui_screen *s = ui_top();     /* bàn phím đã đóng: màn trên cùng là màn trang đã mở nó */
    if (!s || s->build != build_page) return;
    snprintf(s->key, sizeof s->key, "%s", text);
    ui_rebuild(s);
}
static void act_filter(ui_screen *s, ui_item *it) {
    (void)it;
    ui_osk(tr("Lọc liên kết theo chữ"), s->key, 60, filter_done, "");
}
static void act_unfilter(ui_screen *s, ui_item *it) {
    (void)it;
    s->key[0] = 0;
    ui_rebuild(s);
}

static void build_page(ui_screen *s) {
    cJSON *d = s->d, *l, *links = jarr(d, "links"), *f, *cnt = jget(d, "counts");
    int n[4] = {0, 0, 0, 0}, nall = 0;
    const char *flt = s->key;
    cJSON_ArrayForEach(l, links) { nall++; if (link_visible(l, flt)) n[link_group(l)]++; }
    int nf = n[G_FILE], np = n[G_PAGE], ne = n[G_EXT], nn = n[G_NAV], nvis = nf + np + ne + nn;
    /* ui_hdr chỉ vẽ tối đa 5 dòng sau khi bẻ (lm_ui.c): host + số đếm dồn một dòng; cảnh báo quan trọng đứng trước, tin phụ (link bị ẩn) đứng cuối */
    if (flt[0]) {
        ui_hdr(s, "%s", jstrd(d, "host", "?"));
        ui_hdr(s, tr("Lọc: %s — còn %d link"), flt, nvis);
    } else ui_hdr(s, tr("%s · %.0f file · %.0f trang · %.0f liên kết ngoài"), jstrd(d, "host", "?"), jnumd(cnt, "files", nf), jnumd(cnt, "pages", np), jnumd(cnt, "external", ne));
    if (jbool_d(d, "js_download", 0) && jnumd(cnt, "files", nf) == 0)
        ui_hdr(s, tr("! Nút tải của trang này dùng JavaScript/đếm ngược — LoongMate không tải được từ đây. Thử mở các link có nhãn 'tải?', hoặc tải trên PC rồi dùng Thư viện riêng."));
    if (jbool_d(d, "truncated", 0)) ui_hdr(s, tr("! Trang rất dài: chỉ hiện một phần các liên kết."));
    if (jnumd(d, "dropped_insecure", 0) > 0) ui_hdr(s, tr("! Đã ẩn %.0f liên kết không an toàn (không phải https)."), jnumd(d, "dropped_insecure", 0));
    if (jnumd(d, "hidden_junk", 0) > 0) ui_hdr(s, tr("Đã ẩn %.0f link quảng cáo/mạng xã hội."), jnumd(d, "hidden_junk", 0));

    ui_item *it;
    int ng = gallery_count(s);
    if (jstrd(d, "next", "")[0]) {
        it = ui_add(s, tr("Trang sau ›"), "R1", ST_GREEN);
        if (it) {
            ui_exp(it, tr("Mở trang kế tiếp của danh sách này (R1 ở bất kỳ đâu trên trang)."));
            ui_exp(it, "%s", jstrd(d, "next", ""));
            ui_act(it, 0, tr("SAU"), act_next);
        }
    }
    if (jstrd(d, "prev", "")[0]) {
        it = ui_add(s, tr("‹ Trang trước"), "L1", ST_UNKNOWN);
        if (it) {
            ui_exp(it, tr("Quay lại trang trước của danh sách này (L1 ở bất kỳ đâu trên trang)."));
            ui_exp(it, "%s", jstrd(d, "prev", ""));
            ui_act(it, 0, tr("TRƯỚC"), act_prev);
        }
    }
    if (ng >= 1) {
        char lb[96];
        snprintf(lb, sizeof lb, tr("Lưới ảnh bìa (%d)"), ng);
        it = ui_add(s, lb, "Y", ST_GREEN);
        if (it) {
            ui_exp(it, tr("Hiện các game có ảnh bìa thành lưới ô (ảnh + tên). Y — chuyển qua lại giữa lưới và danh sách."));
            ui_act(it, 0, tr("XEM LƯỚI"), act_gallery);
        }
    }
    cJSON_ArrayForEach(f, jarr(d, "forms")) {
        const char *tmpl = jstrd(f, "template", ""), *lab = jstrd(f, "label", tr("Tìm kiếm"));
        if (strlen(tmpl) >= UI_ARGLEN - 1 || !strstr(tmpl, "{q}")) continue;    /* mẫu quá dài cho tham số callback / không có {q}: bỏ, không đoán */
        char lb[200];
        snprintf(lb, sizeof lb, tr("Tìm kiếm: %s"), lab);
        it = ui_add(s, lb, "", ST_UNKNOWN);
        if (!it) return;
        ui_exp(it, tr("Nhập chữ cần tìm bằng bàn phím; kết quả mở như một trang mới (B quay lại)."));
        ui_arg(it, tmpl);
        snprintf(it->arg2, sizeof it->arg2, "%s", lab);
        ui_act(it, 0, tr("TÌM"), act_form);
    }
    if (nall) {
        char lb[96];
        if (flt[0]) snprintf(lb, sizeof lb, tr("Lọc: %s"), flt);
        else snprintf(lb, sizeof lb, tr("Lọc liên kết…"));
        it = ui_add(s, lb, "", ST_UNKNOWN);
        if (it) {
            ui_exp(it, tr("Chỉ hiện các link có chữ này trong tên hoặc địa chỉ (không phân biệt hoa/thường). Áp dụng cho mọi nhóm."));
            ui_exp(it, tr("A — nhập chữ lọc bằng bàn phím."));
            ui_act(it, 0, tr("LỌC"), act_filter);
        }
        if (flt[0]) {
            it = ui_add(s, tr("Bỏ lọc"), "", ST_UNKNOWN);
            if (it) {
                ui_exp(it, tr("Hiện lại tất cả liên kết."));
                ui_act(it, 0, tr("BỎ"), act_unfilter);
            }
        }
    }
    it = ui_add(s, tr("Lưu dấu trang"), "", ST_UNKNOWN);
    if (it) {
        ui_exp(it, tr("Lưu địa chỉ trang này vào Dấu trang ở màn Tải từ web."));
        ui_exp(it, "%s", jstrd(d, "url", ""));
        ui_act(it, 0, tr("LƯU"), bm_add);
    }
    it = ui_add(s, tr("Về trang chủ Web"), "", ST_YELLOW);
    if (it) {
        ui_exp(it, tr("Đóng toàn bộ các trang đang duyệt và quay thẳng về màn hình đầu của Tải từ web."));
        ui_exp(it, tr("A — về trang chủ Web ngay lập tức (không cần bấm B nhiều lần)."));
        ui_act(it, 0, tr("VỀ ĐẦU"), act_close_all_pages);
    }
    if (nf) {
        ui_head(s, tr("File tải được (%d)"), nf);
        int i = 0;
        cJSON_ArrayForEach(l, links) {
            int idx = i++;
            if (link_group(l) != G_FILE || !link_visible(l, flt)) continue;
            const char *url = jstrd(l, "url", ""), *tx = jstrd(l, "text", "");
            it = ui_add(s, tx[0] ? tx : url, jstrd(l, "ext", ""), ST_UNKNOWN);
            if (!it) return;
            /* file trên trang chi tiết game: ảnh của chính link, không có thì ảnh đại diện của trang (og:image) */
            snprintf(it->img, sizeof it->img, "%s", jstrd(l, "thumb", "")[0] ? jstrd(l, "thumb", "") : jstrd(d, "image_thumb", ""));
            ui_exp(it, "%s", url);
            ui_exp(it, tr("A — tải về: kiểm tra với máy chủ trước, hỏi xác nhận, rồi cài bằng Nhập game."));
            it->iarg = idx;
            ui_act(it, 0, tr("TẢI"), act_file);
            ui_act(it, 1, tr("VỀ ĐẦU"), act_close_all_pages);
        }
    }
    if (np) {
        ui_head(s, tr("Liên kết trong trang (%d)"), np);
        build_links(s, G_PAGE);
    }
    if (ne) {
        char lb[96];
        snprintf(lb, sizeof lb, tr("Liên kết ra trang khác (%d) >"), ne);
        it = ui_add(s, lb, "", ST_UNKNOWN);
        if (it) {
            ui_exp(it, tr("Các liên kết dẫn sang trang web khác (không cùng máy chủ). A — xem danh sách."));
            it->iarg = G_EXT;
            ui_act(it, 0, "XEM", open_group);
            ui_act(it, 1, tr("VỀ ĐẦU"), act_close_all_pages);
        }
    }
    if (nn) {
        char lb[96];
        snprintf(lb, sizeof lb, tr("Menu & điều hướng (%d) >"), nn);
        it = ui_add(s, lb, "", ST_UNKNOWN);
        if (it) {
            ui_exp(it, tr("Link trong menu, đầu/chân trang (đăng nhập, danh mục…). A — xem danh sách."));
            it->iarg = G_NAV;
            ui_act(it, 0, "XEM", open_group);
            ui_act(it, 1, tr("VỀ ĐẦU"), act_close_all_pages);
        }
    }
    if (!nvis) ui_head(s, flt[0] ? tr("Không có link nào khớp bộ lọc") : tr("Trang không có link hay file nào"));
    /* trang chi tiết một game (ít ảnh, có og:image): ảnh bìa của trang hiện ở khung giải thích cho mọi mục không có ảnh riêng */
    const char *pimg = jstrd(d, "image_thumb", "");
    if (pimg[0] && ng < GALLERY_MIN)
        for (int i = 0; i < s->n; i++)
            if (!s->items[i]->head && !s->items[i]->img[0]) snprintf(s->items[i]->img, sizeof s->items[i]->img, "%s", pimg);
}

/* ============================================================ lưới ảnh bìa (chế độ của màn trang: s->ia = 1) */
/* Ô = link có ảnh (file tải được hoặc trang trong cùng trang web, không phải menu), theo bộ lọc hiện tại. */
/* Ô TẢI của lưới: file tải thẳng, hoặc link "Download/Tải" (engine đã bỏ link quảng cáo: rel=sponsored, máy chủ quảng cáo/mạng
 * xã hội — y như danh sách). Link "tải?" sang trang web KHÁC chỉ dùng khi trang không có link tải nào cùng trang web.
 * Tải thật vẫn qua bước hỏi máy chủ + hộp xác nhận (mặc định Huỷ) có MÁY CHỦ THẬT, như danh sách. */
static int dl_kind(ui_screen *s, cJSON *l) {      /* 0 không phải, 1 file, 2 link tải cùng trang, 3 link tải trang khác */
    if (!link_visible(l, s->key)) return 0;
    if (jbool_d(l, "file", 0)) return 1;
    if (!jbool_d(l, "download_hint", 0) || !strcmp(jstrd(l, "zone", "main"), "nav")) return 0;
    return jbool_d(l, "same_site", 0) ? 2 : 3;
}
static int page_has_local_dl(ui_screen *s) {
    cJSON *l;
    cJSON_ArrayForEach(l, jarr(s->d, "links")) { int k = dl_kind(s, l); if (k == 1 || k == 2) return 1; }
    return 0;
}
static int is_dl_tile(ui_screen *s, cJSON *l, int local) { int k = dl_kind(s, l); return k == 1 || k == 2 || (k == 3 && !local); }

static int gallery_links(ui_screen *s, int *out, int max) {
    cJSON *l;
    int i = 0, n = 0, local = page_has_local_dl(s);
    cJSON_ArrayForEach(l, jarr(s->d, "links")) {          /* ô tải đứng đầu lưới (ô đang chọn khi mở trang) */
        int idx = i++;
        if (!is_dl_tile(s, l, local)) continue;
        if (out && n < max) out[n] = idx;
        n++;
    }
    i = 0;
    cJSON_ArrayForEach(l, jarr(s->d, "links")) {
        if (is_dl_tile(s, l, local)) { i++; continue; }
        int idx = i++;
        int g = link_group(l);
        const char *th = jstrd(l, "thumb", "");
        if ((g != G_FILE && g != G_PAGE) || !th[0] || !link_visible(l, s->key)) continue;
        int dup = 0;                    /* cùng một ảnh cho nhiều link (thẻ/tag ở lề trang): chỉ giữ ô đầu */
        cJSON *m;
        int k = 0;
        cJSON_ArrayForEach(m, jarr(s->d, "links")) {
            if (k++ >= idx) break;
            int gm = link_group(m);
            if ((gm == G_FILE || gm == G_PAGE) && !strcmp(jstrd(m, "thumb", ""), th) && link_visible(m, s->key)) { dup = 1; break; }
        }
        if (dup) continue;
        if (out && n < max) out[n] = idx;
        n++;
    }
    return n;
}
static int gallery_count(ui_screen *s) {          /* số ô CÓ ẢNH (quyết định mở dạng lưới); ô tải không tính */
    if (!s->d) return 0;
    int n = gallery_links(s, NULL, 0), local = page_has_local_dl(s), d = 0;
    cJSON *l;
    cJSON_ArrayForEach(l, jarr(s->d, "links")) d += is_dl_tile(s, l, local);
    return n - d;
}

typedef struct { int cols, rows, cw, ch, ih, gap, y0; } grid_t;
static grid_t grid_of(SDL_Rect a, int hdr_h) {
    TTF_Font *fh = cat_get_font(CAT_FONT_TINY);
    grid_t g;
    g.gap = cat_device_scale(8);
    g.cols = a.w / cat_device_scale(110);
    if (g.cols < 3) g.cols = 3;
    if (g.cols > 6) g.cols = 6;
    g.cw = (a.w - g.gap * (g.cols - 1)) / g.cols;
    int cap_h = 2 * TTF_FontHeight(fh) + cat_device_scale(6);
    int body = a.h - hdr_h;
    int two = (body - 2 * g.gap) / 2 - cap_h;             /* cao ảnh để vừa 2 hàng */
    g.ih = g.cw * 5 / 4;
    if (g.ih > two && two > g.cw / 2) g.ih = two;         /* ưu tiên thấy 2 hàng (ảnh vẫn giữ tỉ lệ trong ô) */
    if (g.ih + cap_h + g.gap > body) g.ih = body - cap_h - g.gap;
    if (g.ih < cat_device_scale(40)) g.ih = cat_device_scale(40);
    g.ch = g.ih + cap_h;
    g.rows = body / (g.ch + g.gap);
    if (g.rows < 1) g.rows = 1;
    g.y0 = a.y + hdr_h;
    return g;
}

/* file ảnh chưa có và cũng chưa bị đánh dấu hỏng (<tên>.bad) -> còn chờ tải */
static int thumb_missing(const char *path) {
    char bad[300];
    size_t n = strlen(path);
    if (!n || n + 1 >= sizeof bad || access(path, F_OK) == 0) return 0;
    snprintf(bad, sizeof bad, "%s", path);
    char *dot = strrchr(bad, '.');
    if (dot) strcpy(dot, ".bad");
    return access(bad, F_OK) != 0;
}

static const char *link_host(const char *url) {   /* "https://a.b/c" -> "a.b" (bộ đệm tĩnh) */
    static char h[120];
    const char *p = strstr(url, "://");
    p = p ? p + 3 : url;
    size_t n = strcspn(p, "/?#");
    if (n >= sizeof h) n = sizeof h - 1;
    memcpy(h, p, n);
    h[n] = 0;
    return h;
}

static int g_last_hdr_h;
static SDL_Rect g_last_area;
static void gallery_draw(ui_screen *s, SDL_Rect a) {
    cat_theme *th = cat_get_theme();
    TTF_Font *fh = cat_get_font(CAT_FONT_TINY);
    static int idx[MAX_LINKS_GRID];
    int n = gallery_links(s, idx, MAX_LINKS_GRID);
    if (n > MAX_LINKS_GRID) n = MAX_LINKS_GRID;
    char hdr[240];
    int local = page_has_local_dl(s), ndl = 0;
    for (int k = 0; k < n && k < MAX_LINKS_GRID; k++) ndl += is_dl_tile(s, cJSON_GetArrayItem(jarr(s->d, "links"), idx[k]), local);
    /* nhiều ô tải cùng tên game, chỉ khác bản/khu vực ("Game (USA)", "Game (Europe)..."): bỏ phần đầu chung để ô hiện chỗ khác nhau */
    size_t common = 0;
    if (ndl >= 2) {
        const char *first = jstrd(cJSON_GetArrayItem(jarr(s->d, "links"), idx[0]), "text", "");
        common = strlen(first);
        for (int k = 1; k < ndl; k++) {
            const char *t = jstrd(cJSON_GetArrayItem(jarr(s->d, "links"), idx[k]), "text", "");
            size_t m = 0;
            while (m < common && t[m] && t[m] == first[m]) m++;
            common = m;
        }
        while (common > 0 && first[common - 1] != ' ') common--;          /* cắt ở ranh giới từ (luôn là ASCII: an toàn UTF-8) */
        for (int k = 0; k < ndl && common; k++)
            if (strlen(jstrd(cJSON_GetArrayItem(jarr(s->d, "links"), idx[k]), "text", "")) <= common) common = 0;
        if (common < 8) common = 0;
    }
    if (ndl && n > ndl) snprintf(hdr, sizeof hdr, tr("%s · %d mục tải (đầu lưới) · %d game có ảnh · Y: danh sách"), jstrd(s->d, "host", "?"), ndl, n - ndl);
    else if (ndl) snprintf(hdr, sizeof hdr, tr("%s · %d mục tải · A: tải · Y: danh sách"), jstrd(s->d, "host", "?"), ndl);
    else if (s->key[0]) snprintf(hdr, sizeof hdr, tr("%s · %d game có ảnh · lọc: %s · Y: danh sách"), jstrd(s->d, "host", "?"), n, s->key);
    else snprintf(hdr, sizeof hdr, tr("%s · %d game có ảnh · Y: danh sách, tìm kiếm, lọc"), jstrd(s->d, "host", "?"), n);
    int hh = TTF_FontHeight(fh) + cat_device_scale(8);
    g_last_hdr_h = hh;
    g_last_area = a;
    cat_draw_text_ellipsized(fh, hdr, a.x, a.y, th->hint, a.w);
    s->nfooter = 0;
    /* chân màn tối đa 4 nút cho vừa màn: B, (L1 trước), (R1 sau), rồi Y/X nếu còn chỗ, A; "Y: danh sách" đã ghi ở dòng đầu */
    s->footer[s->nfooter++] = (cat_footer_item){ .button = CAT_BTN_B, .label = tr("QUAY LẠI") };
    if (jstrd(s->d, "prev", "")[0]) s->footer[s->nfooter++] = (cat_footer_item){ .button = CAT_BTN_L1, .label = tr("TRƯỚC") };
    if (jstrd(s->d, "next", "")[0]) s->footer[s->nfooter++] = (cat_footer_item){ .button = CAT_BTN_R1, .label = tr("SAU") };
    if (s->nfooter < 3) s->footer[s->nfooter++] = (cat_footer_item){ .button = CAT_BTN_Y, .label = tr("DANH SÁCH") };
    if (!n) { cat_draw_text(fh, tr("Không có game nào có ảnh (khớp bộ lọc)."), a.x, a.y + hh, th->hint); return; }
    grid_t g = grid_of(a, hh);
    if (s->gsel >= n) s->gsel = n - 1;
    if (s->gsel < 0) s->gsel = 0;
    int row = s->gsel / g.cols;
    if (row < s->gtop) s->gtop = row;
    if (row >= s->gtop + g.rows) s->gtop = row - g.rows + 1;
    if (s->gtop < 0) s->gtop = 0;
    int missing = 0;
    for (int k = s->gtop * g.cols; k < n && k < (s->gtop + g.rows) * g.cols; k++) {
        cJSON *l = cJSON_GetArrayItem(jarr(s->d, "links"), idx[k]);
        int c = k % g.cols, r = k / g.cols - s->gtop;
        int x = a.x + c * (g.cw + g.gap), y = g.y0 + r * (g.ch + g.gap);
        int sel = k == s->gsel, bd = cat_device_scale(3);
        if (sel) cat_draw_rounded_rect(x - bd, y - bd, g.cw + 2 * bd, g.ch + 2 * bd, cat_device_scale(8), th->highlight);
        cat_draw_rounded_rect(x, y, g.cw, g.ch, cat_device_scale(6), th->background);
        SDL_Rect box = { x + cat_device_scale(4), y + cat_device_scale(4), g.cw - cat_device_scale(8), g.ih - cat_device_scale(6) };
        int dl = is_dl_tile(s, l, local);
        /* ô tải không có ảnh riêng: dùng ảnh bìa của trang (og:image); không có nữa thì chữ TẢI VỀ to */
        const char *img = jstrd(l, "thumb", "")[0] ? jstrd(l, "thumb", "") : dl ? jstrd(s->d, "image_thumb", "") : "";
        if (dl && !img[0]) {
            TTF_Font *fb = cat_get_font(CAT_FONT_LARGE);
            const char *w = tr("TẢI VỀ");
            int tw = cat_measure_text(fb, w);
            cat_draw_rounded_rect(box.x, box.y, box.w, box.h, cat_device_scale(6), th->highlight);
            cat_draw_text(fb, w, box.x + (box.w - tw) / 2, box.y + (box.h - TTF_FontHeight(fb)) / 2, th->highlighted_text);
        } else if (!ui_thumb_draw(img, box)) {
            if (thumb_missing(img)) missing++;
            const char *w = tr("đang tải ảnh…");
            int tw = cat_measure_text(fh, w);
            if (tw > box.w) tw = box.w;
            cat_draw_text_ellipsized(fh, w, box.x + (box.w - tw) / 2, box.y + box.h / 2 - TTF_FontHeight(fh) / 2, th->hint, box.w);
        }
        lines_t t = {0};
        char cap[260];
        if (dl && dl_kind(s, l) == 3)          /* link tải sang trang khác: ghi rõ máy chủ đích */
            snprintf(cap, sizeof cap, "%s → %s", jstrd(l, "text", "?"), link_host(jstrd(l, "url", "")));
        else if (dl && common) {
            const char *rest = jstrd(l, "text", "?") + common;
            while (*rest == ' ' || *rest == '-') rest++;
            snprintf(cap, sizeof cap, "%s", rest);
        } else snprintf(cap, sizeof cap, "%s", jstrd(l, "text", "?"));
        ui_wrap(fh, cap, g.cw - cat_device_scale(8), &t);
        cat_draw_color tc = sel ? th->text : th->hint;
        int ty = y + g.ih;
        for (int i = 0; i < t.n && i < 2; i++) {
            char line[220];
            if (i == 1 && t.n > 2) snprintf(line, sizeof line, "%s…", t.v[i]); else snprintf(line, sizeof line, "%s", t.v[i]);
            cat_draw_text_ellipsized(fh, line, x + cat_device_scale(4), ty, tc, g.cw - cat_device_scale(8));
            ty += TTF_FontHeight(fh);
        }
        lines_free(&t);
        if (dl) {                             /* nhãn ở góc: file tải thẳng (đuôi file) hoặc link dẫn tới trang tải */
            const char *e = jbool_d(l, "file", 0) ? (jstrd(l, "ext", "")[0] ? jstrd(l, "ext", "") : "file") : tr("TẢI");
            int ew = cat_measure_text(fh, e) + cat_device_scale(10);
            cat_draw_pill(x + g.cw - ew - cat_device_scale(4), y + cat_device_scale(4), ew, TTF_FontHeight(fh) + cat_device_scale(2), th->highlight);
            cat_draw_text(fh, e, x + g.cw - ew + cat_device_scale(1), y + cat_device_scale(5), th->highlighted_text);
        }
    }
    /* quay về trang mà ảnh chưa tải xong (job đã dừng khi sang trang khác): xin tải tiếp, trang này trước */
    if (missing && !g_thumbs && g_thumbs_for != s) thumbs_start(s, jstrd(s->d, "url", ""));
    int total_rows = (n + g.cols - 1) / g.cols;
    if (total_rows > g.rows) cat_draw_scrollbar(a.x + a.w - cat_device_scale(4), g.y0, g.rows * (g.ch + g.gap), g.rows, total_rows, s->gtop);
    cJSON *cur = cJSON_GetArrayItem(jarr(s->d, "links"), idx[s->gsel]);
    if (s->nfooter < 3) s->footer[s->nfooter++] = (cat_footer_item){ .button = CAT_BTN_X, .label = tr("VỀ ĐẦU") };
    s->footer[s->nfooter++] = (cat_footer_item){ .button = CAT_BTN_A, .label = is_dl_tile(s, cur, local) ? tr("TẢI") : tr("MỞ"), .is_confirm = true };
}

static void set_mode(ui_screen *s, int gallery) {
    s->ia = gallery;
    s->draw = gallery ? gallery_draw : NULL;
    if (!gallery) s->nfooter = 0;
}
static void act_gallery(ui_screen *s, ui_item *it) { (void)it; set_mode(s, 1); }

/* Phân trang: R1 trang sau, L1 trang trước (engine dò rel=next/prev, chữ/class của link, số trang trong địa chỉ).
 * L1 khi màn ngay dưới chính là trang trước -> chỉ đóng màn này (không tải lại, giữ vị trí đã chọn). */
static void go_page(ui_screen *s, const char *kind) {
    const char *u = jstrd(s->d, kind, "");
    if (!u[0]) return;
    int d = ui_depth();
    if (!strcmp(kind, "prev") && d >= 2) {
        ui_screen *below = ui_screen_at(d - 2);
        if (below && below->build == build_page && !strcmp(jstrd(below->d, "url", ""), u)) { ui_pop(); return; }
    }
    open_page(u);
}
static void act_next(ui_screen *s, ui_item *it) { (void)it; go_page(s, "next"); }
static void act_prev(ui_screen *s, ui_item *it) { (void)it; go_page(s, "prev"); }

static int page_key(ui_screen *s, cat_input_event *ev) {
    if ((ev->button == CAT_BTN_R1 || ev->button == CAT_BTN_L1) && jstrd(s->d, ev->button == CAT_BTN_R1 ? "next" : "prev", "")[0]) {
        if (!ev->repeated) go_page(s, ev->button == CAT_BTN_R1 ? "next" : "prev");
        return 1;
    }
    if (!s->ia) {                      /* danh sách: Y = sang lưới (mục của màn trang không dùng Y) */
        if (ev->button == CAT_BTN_Y && !ev->repeated && gallery_count(s) >= 1) { set_mode(s, 1); return 1; }
        return 0;
    }
    static int idx[MAX_LINKS_GRID];
    int n = gallery_links(s, idx, MAX_LINKS_GRID);
    if (n > MAX_LINKS_GRID) n = MAX_LINKS_GRID;
    grid_t g = grid_of(g_last_area.w ? g_last_area : cat_get_content_rect(true, true, true), g_last_hdr_h);
    int d = 0;
    switch (ev->button) {
    case CAT_BTN_LEFT: d = -1; break;
    case CAT_BTN_RIGHT: d = 1; break;
    case CAT_BTN_UP: d = -g.cols; break;
    case CAT_BTN_DOWN: d = g.cols; break;
    case CAT_BTN_L1: d = -g.cols * g.rows; break;
    case CAT_BTN_R1: d = g.cols * g.rows; break;
    default: break;
    }
    if (d) {
        int ns = s->gsel + d;
        if (ns < 0) ns = d == -1 ? 0 : s->gsel % g.cols;          /* lên quá đầu: ở hàng đầu, giữ cột */
        if (ns >= n) ns = n ? n - 1 : 0;                          /* xuống quá cuối: ô cuối */
        s->gsel = ns;
        return 1;
    }
    if (ev->repeated) return 1;
    if (ev->button == CAT_BTN_B) { ui_pop(); return 1; }
    if (ev->button == CAT_BTN_Y) { set_mode(s, 0); return 1; }
    if (ev->button == CAT_BTN_X) { act_close_all_pages(s, NULL); return 1; }
    if (ev->button == CAT_BTN_A && n) {
        cJSON *l = cJSON_GetArrayItem(jarr(s->d, "links"), idx[s->gsel]);
        const char *u = jstr(l, "url");
        if (!u) return 1;
        if (jbool_d(l, "file", 0)) dl_start(u, jstr(s->d, "url"), cover_url_of(s->d, l));
        else {
            if (is_dl_tile(s, l, page_has_local_dl(s))) carry_cover(s->d, l);
            open_page(u);
        }
    }
    return 1;
}

/* ============================================================ màn đầu */
static void addr_done(const char *text, const char *arg) {
    (void)arg;
    char *u = NULL;
    const char *rest = strstr(text, "://");
    rest = rest ? rest + 3 : text;
    if (!rest[0]) { ui_info1(tr("Tải từ web"), tr("Chưa nhập địa chỉ.")); return; }
    if (asprintf(&u, "%s%s", strstr(text, "://") ? "" : "https://", text) < 0) return;
    open_page(u);
    free(u);
}
static void act_addr(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    ui_osk(tr("Nhập địa chỉ trang web"), "https://", 500, addr_done, "");
}

static void act_open_item(ui_screen *s, ui_item *it) {   /* it->arg2 = tên mảng trong JSON (inbox/bookmarks/history) */
    open_page(jstr(cJSON_GetArrayItem(jarr(s->d, it->arg2), it->iarg), "url"));
}

static void do_bm_remove(const char *arg) {
    (void)arg;
    cli_task(tr("Đang xoá dấu trang"), SHORT_TIMEOUT_MS, simple_done, tr("Xoá dấu trang"), "web", "bookmark", "remove", cur_url(), NULL);
}
static void ask_bm_remove(ui_screen *s, ui_item *it) {
    const char *url = jstr(cJSON_GetArrayItem(jarr(s->d, "bookmarks"), it->iarg), "url");
    if (!url) return;
    set_url(url);
    lines_t L = {0};
    lines_add(&L, tr("Xoá dấu trang này?"));
    lines_add(&L, "%s", it->label);
    lines_add(&L, "%s", url);
    ui_confirm(tr("Xoá dấu trang"), &L, tr("Xoá"), do_bm_remove, "");
}

static void do_hist_clear(const char *arg) {
    (void)arg;
    cli_task(tr("Đang xoá lịch sử"), SHORT_TIMEOUT_MS, simple_done, tr("Xoá lịch sử"), "web", "history", "clear", NULL);
}
static void ask_hist_clear(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    lines_t L = {0};
    lines_add(&L, tr("Xoá toàn bộ lịch sử các trang đã mở? Dấu trang không bị ảnh hưởng."));
    ui_confirm(tr("Xoá lịch sử"), &L, tr("Xoá lịch sử"), do_hist_clear, "");
}

static void do_resume(const char *arg) {
    if (cli_task(tr("Đang tải & cài"), LONG_TIMEOUT_MS, dl_fetch_done, "", "web", "resume", arg, "--yes", "--progress", NULL)) ui_task_cancelable(1);
}
static void ask_resume(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Tiếp tục job %s: %s."), it->arg, it->arg2);
    lines_add(&L, tr("Tải tiếp phần còn lại, kiểm tra rồi cài bằng Nhập game."));
    lines_add(&L, tr("B trong lúc tải = tạm dừng (tải tiếp được sau)."));
    ui_confirm(tr("Tiếp tục tải"), &L, tr("Tiếp tục"), do_resume, it->arg);
}
static void do_cancel(const char *arg) {
    cli_task(tr("Đang huỷ job"), SHORT_TIMEOUT_MS, simple_done, tr("Huỷ job tải"), "web", "cancel", arg, "--yes", NULL);
}
static void ask_cancel(ui_screen *s, ui_item *it) {
    (void)s;
    lines_t L = {0};
    lines_add(&L, tr("Xoá job %s và mọi file tải dở?"), it->arg);
    ui_confirm(tr("Huỷ job tải"), &L, tr("Huỷ job"), do_cancel, it->arg);
}

static const char *card_vi(const char *c) { return !strcmp(c, "primary") ? "SD1" : !strcmp(c, "secondary") ? "SD2" : c; }

static void add_url_items(ui_screen *s, const char *key, int with_remove) {
    cJSON *x;
    int i = 0;
    cJSON_ArrayForEach(x, jarr(s->d, key)) {
        const char *url = jstrd(x, "url", ""), *tt = jstrd(x, "title", ""), *cd = jstr(x, "card");
        ui_item *it = ui_add(s, tt[0] ? tt : url, cd ? card_vi(cd) : "", ST_UNKNOWN);
        if (!it) return;
        ui_exp(it, "%s", url);
        ui_exp(it, with_remove ? tr("A — mở trang.  Y — xoá dấu trang.") : tr("A — mở trang."));
        it->iarg = i++;
        snprintf(it->arg2, sizeof it->arg2, "%s", key);
        ui_act(it, 0, tr("MỞ"), act_open_item);
        if (with_remove) ui_act(it, 2, tr("XOÁ"), ask_bm_remove);
    }
}

static void build_home(ui_screen *s) {
    cJSON *x;
    ui_hdr(s, tr("Mở một trang web (https), chọn file để tải và cài bằng Nhập game."));
    ui_hdr(s, tr("Chỉ tải nội dung bạn có quyền sử dụng."));
    ui_item *it = ui_add(s, tr("Nhập địa chỉ…"), "", ST_UNKNOWN);
    if (it) {
        ui_exp(it, tr("Gõ địa chỉ trang bằng bàn phím trên màn hình, rồi chọn file trong trang để tải."));
        ui_exp(it, tr("Chỉ https. File tải về được kiểm tra rồi cài bằng trình Nhập game; không tự chạy gì."));
        ui_exp(it, tr("! Trang cần JavaScript hoặc đếm ngược có thể không tải được."));
        ui_exp(it, tr("Chỉ tải nội dung bạn có quyền sử dụng."));
        ui_act(it, 0, tr("MỞ"), act_addr);
    }
    it = ui_add(s, tr("Game mới tải về…"), tr("gần đây"), ST_GREEN);
    if (it) {
        ui_exp(it, tr("Xem lại các game vừa được tải từ web và cài đặt vào máy."));
        ui_act(it, 0, tr("MỞ"), open_recent);
    }
    if (jarr_n(s->d, "inbox")) {
        ui_head(s, tr("Từ thẻ (Import/links.txt)"));
        add_url_items(s, "inbox", 0);
    }
    if (jarr_n(s->d, "bookmarks")) {
        ui_head(s, tr("Dấu trang"));
        add_url_items(s, "bookmarks", 1);
    }
    if (jarr_n(s->d, "history")) {
        ui_head(s, tr("Lịch sử"));
        add_url_items(s, "history", 0);
        it = ui_add(s, tr("Xoá lịch sử"), "", ST_YELLOW);
        if (it) {
            ui_exp(it, tr("Xoá danh sách các trang đã mở. Dấu trang giữ nguyên."));
            ui_act(it, 0, tr("XOÁ"), ask_hist_clear);
        }
    }
    if (jarr_n(s->d, "jobs")) ui_head(s, tr("Tải dở"));
    cJSON_ArrayForEach(x, jarr(s->d, "jobs")) {
        char lab[200], tag[24];
        cJSON *nm = cJSON_GetArrayItem(jarr(x, "names"), 0);
        snprintf(lab, sizeof lab, tr("Tải dở: %s%s"), cJSON_IsString(nm) ? nm->valuestring : "?", jarr_n(x, "names") > 1 ? "…" : "");
        snprintf(tag, sizeof tag, "%.0f%%", jnumd(x, "percent", 0));
        it = ui_add(s, lab, tag, ST_YELLOW);
        if (!it) return;
        ui_exp(it, "Job %s", jstrd(x, "job_id", "?"));
        ui_exp(it, tr("Mất mạng/ngủ máy giữa chừng: tiếp tục được, file đã tải được giữ."));
        ui_exp(it, tr("A — tiếp tục tải rồi cài.  X — huỷ job và xoá file tải dở."));
        ui_arg(it, jstrd(x, "job_id", ""));
        snprintf(it->arg2, sizeof it->arg2, "%.0f/%.0f file (%.0f%%)", jnumd(x, "files_done", 0), jnumd(x, "files", 0), jnumd(x, "percent", 0));
        ui_act(it, 0, tr("TIẾP TỤC"), ask_resume);
        ui_act(it, 1, tr("HUỶ JOB"), ask_cancel);
    }
}

static void home_free(ui_screen *s) { if (g_wh == s) g_wh = NULL; }

static void web_open_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = web_ok_json(j, tr("Tải từ web"));
    if (!r) return;
    ui_screen *s = ui_screen_new("TẢI TỪ WEB", build_home);
    if (!s) { cJSON_Delete(r); return; }
    s->d = r;
    s->on_free = home_free;
    g_wh = s;
    ui_push(s);
}

void open_web(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc Tải từ web"), SHORT_TIMEOUT_MS, web_open_done, "", "web", "home", NULL);
}

/* ============================================================ Game mới tải về */
static void build_recent(ui_screen *s) {
    ui_hdr(s, tr("Danh sách các game vừa tải về hoặc nhập vào thẻ nhớ gần đây."));
    cJSON *rec = jarr(s->d, "recent");
    int n = rec ? cJSON_GetArraySize(rec) : 0;
    if (!n) {
        ui_head(s, tr("(Chưa tải game nào gần đây)"));
        ui_item *it = ui_add(s, tr("Chưa có dữ liệu"), "", ST_UNKNOWN);
        if (it) ui_exp(it, tr("Khi bạn tải game từ web hoặc nhập game qua Import/, danh sách sẽ lưu tại đây."));
        return;
    }
    cJSON *x;
    cJSON_ArrayForEach(x, rec) {
        const char *name = jstrd(x, "name", "Game");
        const char *sys = jstrd(x, "system", "?");
        const char *card = jstrd(x, "card", "");
        const char *t = jstrd(x, "when", jstrd(x, "installed_at", ""));
        const char *path = jstrd(x, "target_file", "");
        double sz = jnumd(x, "size", 0);
        int gone = !jbool_d(x, "exists", 1);
        char tag[64];
        snprintf(tag, sizeof tag, "%s · %s%s", sys, card_vi(card), gone ? tr(" · đã xoá") : "");
        ui_item *it = ui_add(s, name, tag, gone ? ST_UNKNOWN : ST_GREEN);
        if (!it) continue;
        if (t[0]) ui_exp(it, tr("Thời gian tải/cài: %s"), t);
        ui_exp(it, tr("Hệ máy: %s · Thẻ: %s"), sys, card_vi(card));
        if (sz > 0) ui_exp(it, tr("Dung lượng: %s"), mbs(sz));
        if (path[0]) ui_exp(it, tr("Đường dẫn: %s"), path);
        const char *src = jstrd(x, "source", "");
        if (src[0]) ui_exp(it, tr("Nguồn: %s"), src);
        if (gone) ui_exp(it, tr("! File không còn trên thẻ (đã xoá hoặc chuyển chỗ)."));
    }
}

static void recent_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("Game mới tải về"));
    if (!r) return;
    ui_screen *s = ui_screen_new("GAME MỚI TẢI VỀ", build_recent);
    if (!s) { cJSON_Delete(r); return; }
    s->d = r;
    ui_push(s);
}

void open_recent(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang đọc game mới tải"), SHORT_TIMEOUT_MS, recent_done, "", "recent", "--limit", "50", NULL);
}

