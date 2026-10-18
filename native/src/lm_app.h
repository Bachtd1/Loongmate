/* Khai báo dùng chung giữa các màn của LoongMate native. */
#ifndef LM_APP_H
#define LM_APP_H
#include "lm_cli.h"
#include "lm_ui.h"

#define LONG_TIMEOUT_MS (30 * 60 * 1000)
#define SHORT_TIMEOUT_MS (60 * 1000)

extern cJSON *g_ready;          /* kết quả `readiness` gần nhất, hoặc NULL */
extern char g_ready_err[300];   /* lý do thật khi g_ready == NULL */

/* chạy lại readiness (nền), cập nhật g_ready, dựng lại mọi màn danh sách, rồi gọi then (có thể NULL) */
void app_refresh_then(void (*then)(void));
void app_refresh(void);
/* ghi các dòng về kết quả tự rescan Leaf do CLI báo (đối tượng "rescan" trong JSON; NULL = không có thay đổi) */
void app_add_rescan_lines(lines_t *L, const cJSON *rescan);
/* đọc JSON kết quả; nếu lỗi thì hiện hộp lỗi và trả NULL */
cJSON *app_json_or_error(lm_job *j, const char *what);
/* joined tiện dụng cho giá trị JSON bất kỳ -> chuỗi (Python-like): chuỗi, số, bool, mảng/đối tượng dạng gọn */
void app_scalar(const cJSON *v, char *buf, size_t n);

/* kết quả `agent status`/`snapshots` -> mức kiểm tra toàn vẹn kho (cùng quy tắc vault_verify_status của bản Python):
 * level 0 chưa kiểm (KHÔNG cảnh báo) | 1 ổn | 2 cũ (> 14 ngày) | 3 có object hỏng/thiếu */
typedef struct { int level, count, nbad, nmissing, age_days, has_age, partial, has_checked; double ts, checked; } vv_t;
void app_vv_eval(const cJSON *vv, vv_t *out);
/* dòng sẵn để hiện ("" khi level 0) */
void app_vv_line(const vv_t *v, char *buf, size_t n);
/* `snapshots --json` mới nhất (kèm vault_verify), hoặc NULL */
extern cJSON *g_snaps;
/* Kết quả `--apply --plan`: trả 1 (đã hiện hộp báo) nếu kế hoạch bị từ chối toàn bộ (mã thoát 3) hoặc engine không trả kết quả thực hiện. */
int app_plan_rejected(lm_job *j, const cJSON *r, const char *title);
/* JSON xem trước có plan_id + plan_path đúng file mong đợi (engine đã ghi kế hoạch)? Nếu không: hiện hộp lỗi, trả 0. */
int app_plan_written(const cJSON *r, const char *kind, const char *title);

/* lm_home.c */
void open_bundle_confirm(ui_screen *s, ui_item *it);
/* lm_library.c */
void open_library_screen(void);
void open_transfer_screen(ui_screen *s, ui_item *it);
void open_roms_delete(ui_screen *s, ui_item *it);
/* lm_import.c */
void open_import(ui_screen *s, ui_item *it);          /* Library -> Nhập game (Local Importer) */
/* lm_lib.c */
void open_lib(ui_screen *s, ui_item *it);             /* Library -> Thư viện riêng (agent PocketBridge của bạn) */
void open_hb(ui_screen *s, ui_item *it);              /* Library -> Homebrew (tải trực tiếp, chỉ game có giấy phép rõ ràng) */
void open_web(ui_screen *s, ui_item *it);             /* Library -> Tải từ web (lm_web.c) */
void open_patch(ui_screen *s, ui_item *it);           /* Library -> Patch ROM (lm_patch.c) */
void open_roms_move(ui_screen *s, ui_item *it);      /* it->arg = thẻ nguồn "primary"/"secondary" */
/* xem trước chuyển MỘT game (ghi kế hoạch), hiện màn kế hoạch; xác nhận -> --apply --plan */
void lm_move_one_preview(const char *rom, const char *dest);
/* lm_saves.c */
void open_game_saves(ui_screen *s, ui_item *it);   /* SaveVault -> Khôi phục save một game */
/* lm_misc.c */
void open_history(ui_screen *s, ui_item *it);
void open_agent(ui_screen *s, ui_item *it);
void open_nodes(ui_screen *s, ui_item *it);
void open_buttontest(ui_screen *s, ui_item *it);
void open_battery(ui_screen *s, ui_item *it);
void open_update(ui_screen *s, ui_item *it);
/* lm_hottabs.c */
void open_hottabs(ui_screen *s, ui_item *it);
/* lm_leafcfg.c */
void open_leafcfg(ui_screen *s, ui_item *it);
/* lm_roms.c, lm_web.c */
void open_roms_search(ui_screen *s, ui_item *it);
void open_recent(ui_screen *s, ui_item *it);
#endif
