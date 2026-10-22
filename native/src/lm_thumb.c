/* LoongMate native — ảnh thu nhỏ (ảnh bìa trong Tải từ web). Native chỉ ĐỌC file ảnh đã có trong cache của engine
 * (`web thumbs` tải nền, ghi tạm rồi đổi tên nên không bao giờ thấy file dở); file chưa có thì thử lại sau, không chặn khung hình.
 * Bộ đệm texture nhỏ (LRU), ảnh lớn thu về tối đa THUMB_PX cạnh trước khi tạo texture để giữ RAM/VRAM thấp. */
#define _GNU_SOURCE
#include "lm_ui.h"
#include <string.h>
#include <sys/stat.h>

#define THUMB_SLOTS 40
#define THUMB_PX 360
#define RETRY_MS 400u

typedef struct {
    char path[256];
    SDL_Texture *tex;
    int w, h, bad;
    Uint32 used, tried;
} slot_t;

static slot_t g_slots[THUMB_SLOTS];

static SDL_Surface *shrink(SDL_Surface *src) {
    SDL_Surface *s = SDL_ConvertSurfaceFormat(src, SDL_PIXELFORMAT_ARGB8888, 0);
    if (!s) return NULL;
    int w = s->w, h = s->h;
    if (w <= THUMB_PX && h <= THUMB_PX) return s;
    if (w >= h) { h = h * THUMB_PX / w; w = THUMB_PX; } else { w = w * THUMB_PX / h; h = THUMB_PX; }
    if (w < 1) w = 1;
    if (h < 1) h = 1;
    SDL_Surface *d = SDL_CreateRGBSurfaceWithFormat(0, w, h, 32, SDL_PIXELFORMAT_ARGB8888);
    if (d && SDL_BlitScaled(s, NULL, d, NULL) != 0) { SDL_FreeSurface(d); d = NULL; }
    SDL_FreeSurface(s);
    return d;
}

static slot_t *find_slot(const char *path) {
    for (int i = 0; i < THUMB_SLOTS; i++) if (g_slots[i].path[0] && !strcmp(g_slots[i].path, path)) return &g_slots[i];
    slot_t *v = &g_slots[0];               /* chỗ trống, hoặc ảnh lâu chưa dùng nhất */
    for (int i = 0; i < THUMB_SLOTS; i++) {
        if (!g_slots[i].path[0]) { v = &g_slots[i]; break; }
        if (g_slots[i].used < v->used) v = &g_slots[i];
    }
    if (v->tex) SDL_DestroyTexture(v->tex);
    memset(v, 0, sizeof *v);
    snprintf(v->path, sizeof v->path, "%s", path);
    return v;
}

SDL_Texture *ui_thumb(const char *path, int *w, int *h) {
    if (!path || !path[0] || strlen(path) >= sizeof g_slots[0].path) return NULL;
    Uint32 now = SDL_GetTicks();
    slot_t *s = find_slot(path);
    s->used = now;
    if (s->tex) { if (w) *w = s->w; if (h) *h = s->h; return s->tex; }
    if (s->bad || (s->tried && now - s->tried < RETRY_MS)) return NULL;
    s->tried = now ? now : 1;
    struct stat st;
    if (stat(path, &st) != 0 || !S_ISREG(st.st_mode)) return NULL;     /* chưa tải xong: thử lại sau */
    SDL_Surface *img = IMG_Load(path);
    if (!img) { s->bad = 1; return NULL; }
    SDL_Surface *small = shrink(img);
    SDL_FreeSurface(img);
    if (!small) { s->bad = 1; return NULL; }
    s->tex = SDL_CreateTextureFromSurface(cat_get_renderer(), small);
    s->w = small->w; s->h = small->h;
    SDL_FreeSurface(small);
    if (!s->tex) { s->bad = 1; return NULL; }
    if (w) *w = s->w;
    if (h) *h = s->h;
    return s->tex;
}

int ui_thumb_draw(const char *path, SDL_Rect box) {
    int w = 0, h = 0;
    SDL_Texture *t = ui_thumb(path, &w, &h);
    if (!t || w <= 0 || h <= 0 || box.w <= 0 || box.h <= 0) return 0;
    int dw = box.w, dh = h * box.w / w;                 /* vừa khung, giữ tỉ lệ, căn giữa */
    if (dh > box.h) { dh = box.h; dw = w * box.h / h; }
    SDL_Rect d = { box.x + (box.w - dw) / 2, box.y + (box.h - dh) / 2, dw, dh };
    SDL_RenderCopy(cat_get_renderer(), t, NULL, &d);
    return 1;
}

int ui_thumb_ready(const char *path) {
    return ui_thumb(path, NULL, NULL) != NULL;
}
