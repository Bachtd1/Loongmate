/* Phần dùng chung của các binary native: font tiếng Việt, chụp màn hình (PPM),
 * và "harness" thử nghiệm (tiêm phím giả + tự chụp khi UI đã ổn định).
 * Harness chỉ dùng cho kiểm thử không cần màn hình thật (SDL_VIDEODRIVER=offscreen). */
#ifndef LM_COMMON_H
#define LM_COMMON_H
#include "catastrophe.h"

/* Đảm bảo font đang dùng có glyph tiếng Việt (U+1EC5). Ưu tiên font của theme Leaf;
 * nếu thiếu glyph thì nạp Nunito-Bold của launcher (cùng font bản Python). Trả đường
 * dẫn font cuối cùng dùng (hoặc "theme"), ghi log kết quả. */
const char *lm_ensure_vietnamese_font(void);

/* Ghi khung hình hiện tại của renderer ra file PPM (P6). 0 = ok. Gọi TRƯỚC cat_present. */
int lm_write_ppm(const char *path);

typedef struct {
    const char *shot_path;      /* --screenshot FILE.ppm */
    cat_button keys[256];        /* thêm "SHOT" = chụp giữa chừng; L1, R1 */        /* --keys UP,DOWN,A,B,X,Y,LEFT,RIGHT */
    int nkeys, kpos;
    unsigned busy_since;        /* mốc bắt đầu tác vụ nền hiện tại (SHOTB) */
    int shot_n;                 /* số ảnh "SHOT" đã chụp */
    int settle_frames;          /* khung hình đã trôi qua kể từ phím cuối */
    unsigned start_ms;
    int timed_out;
    int pending_b;              /* BUSYB: một lần bấm B sắp được tiêm (trong lúc tác vụ nền chạy) */
} lm_harness;

/* Thử tiêu thụ đối số argv[*i]; trả 1 nếu là đối số của harness (và tăng *i khi cần). */
int lm_harness_arg(lm_harness *h, int argc, char **argv, int *i);
int lm_harness_active(const lm_harness *h);   /* có --screenshot hoặc --keys */
/* Lấy sự kiện: phím giả trước, rồi mới tới bàn phím/tay cầm thật. */
bool lm_harness_poll(lm_harness *h, cat_input_event *ev);
/* Gọi mỗi khung hình sau khi vẽ xong. Trả 1 nếu đã chụp (hoặc hết giờ) và nên thoát. */
int lm_harness_frame_end(lm_harness *h, int busy);
#endif
