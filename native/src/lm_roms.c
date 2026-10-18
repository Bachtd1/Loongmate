/* LoongMate native — duyệt ROM (cli.py roms --json): Xoá ROM từng game và chọn từng game để chuyển sang thẻ kia.
 * Danh sách chỉ lấy từ `roms --json` (một lần mỗi lần mở); lọc theo thẻ/hệ máy làm ngay trên dữ liệu đó.
 * Xoá = `rom delete <đường dẫn> [<đường dẫn>...]` (MỘT lần gọi cho cả lô đánh dấu bằng X; xem trước không --yes rồi --yes); chuyển = `move <rom> --to <thẻ>` (xem trước ghi kế hoạch, thực hiện trong lm_library.c).
 * Ẩn track .bin của game có .cue (chỉ từ dữ liệu trả về; xem rb_index).
 * Sắp xếp (Tên / Dung lượng) và lọc (tất cả / có save / không save) của màn game làm TRONG C trên chính dữ liệu `roms --json` đó
 * (has_save có sẵn từng dòng): không gọi lại CLI mỗi lần bấm, dấu chọn (X) nằm theo chỉ số dòng nên không mất khi đổi. */
#define _GNU_SOURCE
#include "lm_app.h"
#include "lm_i18n.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

enum { RB_DELETE = 0, RB_MOVE = 1 };
#define TOP_KEY "*"      /* khoá "hệ" của "Game lớn nhất (mọi hệ)"; không trùng tên thư mục hệ nào */
#define TOP_N 50
enum { SORT_NAME = 0, SORT_SIZE = 1 };
enum { SAVE_ALL = 0, SAVE_WITH = 1, SAVE_WITHOUT = 2 };
typedef struct { int sort, save; } gview_t;   /* trạng thái xem của MỘT màn game (s->priv) */

static cJSON *g_roms;            /* `roms --json` (sở hữu); thay đổi khi xoá xong để khỏi quét lại */
static cJSON **g_row;            /* con trỏ tới từng dòng của g_roms.roms (chỉ mục chung với g_hide) */
static unsigned char *g_hide;    /* 1 = track .bin của game có .cue, không hiện */
static unsigned char *g_mark;    /* 1 = game đánh dấu để xoá nhiều (chỉ mục chung với g_row); rb_index xoá sạch */
static int g_n, g_nhidden;

static const char *sd_name(const char *card) { return card && !strcmp(card, "primary") ? "SD1" : "SD2"; }
static const char *mbs(double bytes) { static char b[4][32]; static int i; i = (i + 1) & 3; ui_fmt_bytes(b[i], sizeof b[i], bytes); return b[i]; }

/* .bin bị ẩn khi cùng thư mục + cùng hệ có file .cue mà tên (bỏ đuôi) là tiền tố của tên .bin: "Game.cue" -> "Game.bin",
 * "Game (Track 1).bin". Chỉ là suy đoán từ tên; .bin lẻ không khớp .cue nào vẫn hiện đúng như CLI trả về. */
static void rb_index(void) {
    free(g_row); free(g_hide); free(g_mark);
    g_row = NULL; g_hide = NULL; g_mark = NULL; g_n = g_nhidden = 0;
    cJSON *arr = jarr(g_roms, "roms"), *r;
    int n = arr ? cJSON_GetArraySize(arr) : 0;
    if (!n) return;
    g_row = calloc((size_t)n, sizeof *g_row);
    g_hide = calloc((size_t)n, 1);
    g_mark = calloc((size_t)n, 1);
    int *cue = malloc((size_t)n * sizeof *cue), nc = 0;
    if (!g_row || !g_hide || !g_mark || !cue) { free(g_row); free(g_hide); free(g_mark); free(cue); g_row = NULL; g_hide = NULL; g_mark = NULL; g_n = 0; return; }
    cJSON_ArrayForEach(r, arr) {
        g_row[g_n] = r;
        if (!strcasecmp(jstrd(r, "extension", ""), ".cue")) cue[nc++] = g_n;
        g_n++;
    }
    for (int i = 0; i < g_n; i++) {
        if (strcasecmp(jstrd(g_row[i], "extension", ""), ".bin")) continue;
        const char *bp = jstrd(g_row[i], "path", ""), *bn = jstrd(g_row[i], "name", "");
        const char *bs = strrchr(bp, '/');
        size_t bdir = bs ? (size_t)(bs - bp) : 0;
        for (int k = 0; k < nc; k++) {
            cJSON *c = g_row[cue[k]];
            const char *cp = jstrd(c, "path", ""), *cn = jstrd(c, "name", ""), *cs = strrchr(cp, '/');
            size_t cdir = cs ? (size_t)(cs - cp) : 0, cl = strlen(cn);
            if (cl < 5 || cdir != bdir || strncmp(bp, cp, bdir) || strcmp(jstrd(c, "system", ""), jstrd(g_row[i], "system", ""))) continue;
            if (strncasecmp(cn, bn, cl - 4) == 0) { g_hide[i] = 1; g_nhidden++; break; }
        }
    }
    free(cue);
}

static void strv_clear_all(void);

static void roms_free(ui_screen *s) {
    (void)s;
    cJSON_Delete(g_roms);
    g_roms = NULL;
    free(g_row); free(g_hide); free(g_mark);
    g_row = NULL; g_hide = NULL; g_mark = NULL; g_n = g_nhidden = 0;
    strv_clear_all();
}

static int card_ok(const cJSON *r, int filter) {   /* 0 cả hai, 1 primary, 2 secondary */
    if (!filter) return 1;
    return !strcmp(jstrd(r, "card", ""), filter == 1 ? "primary" : "secondary");
}

