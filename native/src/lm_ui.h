/* Khung giao diện chung của LoongMate native: danh sách cuộn + khung giải thích + chân màn hình theo mục,
 * hộp xác nhận (mặc định Huỷ), hộp thông tin cuộn được, chạy CLI nền có đếm giây.
 * Hành vi bám theo ListScreen / Dialog / Task của engine/ui_screens.py (đặc tả hành vi). */
#ifndef LM_UI_H
#define LM_UI_H
#include "catastrophe.h"
#include "cjson/cJSON.h"
#include "lm_common.h"
#include "lm_json.h"
#include "lm_run.h"
#include <stdarg.h>

#define UI_ARGLEN 640

typedef enum { ST_GREEN, ST_YELLOW, ST_RED, ST_UNKNOWN } status_t;

/* ---- danh sách dòng chữ (heap) ---- */
typedef struct { char **v; int n, cap; } lines_t;
void lines_add(lines_t *L, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
void lines_free(lines_t *L);

/* ---- mục & màn hình ---- */
typedef struct ui_item ui_item;
typedef struct ui_screen ui_screen;
typedef void (*ui_fn)(ui_screen *s, ui_item *it);

struct ui_item {
    char label[200], tag[96];
    int tagc, head;
    lines_t exp;                       /* khung giải thích */
    ui_fn fn[3];                       /* A, X, Y */
    const char *lbl[3];                /* nhãn chân màn hình (chuỗi tĩnh) */
    char arg[UI_ARGLEN];               /* tham số cho hành động (id snapshot, đường dẫn, ...) */
    char arg2[96];
    int iarg;
};

struct ui_screen {
    char title[96];
    lines_t hdr;
    ui_item **items; int n, cap;
    int sel, top, exp_scroll;
    void (*build)(ui_screen *s);       /* dựng lại hdr + items từ dữ liệu toàn cục / s->key */
    char key[UI_ARGLEN];               /* ngữ cảnh của màn (hệ máy, thẻ ưu tiên, tên node, ...) */
    int ia;
    cJSON *d;                          /* JSON riêng của màn (sở hữu, giải phóng khi đóng) */
    /* màn tuỳ biến (Home, biểu đồ, kiểm tra nút) */
    void (*draw)(ui_screen *s, SDL_Rect area);
    int (*key_fn)(ui_screen *s, cat_input_event *ev);   /* trả 1 nếu đã xử lý */
    cat_footer_item footer[6]; int nfooter;
    void *priv;
    void (*on_free)(ui_screen *s);
};

ui_screen *ui_screen_new(const char *title, void (*build)(ui_screen *));
ui_screen *ui_push(ui_screen *s);              /* đẩy lên ngăn xếp; nếu có build thì gọi build */
void ui_pop(void);                             /* đóng màn trên cùng (không bao giờ đóng đáy) */
void ui_pop_to(int depth);
void ui_drop_at(int idx);                      /* gỡ và giải phóng màn hình ở vị trí idx (chống tràn ngăn xếp) */
ui_screen *ui_screen_at(int idx);              /* lấy màn hình tại vị trí idx trong ngăn xếp */
int ui_depth(void);
ui_screen *ui_top(void);
void ui_rebuild(ui_screen *s);
void ui_rebuild_all(void);

void ui_hdr(ui_screen *s, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
ui_item *ui_head(ui_screen *s, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
ui_item *ui_add(ui_screen *s, const char *label, const char *tag, int tagc);
void ui_exp(ui_item *it, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
void ui_act(ui_item *it, int slot, const char *lbl, ui_fn fn);
void ui_arg(ui_item *it, const char *arg);

/* ---- hộp thoại ---- */
void ui_info(const char *title, lines_t *L);                     /* nhận quyền sở hữu L (giải phóng bên trong) */
void ui_info1(const char *title, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
typedef void (*ui_confirm_cb)(const char *arg);
void ui_confirm(const char *title, lines_t *L, const char *confirm_label, ui_confirm_cb cb, const char *arg);
/* pref "auto"/rỗng và CẢ HAI thẻ dùng được -> mở màn CHỌN THẺ LƯU GAME, chọn xong gọi replan("primary"/"secondary"); trả 1.
 * Người dùng đã chọn thẻ, hoặc chỉ một thẻ dùng được -> trả 0 (đi tiếp như cũ). card = object "card" của kế hoạch. */
int ui_card_pick(const cJSON *card, const char *pref, ui_confirm_cb replan);

/* ---- tác vụ nền: chạy CLI, hiện "Đang ... (Ns)", chặn mọi phím cho tới khi xong ---- */
typedef void (*ui_task_cb)(lm_job *j, const char *arg);
/* Trả 0 nếu không khởi chạy được (đã hiện hộp lỗi). argv NULL-terminated, dùng xong là bỏ. */
int ui_task(const char *label, char *const argv[], int timeout_ms, ui_task_cb cb, const char *arg);
int ui_busy(void);
/* Gọi NGAY sau khi khởi chạy tác vụ: cho phép bấm B để tạm dừng (kill tiến trình con). Callback thấy ui_task_cancelled() == 1. */
void ui_task_cancelable(int on);
int ui_task_cancelled(void);
/* Parse stdout của job thành JSON object/array. NULL + mô tả lỗi thật nếu không được. */
cJSON *ui_job_json(const lm_job *j, char *err, size_t errsz);
void ui_job_failure(const lm_job *j, char *buf, size_t n);

/* ---- tiện ích hiển thị ---- */
void ui_fmt_bytes(char *buf, size_t n, double bytes);            /* "3.0 MB" / "512.0 KB" (cùng cách mb() của Python) */
void ui_fmt_mb(char *buf, size_t n, double mb);                  /* "909 MB" / "23.8 GB" */
const char *ui_status_vi(const char *s);
cat_draw_color ui_color(status_t s);                             /* màu chữ/chấm theo status, đọc được trên nền theme */
cat_draw_color ui_line_color(const char *line);                  /* tiền tố "!!" đỏ, "!" vàng, "+" xanh */
status_t ui_parse_status(const char *s);
status_t ui_tagc(const char *s);                                 /* "green"/"yellow"/"red"/else unknown */
/* cắt chuỗi dài giữ phần đuôi (đường dẫn): "...đuôi" */
void ui_tail(char *buf, size_t n, const char *s, size_t keep);

/* ---- bàn phím trên màn hình (lm_osk.c): ASCII; XONG -> đóng bàn phím rồi gọi cb(text đã cắt khoảng trắng, arg) nếu không rỗng; HUỶ -> không gọi ---- */
void ui_osk(const char *title, const char *initial, int maxlen, void (*cb)(const char *text, const char *arg), const char *arg);

/* ---- vòng lặp ---- */
extern int g_debounce_ms;       /* chống bấm đúp ngay sau khi hộp thoại mở; harness đặt 0 */
void ui_quit(void);
int ui_run(lm_harness *h, ui_screen *home);                      /* trả mã thoát */

/* vẽ nền tảng cho màn tuỳ biến */
int ui_wrap(TTF_Font *f, const char *text, int maxw, lines_t *out);   /* xuống dòng theo từ; trả số dòng thêm */
#endif
