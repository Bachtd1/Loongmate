/* LoongMate native — ngôn ngữ giao diện. Mã viết tiếng Việt; tr("chuỗi tiếng Việt") trả bản dịch theo danh mục
 * <pak>/res/i18n_<lang>.json (sinh từ i18n/en.json), không có thì trả nguyên chuỗi. Chuỗi định dạng được dịch TRƯỚC khi
 * điền giá trị; bản dịch nào có %... khác bản gốc bị bỏ qua lúc nạp (không bao giờ để printf đọc sai tham số).
 * Ngôn ngữ chọn ở Máy > Ngôn ngữ, lưu <userdata>/lang.json; đặt LOONGMATE_LANG cho engine (engine/i18n.py dịch JSON). */
#define _GNU_SOURCE
#include "lm_i18n.h"
#include "cjson/cJSON.h"
#include "lm_cli.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { char *vi, *en; } pair_t;
static pair_t *g_tab;
static size_t g_cap;          /* lũy thừa 2; 0 = tiếng Việt (không tra) */
static char g_lang[8] = "vi";

static uint32_t fnv(const char *s) {
    uint32_t h = 2166136261u;
    for (; *s; s++) h = (h ^ (unsigned char)*s) * 16777619u;
    return h;
}

/* Dãy chỉ thị định dạng (%s, %.0f, %d, %%...) của s, nối thành chuỗi để so. */
static void specs(const char *s, char *out, size_t n) {
    size_t o = 0;
    for (; *s; s++) {
        if (*s != '%') continue;
        const char *b = s++;
        while (*s && strchr("-+ #0123456789.*hlzjt", *s)) s++;
        if (!*s) break;
        size_t len = (size_t)(s - b) + 1;
        if (o + len + 1 < n) { memcpy(out + o, b, len); o += len; out[o++] = '|'; }
    }
    out[o] = 0;
}

static void clear_tab(void) {
    for (size_t i = 0; i < g_cap; i++) { free(g_tab[i].vi); free(g_tab[i].en); }
    free(g_tab);
    g_tab = NULL;
    g_cap = 0;
}

static int load_catalog(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = n > 0 ? malloc((size_t)n + 1) : NULL;
    if (!buf || fread(buf, 1, (size_t)n, f) != (size_t)n) { free(buf); fclose(f); return -1; }
    fclose(f);
    buf[n] = 0;
    cJSON *root = cJSON_Parse(buf);
    free(buf);
    if (!cJSON_IsObject(root)) { cJSON_Delete(root); return -1; }
    size_t cnt = 0;
    cJSON *e;
    cJSON_ArrayForEach(e, root) cnt++;
    size_t cap = 64;
    while (cap < cnt * 2) cap <<= 1;
    pair_t *tab = calloc(cap, sizeof *tab);
    if (!tab) { cJSON_Delete(root); return -1; }
    char a[256], b[256];
    cJSON_ArrayForEach(e, root) {
        if (!cJSON_IsString(e) || !e->valuestring || !e->valuestring[0] || !e->string) continue;
        specs(e->string, a, sizeof a);
        specs(e->valuestring, b, sizeof b);
        if (strcmp(a, b)) continue;                       /* chỗ trống lệch: giữ tiếng Việt */
        size_t i = fnv(e->string) & (cap - 1);
        while (tab[i].vi) i = (i + 1) & (cap - 1);
        tab[i].vi = strdup(e->string);
        tab[i].en = strdup(e->valuestring);
    }
    cJSON_Delete(root);
    clear_tab();
    g_tab = tab;
    g_cap = cap;
    return 0;
}

const char *tr(const char *vi) {
    if (!g_cap || !vi || !*vi) return vi;
    size_t i = fnv(vi) & (g_cap - 1);
    while (g_tab[i].vi) {
        if (!strcmp(g_tab[i].vi, vi)) return g_tab[i].en;
        i = (i + 1) & (g_cap - 1);
    }
    return vi;
}

const char *i18n_lang(void) { return g_lang; }

int i18n_set(const char *lang) {
    if (!lang || !strcmp(lang, "vi")) {
        clear_tab();
        snprintf(g_lang, sizeof g_lang, "vi");
        unsetenv("LOONGMATE_LANG");
        return 0;
    }
    char path[700];
    snprintf(path, sizeof path, "%s/res/i18n_%s.json", P.pak, lang);
    if (load_catalog(path) != 0) return -1;
    snprintf(g_lang, sizeof g_lang, "%s", lang);
    setenv("LOONGMATE_LANG", g_lang, 1);                /* engine (tiến trình con) dịch JSON theo cùng ngôn ngữ */
    return 0;
}

void i18n_init(void) {
    char path[600];
    snprintf(path, sizeof path, "%s/lang.json", P.userdata);
    FILE *f = fopen(path, "rb");
    if (!f) return;
    char buf[256] = {0};
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    cJSON *j = cJSON_Parse(buf);
    cJSON *l = cJSON_GetObjectItemCaseSensitive(j, "lang");
    if (cJSON_IsString(l) && l->valuestring) i18n_set(l->valuestring);
    cJSON_Delete(j);
}

int i18n_save(const char *lang) {
    char path[600], tmp[620];
    snprintf(path, sizeof path, "%s/lang.json", P.userdata);
    snprintf(tmp, sizeof tmp, "%s.partial", path);
    FILE *f = fopen(tmp, "wb");
    if (!f) return -1;
    fprintf(f, "{\"lang\": \"%s\"}\n", lang);
    if (fclose(f) != 0) return -1;
    return rename(tmp, path);
}