/* ============================================================ màn chọn game */
static void act_game(ui_screen *s, ui_item *it);
static void act_toggle(ui_screen *s, ui_item *it);
static void act_clear_marks(ui_screen *s, ui_item *it);
static void act_delete_marked(ui_screen *s, ui_item *it);
static void del_preview_done(lm_job *j, const char *arg);
static void ask_delete(const cJSON *games);
static void build_games(ui_screen *s);

static void games_free(ui_screen *s) {     /* rời hệ máy thì bỏ đánh dấu (đánh dấu chỉ có hiệu lực trong MỘT hệ) */
    free(s->priv);
    s->priv = NULL;
    if (g_mark) memset(g_mark, 0, (size_t)g_n);
}

static gview_t *view_of(ui_screen *s) {
    static gview_t dflt;
    return s->priv ? (gview_t *)s->priv : &dflt;
}
static int in_scope(const cJSON *r, const char *key) { return !strcmp(key, TOP_KEY) || !strcmp(jstrd(r, "system", ""), key); }
static int is_top(const ui_screen *s) { return !strcmp(s->key, TOP_KEY); }

/* thứ tự giống Python rom_view_rows: Tên = tên (không phân biệt hoa/thường) rồi thẻ; Dung lượng = lớn trước, rồi tên, rồi thẻ.
 * Thêm đường dẫn làm khoá cuối để kết quả tất định (qsort không ổn định). */
static int g_cmp_size;
static int cmp_row(const void *pa, const void *pb) {
    const cJSON *a = g_row[*(const int *)pa], *b = g_row[*(const int *)pb];
    if (g_cmp_size) {
        double d = jnumd(b, "size", 0) - jnumd(a, "size", 0);
        if (d) return d < 0 ? -1 : 1;
    }
    int c = strcasecmp(jstrd(a, "name", ""), jstrd(b, "name", ""));
    if (!c) c = strcmp(jstrd(a, "card", ""), jstrd(b, "card", ""));
    if (!c) c = strcmp(jstrd(a, "path", ""), jstrd(b, "path", ""));
    return c;
}

static int marked_count(const ui_screen *s, double *bytes) {
    int filter = s->ia % 10, n = 0;
    double b = 0;
    for (int i = 0; i < g_n; i++) {
        cJSON *r = g_row[i];
        if (!g_mark || !g_mark[i] || g_hide[i] || !card_ok(r, filter) || !in_scope(r, s->key)) continue;
        n++;
        b += jnumd(r, "size", 0);
    }
    if (bytes) *bytes = b;
    return n;
}

static void act_cycle_save(ui_screen *s, ui_item *it);
static void act_toggle_sort(ui_screen *s, ui_item *it);

static const char *const SAVE_LABEL[3] = { "tất cả", "có save", "không save" };

