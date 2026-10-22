/* Đường dẫn + cách gọi engine Python (cli.py --json). Native KHÔNG tự đọc/ghi file người dùng:
 * mọi dữ liệu và hành động đi qua đây, nên mọi chốt an toàn nằm ở engine đã kiểm thử. */
#ifndef LM_CLI_H
#define LM_CLI_H
#include "lm_ui.h"

typedef struct {
    char pak[512], python[512], userdata[512], primary[512], secondary[512];
    char cli[600], ui[600];   /* ui = <pak>/engine/ui_sdl.py (giao diện Python dự phòng) */
    char apps_dir[512];     /* --apps-dir (thử cập nhật trên sandbox); rỗng = mặc định của engine */
    int no_rescan;          /* LOONGMATE_NO_RESCAN=1 -> truyền --no-rescan xuống CLI */
} lm_paths;
extern lm_paths P;

/* arg_* có thể NULL. 0 = ok; khác 0 = lỗi kèm mô tả thật. */
int cli_resolve(const char *arg_pak, const char *arg_py, const char *arg_ud,
                const char *arg_primary, const char *arg_secondary, const char *arg_apps, char *err, size_t errsz);

/* Chạy `python cli.py [--primary ..] [--secondary ..] --userdata .. --quiet --json [--no-rescan] <extra...>`.
 * extra là danh sách const char* kết thúc bằng NULL (từng phần tử là MỘT đối số argv, không qua shell). */
/* File kế hoạch đã duyệt: <userdata>/jobs/native-<kind>.plan.json (CLI --plan-out ghi, --plan đọc). plan_file trả bộ đệm tĩnh. */
const char *plan_file(const char *kind);
void plan_remove(const char *kind);   /* xoá file nếu có (êm lặng nếu không có) */

/* Thay tiến trình bằng giao diện Python (ui_sdl.py) với cùng --userdata/--primary/--secondary/--apps-dir. Không trở về nếu thành công;
 * thiếu Python/ui_sdl.py thì báo hộp thoại rồi trở về. */
void ui_exec_python_ui(void);

int cli_task_v(const char *label, int timeout_ms, ui_task_cb cb, const char *arg, const char *const *extra, int nextra);
int cli_task(const char *label, int timeout_ms, ui_task_cb cb, const char *arg, ...);
lm_job *cli_spawn(int timeout_ms, const char *const *extra, int nextra);   /* chạy nền, không chặn phím; người gọi poll/free */
#endif
