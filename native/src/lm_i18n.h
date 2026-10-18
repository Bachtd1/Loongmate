#ifndef LM_I18N_H
#define LM_I18N_H
/* Ngôn ngữ giao diện (lm_i18n.c). format_arg: trình biên dịch vẫn kiểm %s/%d của chuỗi gốc khớp tham số. */
const char *tr(const char *vi) __attribute__((format_arg(1)));
const char *i18n_lang(void);      /* "vi" | "en" */
int i18n_set(const char *lang);   /* nạp danh mục <pak>/res/i18n_<lang>.json; "vi" = bỏ dịch. 0 = được */
void i18n_init(void);             /* đọc <userdata>/lang.json lúc khởi động (sau cli_resolve) */
int i18n_save(const char *lang);  /* ghi <userdata>/lang.json */
#endif