static void build_games(ui_screen *s) {
    int mode = s->ia / 10, filter = s->ia % 10, shown = 0;
    int del = mode == RB_DELETE, top = is_top(s);
    gview_t *v = view_of(s);
    double mbytes = 0;
    int nm = del ? marked_count(s, &mbytes) : 0;

    /* các dòng sẽ hiện: trong phạm vi (hệ / mọi hệ) + thẻ, rồi (chỉ Xoá ROM) lọc save -> top 50 -> sắp xếp */
    int *idx = calloc((size_t)(g_n ? g_n : 1), sizeof *idx), nidx = 0, nscope = 0;
    if (!idx) return;
    for (int i = 0; i < g_n; i++) {
        cJSON *r = g_row[i];
        if (g_hide[i] || !card_ok(r, filter) || !in_scope(r, s->key)) continue;
        nscope++;
        if (del && v->save != SAVE_ALL && jbool_d(r, "has_save", 0) != (v->save == SAVE_WITH)) continue;
        idx[nidx++] = i;
    }
    if (del) {
        if (top && nidx > TOP_N) { g_cmp_size = 1; qsort(idx, (size_t)nidx, sizeof *idx, cmp_row); nidx = TOP_N; }
        g_cmp_size = v->sort == SORT_SIZE;
        qsort(idx, (size_t)nidx, sizeof *idx, cmp_row);
    }

    if (del) {
        const char *cardtxt = filter == 1 ? tr(" trên SD1") : filter == 2 ? tr(" trên SD2") : "";
        if (top) ui_hdr(s, tr("%d game nặng nhất%s (mọi hệ)."), nidx, cardtxt);
        else if (nidx == nscope) ui_hdr(s, tr("Hệ %s: %d game%s."), s->key, nscope, cardtxt);
        else ui_hdr(s, tr("Hệ %s: hiện %d / %d game%s."), s->key, nidx, nscope, cardtxt);
        ui_hdr(s, tr("Sắp xếp: %s · Lọc: %s."), v->sort == SORT_SIZE ? tr("Dung lượng (lớn trước)") : tr("Tên"), tr(SAVE_LABEL[v->save]));
        if (nm) {
            int vis = 0;
            for (int k = 0; k < nidx; k++) if (g_mark && g_mark[idx[k]]) vis++;
            if (nm > vis) ui_hdr(s, tr("Đã chọn %d game · %s, %d đang bị bộ lọc ẩn."), nm, mbs(mbytes), nm - vis);
            else ui_hdr(s, tr("Đã chọn %d game · %s."), nm, mbs(mbytes));
        } else ui_hdr(s, tr("Đã chọn 0 game."));
        ui_hdr(s, tr("Y: đổi sắp xếp. A ở mục Lọc: đổi lọc. X: chọn nhiều game để xoá một lần. A ở game: xoá riêng game đó."));
    } else {
        ui_hdr(s, tr("Hệ %s. A: xem kế hoạch chuyển rồi mới xác nhận (mặc định Huỷ). Chuyển nhiều game một lần: thiếu CLI (lệnh `move` chỉ nhận MỘT game mỗi kế hoạch)."), s->key);
    }
    const char *ylbl = tr("SẮP XẾP");
    if (nm >= 1) {
        char label[64];
        snprintf(label, sizeof label, tr("Xoá %d game đã chọn"), nm);
        ui_item *it = ui_add(s, label, mbs(mbytes), ST_RED);
        it->iarg = -1;
        ui_exp(it, tr("Xoá %d game đã đánh dấu (%s)%s."), nm, mbs(mbytes), top ? tr(" trong danh sách mọi hệ") : tr(" trong hệ này"));
        ui_exp(it, tr("Gồm cả game đang bị bộ lọc ẩn (dấu chọn không mất khi đổi lọc/sắp xếp)."));
        ui_exp(it, tr("Một lần gọi engine cho cả lô; trước khi xoá sẽ hiện danh sách từng game và file, mặc định Huỷ."));
        ui_exp(it, tr("! Save/state không bị xoá cùng game: ROM mất, save còn lại thành mồ côi."));
        ui_exp(it, tr("X: bỏ đánh dấu tất cả. Y: đổi cách sắp xếp (Tên / Dung lượng). Rời màn là bỏ đánh dấu."));
        ui_act(it, 0, tr("XOÁ ĐÃ CHỌN…"), act_delete_marked);
        ui_act(it, 1, tr("BỎ CHỌN HẾT"), act_clear_marks);
        ui_act(it, 2, ylbl, act_toggle_sort);
    }
    if (del) {
        char label[48], tag[32];
        snprintf(label, sizeof label, tr("Lọc: %s"), tr(SAVE_LABEL[v->save]));
        snprintf(tag, sizeof tag, "%d game", nidx);
        ui_item *it = ui_add(s, label, tag, ST_UNKNOWN);
        it->iarg = -2;
        ui_exp(it, tr("Lọc theo save: tất cả / có save / không save."));
        ui_exp(it, tr("Có save = có file save cùng tên game (không tính state), ở thẻ nào cũng được."));
        ui_exp(it, tr("Dấu chọn (X) được giữ nguyên khi đổi lọc hay đổi sắp xếp."));
        ui_exp(it, tr("A: đổi sang \"%s\". Y: đổi cách sắp xếp (Tên / Dung lượng)."), tr(SAVE_LABEL[(v->save + 1) % 3]));
        ui_act(it, 0, tr("ĐỔI LỌC"), act_cycle_save);
        ui_act(it, 2, ylbl, act_toggle_sort);
    }
    for (int k = 0; k < nidx; k++) {
        int i = idx[k];
        cJSON *r = g_row[i];
        int sv = jbool_d(r, "has_save", 0), st = jbool_d(r, "has_state", 0);
        int mk = del && g_mark && g_mark[i];
        char tag[96], label[260];
        snprintf(tag, sizeof tag, "%s%s%s%s%s", top ? jstrd(r, "system", "?") : "", top ? " " : "", mbs(jnumd(r, "size", 0)), sv ? " +save" : "", st ? " +state" : "");
        snprintf(label, sizeof label, "%s%s", mk ? "[x] " : "", jstrd(r, "name", "?"));
        ui_item *it = ui_add(s, label, tag, mk ? ST_GREEN : sv || st ? ST_YELLOW : ST_UNKNOWN);
        ui_arg(it, jstrd(r, "path", ""));
        it->iarg = i;
        snprintf(it->arg2, sizeof it->arg2, "%s", jstrd(r, "card", ""));
        if (mk) ui_exp(it, tr("+ ĐÃ CHỌN để xoá (X để bỏ chọn)."));
        ui_exp(it, "%s", jstrd(r, "name", "?"));
        ui_exp(it, tr("Hệ: %s"), jstrd(r, "system", "?"));
        ui_exp(it, tr("Thẻ: %s"), sd_name(jstr(r, "card")));
        ui_exp(it, tr("Dung lượng: %s"), mbs(jnumd(r, "size", 0)));
        ui_exp(it, "%s", jstrd(r, "path", "?"));
        ui_exp(it, "%s", "");
        ui_exp(it, "Save: %s", sv ? tr("CÓ") : tr("không"));
        ui_exp(it, "State: %s", st ? tr("CÓ") : tr("không"));
        if (mode == RB_DELETE && (sv || st)) ui_exp(it, tr("! Save/state KHÔNG bị xoá cùng game (giữ lại, thành mồ côi nếu bạn không chơi lại)."));
        if (mode == RB_MOVE && (sv || st)) ui_exp(it, tr("! Save/state được CHÉP sang thẻ đích nhưng GIỮ LẠI ở thẻ nguồn."));
        const char *ext = jstrd(r, "extension", "");
        if (!strcasecmp(ext, ".cue") || !strcasecmp(ext, ".m3u")) ui_exp(it, tr("Game nhiều file: thao tác với file này áp dụng cho cả bộ (các track/đĩa đi kèm)."));
        else if (!strcasecmp(ext, ".png") || !strcasecmp(ext, ".jpg") || !strcasecmp(ext, ".jpeg") || !strcasecmp(ext, ".bmp"))
            ui_exp(it, tr("! Đây là file ảnh nằm trong Roms/ (thường là Imgs/), không phải ROM — `roms --json` liệt kê mọi file trong Roms/."));
        else if (!strcasecmp(ext, ".bin")) ui_exp(it, tr("! File .bin lẻ (không thấy .cue cùng tên): engine có thể từ chối nếu nó là track của game khác."));
        ui_act(it, 0, mode == RB_DELETE ? tr("XOÁ…") : tr("CHUYỂN…"), act_game);
        if (mode == RB_DELETE) {
            ui_act(it, 1, mk ? tr("BỎ CHỌN") : tr("CHỌN"), act_toggle);
            ui_act(it, 2, ylbl, act_toggle_sort);
        }
        shown++;
    }
    free(idx);
    if (!shown) ui_head(s, del && nscope ? tr("Không có game nào khớp bộ lọc") : tr("Không còn game"));
}

