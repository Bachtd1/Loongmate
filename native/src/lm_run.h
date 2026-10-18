/* Chạy tiến trình con bằng fork+execv (mảng argv, KHÔNG qua shell), không chặn:
 * gọi lm_job_poll() mỗi khung hình để vừa đọc stdout/stderr vừa giữ giao diện sống.
 * Lý do tách riêng: engine Python là nguồn logic duy nhất, bản native chỉ gọi nó. */
#ifndef LM_RUN_H
#define LM_RUN_H
#include <stddef.h>

typedef struct lm_job lm_job;

/* Trả NULL (kèm lý do trong err) nếu không tạo được pipe/fork. Nếu execv thất bại trong
 * con thì job vẫn kết thúc với exit_code 127. timeout_ms <= 0: không giới hạn. */
lm_job *lm_job_start(char *const argv[], int timeout_ms, char *err, size_t errsz);

/* Trả 1 khi đã xong (hoặc bị giết do quá hạn), 0 nếu còn chạy. */
int lm_job_poll(lm_job *j);

const char *lm_job_stdout(const lm_job *j);   /* luôn NUL-terminated, không NULL */
const char *lm_job_stderr(const lm_job *j);   /* 4 KB cuối cùng đủ để báo lỗi */
int lm_job_exit_code(const lm_job *j);        /* -1 nếu bị tín hiệu giết hoặc quá hạn */
int lm_job_timed_out(const lm_job *j);
int lm_job_stdout_truncated(const lm_job *j); /* stdout vượt trần 4 MB */
/* Xin tiến trình con dừng (SIGTERM). Việc dọn/thu xác do lm_job_poll làm như mọi lần kết thúc. */
void lm_job_kill(lm_job *j);
void lm_job_free(lm_job *j);
#endif