/* đặt con trỏ vào mục game có đường dẫn `path` (sau khi dựng lại danh sách) */
static void select_path(ui_screen *s, const char *path) {
    if (!path[0]) return;
    for (int i = 0; i < s->n; i++)
        if (!s->items[i]->head && s->items[i]->iarg >= 0 && !strcmp(s->items[i]->arg, path)) { s->sel = i; s->exp_scroll = 0; return; }
}

static void act_toggle(ui_screen *s, ui_item *it) {
    if (!g_mark || it->iarg < 0 || it->iarg >= g_n) return;
    char path[UI_ARGLEN];
    snprintf(path, sizeof path, "%s", it->arg);
    g_mark[it->iarg] ^= 1;
    ui_rebuild(s);                 /* mục "Xoá N game" xuất hiện/biến mất làm lệch chỉ số: giữ con trỏ theo đường dẫn */
    select_path(s, path);
}

/* con trỏ vào GAME đầu tiên (không phải mục Lọc / Xoá-lô: bấm A ngay thì mở game chứ không đổi lọc) */
static void select_first_game(ui_screen *s) {
    for (int i = 0; i < s->n; i++)
        if (!s->items[i]->head && s->items[i]->iarg >= 0) { s->sel = i; s->exp_scroll = 0; return; }
}

static void act_clear_marks(ui_screen *s, ui_item *it) {
    (void)it;
    if (g_mark) memset(g_mark, 0, (size_t)g_n);
    s->sel = 0;
    ui_rebuild(s);
    select_first_game(s);
}

/* dựng lại màn rồi đặt con trỏ về đúng mục đang chọn trước đó: game (theo đường dẫn) hoặc mục Lọc / Xoá-lô (theo iarg âm) */
static void rebuild_keep(ui_screen *s) {
    char path[UI_ARGLEN] = "";
    int ctl = 0;
    if (s->n && !s->items[s->sel]->head) {
        ui_item *cur = s->items[s->sel];
        if (cur->iarg >= 0) snprintf(path, sizeof path, "%s", cur->arg);
        else ctl = cur->iarg;
    }
    ui_rebuild(s);
    if (path[0]) select_path(s, path);
    else if (ctl)
        for (int i = 0; i < s->n; i++)
            if (!s->items[i]->head && s->items[i]->iarg == ctl) { s->sel = i; s->exp_scroll = 0; break; }
}

static void act_toggle_sort(ui_screen *s, ui_item *it) {
    (void)it;
    gview_t *v = view_of(s);
    v->sort = v->sort == SORT_SIZE ? SORT_NAME : SORT_SIZE;
    rebuild_keep(s);
}

static void act_cycle_save(ui_screen *s, ui_item *it) {
    (void)it;
    gview_t *v = view_of(s);
    v->save = (v->save + 1) % 3;
    rebuild_keep(s);
}

/* ---- xoá (một hoặc nhiều game): MỘT lần gọi `rom delete P1 P2 ...`; xem trước = không --yes ---- */
static char **g_req;               /* đường dẫn đã gửi xem trước */
static int g_nreq;
static char **g_apply;             /* đường dẫn sẽ gửi cùng --yes: các game plan.ok=true của lần xem trước */
static int g_napply;

static void strv_free(char ***v, int *n) {
    for (int i = 0; i < *n; i++) free((*v)[i]);
    free(*v);
    *v = NULL; *n = 0;
}
static void strv_clear_all(void) { strv_free(&g_req, &g_nreq); strv_free(&g_apply, &g_napply); }
static int strv_add(char ***v, int *n, const char *s) {
    char **nv = realloc(*v, (size_t)(*n + 1) * sizeof *nv);
    if (!nv) return 0;
    *v = nv;
    char *c = strdup(s);
    if (!c) return 0;
    (*v)[(*n)++] = c;
    return 1;
}

static void start_preview(char **paths, int n) {
    strv_free(&g_req, &g_nreq);
    for (int i = 0; i < n; i++) strv_add(&g_req, &g_nreq, paths[i]);
    const char **ex = malloc((size_t)(n + 3) * sizeof *ex);
    if (!ex) return;
    int k = 0;
    ex[k++] = "rom"; ex[k++] = "delete";
    for (int i = 0; i < n; i++) ex[k++] = paths[i];
    /* không --yes: stdin của engine là /dev/null nên CLI chỉ in kế hoạch (mỗi game: ok/files/artwork/saves_kept) rồi dừng, không xoá gì */
    cli_task_v(n == 1 ? tr("Đang lập danh sách file sẽ xoá") : tr("Đang lập danh sách file của cả lô sẽ xoá"), SHORT_TIMEOUT_MS, del_preview_done, "", ex, k);
    free(ex);
}

static void act_game(ui_screen *s, ui_item *it) {
    int mode = s->ia / 10;
    if (mode == RB_DELETE) {
        char *p = it->arg;
        start_preview(&p, 1);
    } else {
        lm_move_one_preview(it->arg, strcmp(it->arg2, "primary") ? "primary" : "secondary");
    }
}

static void act_delete_marked(ui_screen *s, ui_item *it) {
    (void)it;
    char **paths = NULL;
    int n = 0, filter = s->ia % 10;
    for (int i = 0; i < g_n; i++) {
        cJSON *r = g_row[i];
        if (!g_mark[i] || g_hide[i] || !card_ok(r, filter) || !in_scope(r, s->key)) continue;
        if (!strv_add(&paths, &n, jstrd(r, "path", ""))) break;
    }
    if (n) start_preview(paths, n);
    strv_free(&paths, &n);
}

/* game được xoá thật: plan.ok và không bị gộp vào kế hoạch của game khác trong lô (merged_into) */
static int game_live(const cJSON *g) { return jbool_d(jget(g, "plan"), "ok", 0) && !jstr(g, "merged_into"); }

/* danh sách game từ JSON xem trước/kết quả. Engine mới: "games":[{path,plan,...}]. Engine cũ (một game): chính JSON là plan. */
static cJSON *games_of(cJSON *r, cJSON **owned) {
    *owned = NULL;
    cJSON *g = jarr(r, "games");
    if (g) return g;
    cJSON *one = cJSON_CreateObject(), *arr = cJSON_CreateArray();
    if (!one || !arr) { cJSON_Delete(one); cJSON_Delete(arr); return NULL; }
    cJSON_AddStringToObject(one, "path", g_nreq == 1 ? g_req[0] : "");
    cJSON_AddItemReferenceToObject(one, "plan", jget(r, "plan") ? jget(r, "plan") : r);
    cJSON_AddItemToArray(arr, one);
    *owned = arr;
    return arr;
}

static void del_preview_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *p = app_json_or_error(j, tr("Xoá ROM"));
    if (!p) return;
    cJSON *own, *games = games_of(p, &own), *g;
    int nok = 0;
    strv_free(&g_apply, &g_napply);
    cJSON_ArrayForEach(g, games) {
        if (game_live(g)) {
            nok++;
            strv_add(&g_apply, &g_napply, jstrd(g, "path", ""));
        }
    }
    if (!nok) {
        lines_t L = {0};
        lines_add(&L, tr("!! Không xoá được:"));
        if (!games || !cJSON_GetArraySize(games)) lines_add(&L, "%s", jstrd(p, "message", tr("engine không trả danh sách game")));
        cJSON_ArrayForEach(g, games) {
            cJSON *pl = jget(g, "plan"), *w;
            const char *path = jstrd(g, "path", "?"), *b = strrchr(path, '/');
            lines_add(&L, "• %s: %s", b ? b + 1 : path, jstrd(pl, "message", tr("engine không báo lý do")));
            cJSON_ArrayForEach(w, jarr(pl, "warnings")) if (cJSON_IsString(w)) lines_add(&L, "  ! %s", w->valuestring);
        }
        ui_info(tr("Xoá ROM"), &L);
    } else ask_delete(games);
    cJSON_Delete(own);
    cJSON_Delete(p);
}

/* bỏ các dòng có đường dẫn nằm trong `deleted` khỏi g_roms, rồi dựng lại chỉ mục + đánh dấu.
 * Màn game đang mở đứng yên: con trỏ sang game còn lại kế tiếp (không có thì game liền trước). */
static int in_deleted(const cJSON *deleted, const char *path) {
    const cJSON *d;
    cJSON_ArrayForEach(d, deleted) if (cJSON_IsString(d) && !strcmp(d->valuestring, path)) return 1;
    return 0;
}

static void drop_deleted(const cJSON *deleted) {
    ui_screen *top = ui_top();
    char anchor[UI_ARGLEN] = "";
    int is_games = top && top->build == build_games && top->n > 0;
    if (is_games) {
        int start = top->sel;
        if (top->items[start]->iarg < 0)   /* con trỏ ở mục "Xoá N game đã chọn": bắt đầu từ game đầu tiên được chọn */
            for (int k = 0; k < top->n; k++)
                if (!top->items[k]->head && top->items[k]->iarg >= 0 && g_mark && g_mark[top->items[k]->iarg]) { start = k; break; }
        for (int k = start; k < top->n && !anchor[0]; k++)
            if (!top->items[k]->head && top->items[k]->iarg >= 0 && !in_deleted(deleted, top->items[k]->arg)) snprintf(anchor, sizeof anchor, "%s", top->items[k]->arg);
        for (int k = start - 1; k >= 0 && !anchor[0]; k--)
            if (!top->items[k]->head && top->items[k]->iarg >= 0 && !in_deleted(deleted, top->items[k]->arg)) snprintf(anchor, sizeof anchor, "%s", top->items[k]->arg);
    }
    cJSON *arr = jarr(g_roms, "roms"), *d;
    cJSON_ArrayForEach(d, deleted) {
        if (!cJSON_IsString(d)) continue;
        int i = 0;
        cJSON *r;
        cJSON_ArrayForEach(r, arr) {
            if (!strcmp(jstrd(r, "path", ""), d->valuestring)) { cJSON_DeleteItemFromArray(arr, i); break; }
            i++;
        }
    }
    rb_index();                    /* cũng xoá mọi đánh dấu */
    ui_rebuild_all();
    if (is_games) { top->sel = 0; select_path(top, anchor); if (top->n && top->items[top->sel]->head) top->sel = 0; }
}

static void del_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("Kết quả xoá ROM"));
    if (!r) return;
    lines_t L = {0};
    cJSON *deleted = jarr(r, "deleted"), *refused = jarr(r, "refused"), *x;
    if (!deleted) {
        lines_add(&L, tr("!! Chưa xoá gì:"));
        lines_add(&L, "%s", jstrd(r, "message", jstrd(jget(r, "plan"), "message", tr("engine không trả kết quả"))));
    } else {
        cJSON *own, *games = games_of(r, &own), *g;
        int nd = cJSON_GetArraySize(deleted), nr = refused ? cJSON_GetArraySize(refused) : 0, ns = 0;
        lines_add(&L, tr("%sĐã xoá %d file, giải phóng %s."), nd && !nr ? "+ " : "", nd, mbs(jnumd(r, "bytes_freed", 0)));
        if (nr) lines_add(&L, tr("!! %d file bị TỪ CHỐI (vẫn còn trên thẻ):"), nr);
        cJSON_ArrayForEach(x, refused) lines_add(&L, tr("!! TỪ CHỐI %s: %s"), jstrd(x, "path", "?"), jstrd(x, "reason", ""));
        cJSON_ArrayForEach(g, games) {     /* chỉ đếm save của game đã thực sự mất file */
            int gone;
            cJSON *gd = jarr(g, "deleted");
            if (gd) gone = cJSON_GetArraySize(gd) > 0;
            else gone = in_deleted(deleted, jstrd(g, "path", ""));
            if (gone) ns += jarr_n(jget(g, "plan"), "saves_kept");
        }
        cJSON_Delete(own);
        if (ns) lines_add(&L, tr("Save/state được GIỮ LẠI (không xoá, giờ thành mồ côi): %d file."), ns);
        app_add_rescan_lines(&L, jget(r, "rescan"));
        drop_deleted(deleted);     /* bỏ các dòng đã xoá khỏi danh sách đang duyệt (khỏi quét lại) */
    }
    cJSON_Delete(r);
    ui_info(tr("Kết quả xoá ROM"), &L);
    app_refresh();
}

static void do_delete(const char *arg) {
    (void)arg;
    /* --yes chỉ sau khi người dùng đã xác nhận trong hộp thoại (mặc định Huỷ); chỉ gửi các game đã xem là ok */
    int n = g_napply;
    const char **ex = malloc((size_t)(n + 4) * sizeof *ex);
    if (!ex || !n) { free(ex); return; }
    int k = 0;
    ex[k++] = "rom"; ex[k++] = "delete";
    for (int i = 0; i < n; i++) ex[k++] = g_apply[i];
    ex[k++] = "--yes";
    cli_task_v(tr("Đang xoá ROM"), LONG_TIMEOUT_MS, del_done, "", ex, k);
    free(ex);
}

static void ask_delete(const cJSON *games) {
    lines_t L = {0};
    cJSON *g, *f, *w;
    int nok = 0, nskip = 0, nf = 0, na = 0, ns = 0;
    double bytes = 0;
    cJSON_ArrayForEach(g, games) {
        cJSON *pl = jget(g, "plan");
        if (jstr(g, "merged_into")) continue;
        if (!jbool_d(pl, "ok", 0)) { nskip++; continue; }
        nok++;
        nf += jarr_n(pl, "files"); na += jarr_n(pl, "artwork"); ns += jarr_n(pl, "saves_kept");
        bytes += jnumd(pl, "bytes", 0);
    }
    lines_add(&L, tr("!! XOÁ VĨNH VIỄN %d game, %d file ROM + %d ảnh bìa (%s)"), nok, nf, na, mbs(bytes));
    cJSON_ArrayForEach(g, games) {
        cJSON *pl = jget(g, "plan");
        if (!game_live(g)) continue;
        const char *path = jstrd(g, "path", "?"), *base = strrchr(path, '/');
        lines_add(&L, "%s", "");
        lines_add(&L, tr("• %s (%d file ROM + %d ảnh bìa, %s)"), base ? base + 1 : path, jarr_n(pl, "files"), jarr_n(pl, "artwork"), mbs(jnumd(pl, "bytes", 0)));
        cJSON_ArrayForEach(f, jarr(pl, "files")) if (cJSON_IsString(f)) lines_add(&L, "    %s", f->valuestring);
        cJSON_ArrayForEach(f, jarr(pl, "artwork")) if (cJSON_IsString(f)) lines_add(&L, tr("    (ảnh bìa) %s"), f->valuestring);
        cJSON_ArrayForEach(w, jarr(pl, "warnings")) if (cJSON_IsString(w)) lines_add(&L, "  ! %s", w->valuestring);
    }
    if (ns) {
        lines_add(&L, "%s", "");
        lines_add(&L, tr("+ Save/state được GIỮ LẠI (LoongMate không xoá), %d file. ROM bị xoá nên các save này thành mồ côi:"), ns);
        cJSON_ArrayForEach(g, games) {
            cJSON *pl = jget(g, "plan");
            if (!game_live(g)) continue;
            cJSON_ArrayForEach(f, jarr(pl, "saves_kept")) if (cJSON_IsString(f)) lines_add(&L, "  %s", f->valuestring);
        }
    } else lines_add(&L, tr("Không có save/state đi kèm."));
    if (nskip) {
        lines_add(&L, "%s", "");
        lines_add(&L, tr("!! %d game sẽ bị BỎ QUA (không xoá):"), nskip);
        cJSON_ArrayForEach(g, games) {
            cJSON *pl = jget(g, "plan");
            if (jbool_d(pl, "ok", 0) || jstr(g, "merged_into")) continue;
            const char *path = jstrd(g, "path", "?"), *base = strrchr(path, '/');
            lines_add(&L, "!! %s: %s", base ? base + 1 : path, jstrd(pl, "message", tr("engine không báo lý do")));
        }
    }
    cJSON_ArrayForEach(g, games) {
        const char *mi = jstr(g, "merged_into");
        if (!mi) continue;
        const char *path = jstrd(g, "path", "?"), *b = strrchr(path, '/'), *mb2 = strrchr(mi, '/');
        lines_add(&L, "%s", "");
        lines_add(&L, tr("• %s: file nằm trong kế hoạch xoá của %s (gộp, không xoá hai lần)."), b ? b + 1 : path, mb2 ? mb2 + 1 : mi);
    }
    lines_add(&L, "%s", "");
    lines_add(&L, tr("Mỗi lần xoá được ghi vào sổ xoá trong thư mục dữ liệu LoongMate."));
    lines_add(&L, tr("! `rom delete` của CLI chưa có kế hoạch lưu file: engine lập lại danh sách file ngay lúc xoá (thường giống hệt danh sách trên); file nào ngoài Roms/ hay không an toàn sẽ bị từ chối riêng, kèm lý do."));
    char title[64];
    if (nok == 1) snprintf(title, sizeof title, tr("Xác nhận xoá ROM"));
    else snprintf(title, sizeof title, tr("Xác nhận xoá %d game"), nok);
    ui_confirm(title, &L, tr("XOÁ"), do_delete, "");
}

/* ============================================================ màn chọn hệ máy */
/* mở màn game của một hệ (key = tên hệ) hoặc của "Game lớn nhất (mọi hệ)" (key = TOP_KEY); con trỏ vào GAME đầu tiên, không vào mục Lọc */
static void open_games(ui_screen *from, const char *title, const char *key, int sort) {
    ui_screen *g = ui_screen_new(title, build_games);
    if (!g) return;
    snprintf(g->key, sizeof g->key, "%s", key);
    g->ia = from->ia;
    g->on_free = games_free;
    gview_t *v = calloc(1, sizeof *v);
    if (v) v->sort = sort;
    g->priv = v;
    if (ui_push(g)) select_first_game(g);
}

static void act_system(ui_screen *s, ui_item *it) {
    char title[120];
    snprintf(title, sizeof title, "%s — %s", s->ia / 10 == RB_DELETE ? tr("Xoá ROM") : tr("Chuyển game"), it->label);
    open_games(s, title, it->label, SORT_NAME);
}

static void act_top(ui_screen *s, ui_item *it) {
    (void)it;
    open_games(s, tr("Xoá ROM — Game lớn nhất (mọi hệ)"), TOP_KEY, SORT_SIZE);
}

static void act_cycle_card(ui_screen *s, ui_item *it) {
    (void)it;
    int mode = s->ia / 10, f = (s->ia % 10 + 1) % 3;
    s->ia = mode * 10 + f;
    ui_rebuild(s);
}

static int cmp_sys(const void *a, const void *b) { return strcasecmp(*(const char *const *)a, *(const char *const *)b); }

static void build_systems(ui_screen *s) {
    int mode = s->ia / 10, filter = s->ia % 10;
    const char *names[256];
    int nsys = 0;
    for (int i = 0; i < g_n; i++) {
        cJSON *r = g_row[i];
        if (g_hide[i] || !card_ok(r, filter)) continue;
        const char *sy = jstrd(r, "system", "?");
        int f = 0;
        for (int k = 0; k < nsys; k++) if (!strcmp(names[k], sy)) { f = 1; break; }
        if (!f && nsys < 256) names[nsys++] = sy;
    }
    qsort(names, (size_t)nsys, sizeof names[0], cmp_sys);
    int total = 0;
    double bytes = 0;
    for (int i = 0; i < g_n; i++) if (!g_hide[i] && card_ok(g_row[i], filter)) { total++; bytes += jnumd(g_row[i], "size", 0); }
    if (mode == RB_DELETE) {
        ui_item *it = ui_add(s, tr("Thẻ"), filter == 0 ? tr("cả hai thẻ") : filter == 1 ? "SD1" : "SD2", ST_UNKNOWN);
        ui_exp(it, tr("Lọc danh sách theo thẻ: cả hai thẻ > SD1 > SD2. Bấm A để đổi."));
        ui_act(it, 0, tr("ĐỔI THẺ"), act_cycle_card);
    }
    ui_hdr(s, "%d game (%s)%s", total, mbs(bytes), filter == 0 ? "" : filter == 1 ? tr(" trên SD1") : tr(" trên SD2"));
    if (g_nhidden) ui_hdr(s, tr("Ẩn %d file .bin là track của game có .cue (chọn file .cue để thao tác trọn bộ)."), g_nhidden);
    if (mode == RB_DELETE && total > 0) {
        char tag[32];
        snprintf(tag, sizeof tag, "%d game", total < TOP_N ? total : TOP_N);
        ui_item *it = ui_add(s, tr("Game lớn nhất (mọi hệ)"), tag, ST_UNKNOWN);
        ui_exp(it, tr("%d game nặng nhất trên mọi hệ máy%s."), TOP_N, filter == 0 ? tr(" (cả hai thẻ)") : filter == 1 ? tr(" (chỉ SD1)") : tr(" (chỉ SD2)"));
        ui_exp(it, tr("Lọc theo save trước, rồi mới lấy %d game lớn nhất."), TOP_N);
        ui_exp(it, tr("Dùng để dọn nhanh khi thẻ gần đầy."));
        ui_exp(it, tr("A — duyệt (chọn nhiều bằng X, xoá cả lô, đổi lọc có/không save bằng mục Lọc, Y đổi sắp xếp)."));
        ui_act(it, 0, tr("MỞ"), act_top);
    }
    ui_head(s, tr("Hệ máy (%d)"), nsys);
    for (int k = 0; k < nsys; k++) {
        int c = 0, cp = 0, cs = 0;
        double sb = 0;
        for (int i = 0; i < g_n; i++) {
            cJSON *r = g_row[i];
            if (g_hide[i] || !card_ok(r, filter) || strcmp(jstrd(r, "system", "?"), names[k])) continue;
            c++;
            sb += jnumd(r, "size", 0);
            if (!strcmp(jstrd(r, "card", ""), "primary")) cp++; else cs++;
        }
        char tag[64];
        snprintf(tag, sizeof tag, "%d game, %s", c, mbs(sb));
        ui_item *it = ui_add(s, names[k], tag, ST_UNKNOWN);
        ui_exp(it, "%d game, %s.", c, mbs(sb));
        ui_exp(it, tr("Trên SD1: %d game"), cp);
        ui_exp(it, tr("Trên SD2: %d game"), cs);
        ui_act(it, 0, tr("MỞ"), act_system);
    }
    if (!nsys) ui_head(s, tr("(Không có ROM nào)"));
}

/* ============================================================ vào từ menu */
static void roms_done(lm_job *j, const char *arg) {
    int mode = arg[0] - '0';
    const char *card = arg[1] == ':' ? arg + 2 : "";
    cJSON *r = app_json_or_error(j, tr("Danh sách ROM"));
    if (!r) return;
    if (!jarr(r, "roms")) {
        lines_t L = {0};
        lines_add(&L, tr("!! Không liệt kê được ROM:"));
        lines_add(&L, "%s", jstrd(r, "message", tr("engine không trả danh sách (engine cũ chưa có lệnh roms?)")));
        cJSON_Delete(r);
        ui_info(tr("Danh sách ROM"), &L);
        return;
    }
    cJSON_Delete(g_roms);
    g_roms = r;
    rb_index();
    char title[96];
    if (mode == RB_MOVE) snprintf(title, sizeof title, tr("Chuyển game từ %s — hệ máy"), sd_name(card));
    else snprintf(title, sizeof title, tr("Xoá ROM — hệ máy"));
    ui_screen *s = ui_screen_new(title, build_systems);
    s->ia = mode * 10 + (mode == RB_MOVE ? (strcmp(card, "primary") ? 2 : 1) : 0);
    s->on_free = roms_free;
    ui_push(s);
}

void open_roms_delete(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task(tr("Đang liệt kê ROM"), LONG_TIMEOUT_MS, roms_done, "0", "roms", NULL);
}

void open_roms_move(ui_screen *s, ui_item *it) {
    (void)s;
    char arg[40];
    snprintf(arg, sizeof arg, "1:%s", it->arg);
    cli_task(tr("Đang liệt kê ROM"), LONG_TIMEOUT_MS, roms_done, arg, "roms", "--card", it->arg, NULL);
}

/* ============================================================ Tìm kiếm game */
static const char *ci_strstr(const char *h, const char *n) {
    if (!h || !n) return NULL;
    if (!n[0]) return h;
    size_t nl = strlen(n);
    for (; *h; h++) {
        if (strncasecmp(h, n, nl) == 0) return h;
    }
    return NULL;
}

static void search_osk_done(const char *text, const char *arg);

static void act_search_again(ui_screen *s, ui_item *it) {
    (void)it;
    ui_osk(tr("Tìm kiếm game (gõ tên)"), s->key, 80, search_osk_done, "again");
}

static void build_search_results(ui_screen *s) {
    ui_hdr(s, tr("Kết quả tìm kiếm cho: \"%s\""), s->key);
    ui_hdr(s, tr("Bấm Y để gõ từ khoá tìm kiếm khác."));

    int count = 0;
    for (int i = 0; i < g_n; i++) {
        if (g_hide && g_hide[i]) continue;
        cJSON *r = g_row[i];
        const char *name = jstrd(r, "name", "");
        const char *sys = jstrd(r, "system", "");
        const char *path = jstrd(r, "path", "");
        if (!ci_strstr(name, s->key) && !ci_strstr(sys, s->key) && !ci_strstr(path, s->key)) continue;

        char tag[64];
        snprintf(tag, sizeof tag, "%s · %s", sys, sd_name(jstr(r, "card")));
        ui_item *it = ui_add(s, name, tag, ST_GREEN);
        if (!it) continue;
        ui_exp(it, tr("Tên game: %s"), name);
        ui_exp(it, tr("Hệ máy: %s · Thẻ: %s"), sys, sd_name(jstr(r, "card")));
        ui_exp(it, tr("Dung lượng: %s"), mbs(jnumd(r, "size", 0)));
        ui_exp(it, tr("Đường dẫn: %s"), path);
        ui_exp(it, "Save: %s · State: %s", jbool_d(r, "has_save", 0) ? tr("CÓ") : tr("không"), jbool_d(r, "has_state", 0) ? tr("CÓ") : tr("không"));
        ui_act(it, 2, tr("TÌM TỪ KHÁC"), act_search_again);
        count++;
    }

    if (!count) {
        ui_head(s, tr("(Không có game khớp từ khoá)"));
        ui_item *it = ui_add(s, tr("Tìm lại với từ khoá khác…"), "", ST_YELLOW);
        if (it) {
            ui_exp(it, tr("Thử gõ một phần tên game, ví dụ: mario, poke, zelda..."));
            ui_act(it, 0, tr("GÕ LẠI"), act_search_again);
        }
    }
}

static void search_osk_done(const char *text, const char *arg) {
    if (!text || !text[0]) return;
    ui_screen *top = ui_top();
    if (!strcmp(arg, "again") && top && top->build == build_search_results) {
        /* tìm lại ngay trên màn kết quả: không chồng màn mới (đóng màn trên sẽ giải phóng danh sách ROM dùng chung) */
        snprintf(top->key, sizeof top->key, "%s", text);
        top->sel = top->top = 0;
        ui_rebuild(top);
        return;
    }
    ui_screen *s = ui_screen_new("TÌM KIẾM GAME", build_search_results);
    if (!s) return;
    snprintf(s->key, sizeof s->key, "%s", text);
    s->on_free = roms_free;
    ui_push(s);
}

static void roms_search_init_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, tr("Danh sách ROM"));
    if (!r) return;
    cJSON_Delete(g_roms);
    g_roms = r;
    rb_index();
    ui_osk(tr("Tìm kiếm game (gõ tên)"), "", 80, search_osk_done, "");
}

void open_roms_search(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    if (g_roms && g_n > 0) {
        ui_osk(tr("Tìm kiếm game (gõ tên)"), "", 80, search_osk_done, "");
    } else {
        cli_task(tr("Đang quét danh sách ROM"), LONG_TIMEOUT_MS, roms_search_init_done, "", "roms", NULL);
    }
}

