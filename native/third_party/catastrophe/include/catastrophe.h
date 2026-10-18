/*
 * Catastrophe — A C UI toolkit for Allium on retro gaming handhelds
 *
 * Header-only library. Define CAT_IMPLEMENTATION in exactly ONE .c file
 * before including this header to generate the implementation.
 *
 *   #define CAT_IMPLEMENTATION
 *   #include "catastrophe.h"
 *
 * Dependencies: SDL2, SDL2_ttf, SDL2_image, C standard library, pthreads
 * Platforms:    tg5040 (TrimUI Brick/Smart Pro), tg5050 (TrimUI Smart Pro S),
 *               my355 (Miyoo Flip), macOS, Linux, Windows (dev/testing)
 *
 * License: MIT
 * https://github.com/Helaas/catastrophe
 */

#ifndef CATASTROPHE_H
#define CATASTROPHE_H

#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <SDL2/SDL_image.h>

#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <string.h>
#include <errno.h>
#include <math.h>
#include <time.h>
#include <sys/stat.h>
#include <dirent.h>
#if defined(__linux__) || defined(__APPLE__)
#include <poll.h>
#endif

#ifdef __linux__
#include <pthread.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <errno.h>
#include <sys/wait.h>
#if defined(PLATFORM_TG5040) || defined(PLATFORM_TG5050) || defined(PLATFORM_MY355) || defined(PLATFORM_MLP1)
#include <linux/input.h>
#include <sys/ioctl.h>
#include <poll.h>
#endif
#endif

#ifdef __APPLE__
#include <pthread.h>
#include <unistd.h>
#endif

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <direct.h>
#include <io.h>
#ifndef R_OK
#define R_OK 4
#endif
#ifndef X_OK
#define X_OK 0  /* X_OK not meaningful on Windows; fall back to existence check */
#endif
#ifndef access
#define access _access
#endif
#endif

/* ═══════════════════════════════════════════════════════════════════════════
 * Platform Detection
 * ═══════════════════════════════════════════════════════════════════════════ */

#if defined(PLATFORM_TG5040)
    #define CAT_PLATFORM_NAME "tg5040"
    #define CAT_PLATFORM_IS_DEVICE 1
#elif defined(PLATFORM_TG5050)
    #define CAT_PLATFORM_NAME "tg5050"
    #define CAT_PLATFORM_IS_DEVICE 1
#elif defined(PLATFORM_MY355)
    #define CAT_PLATFORM_NAME "my355"
    #define CAT_PLATFORM_IS_DEVICE 1
#elif defined(PLATFORM_MLP1)
    #define CAT_PLATFORM_NAME "mlp1"
    #define CAT_PLATFORM_IS_DEVICE 1
#elif defined(PLATFORM_MAC) || defined(__APPLE__)
    #define CAT_PLATFORM_NAME "mac"
    #define CAT_PLATFORM_IS_DEVICE 0
    #ifndef PLATFORM_MAC
        #define PLATFORM_MAC
    #endif
#elif defined(PLATFORM_WINDOWS) || defined(_WIN32)
    #define CAT_PLATFORM_NAME "windows"
    #define CAT_PLATFORM_IS_DEVICE 0
    #ifndef PLATFORM_WINDOWS
        #define PLATFORM_WINDOWS
    #endif
#elif defined(PLATFORM_LINUX) || defined(__linux__)
    #define CAT_PLATFORM_NAME "linux"
    #define CAT_PLATFORM_IS_DEVICE 0
    #ifndef PLATFORM_LINUX
        #define PLATFORM_LINUX
    #endif
#else
    #define CAT_PLATFORM_NAME "unknown"
    #define CAT_PLATFORM_IS_DEVICE 0
#endif

/* ═══════════════════════════════════════════════════════════════════════════
 * Constants & Return Codes
 * ═══════════════════════════════════════════════════════════════════════════ */

#define CAT_OK        0
#define CAT_ERROR    (-1)
#define CAT_CANCELLED (-2)

#define CAT__MLP1_DEFAULT_SDCARD_PATH "/mnt/sdcard"
#define CAT__RETRO_DEFAULT_SDCARD_PATH "/mnt/SDCARD"

#if defined(__GNUC__) || defined(__clang__)
#define CAT__MAYBE_UNUSED __attribute__((unused))
#else
#define CAT__MAYBE_UNUSED
#endif

/* Design reference width for scaling calculations */
#define CAT_REFERENCE_WIDTH 1024

/* Scaling damping factor for screens wider than reference */
#define CAT_SCALE_DAMPING 0.75f

/* Font bump: additive increase to base font sizes on larger logical screens.
   Reference is MY355 logical resolution (640/2 × 480/2 = 320×240). */
#define CAT_FONT_BUMP_MAX           5
#define CAT_FONT_BUMP_REF_LOGICAL_W 320
#define CAT_FONT_BUMP_REF_LOGICAL_H 240

/* Input timing defaults (milliseconds) */
#define CAT_INPUT_REPEAT_DELAY  225
#define CAT_INPUT_REPEAT_RATE    75
#define CAT_INPUT_DEBOUNCE       20
#ifdef PLATFORM_MY355
#define CAT_AXIS_DEADZONE     20000  /* MY355 joystick needs higher deadzone to avoid crosstalk */
#else
#define CAT_AXIS_DEADZONE     16000
#endif

/* Text scroll timing */
#define CAT_TEXT_SCROLL_SPEED     1
#define CAT_TEXT_SCROLL_PAUSE_MS 1000

/* Texture cache capacity */
/* 32 was too small once a view holds a page of tiles and a list scrolls covers
   past them: a 12-tile grid plus a scrolled game list evicts the tiles long
   before the user returns to them, so backing out re-decoded every icon. The
   entries are thumbnails, so the ceiling is tens of megabytes, not hundreds. */
#define CAT_TEXTURE_CACHE_SIZE 96
#define CAT_TEXT_CACHE_SIZE 64
#define CAT_TEXT_CACHE_MAX_TEXT 192

/* Max combo registrations */
#define CAT_MAX_COMBOS 16

/* Footer layout bookkeeping */
#define CAT__MAX_FOOTER_ITEMS 64

/* Input devices: max simultaneously tracked SDL joysticks/controllers */
#define CAT__MAX_PADS 8

/* Idle poll() wake descriptors (device only): bounded collection of non-grabbing,
   read-only evdev fds (virtual pad + external controllers). */
#define CAT__WAKE_MAX_FDS   4
#define CAT__WAKE_PATH_MAX  64
#define CAT__WAKE_RESCAN_MS 3000

/* Max log message length */
#define CAT_MAX_LOG_LEN 2048

/* ═══════════════════════════════════════════════════════════════════════════
 * Enums
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Virtual button abstraction — unifies keyboard, joystick, gamepad input */
typedef enum {
    CAT_BTN_NONE = 0,
    CAT_BTN_UP,
    CAT_BTN_DOWN,
    CAT_BTN_LEFT,
    CAT_BTN_RIGHT,
    CAT_BTN_A,
    CAT_BTN_B,
    CAT_BTN_X,
    CAT_BTN_Y,
    CAT_BTN_L1,
    CAT_BTN_L2,
    CAT_BTN_R1,
    CAT_BTN_R2,
    CAT_BTN_START,
    CAT_BTN_SELECT,
    CAT_BTN_MENU,
    CAT_BTN_POWER,
    CAT_BTN_QUIT,
    CAT_BTN_STICK,   /* analog-stick click (L3/thumb) */
    CAT_BTN_COUNT
} cat_button;

/* Font size tiers — scaled to screen resolution at init */
typedef enum {
    CAT_FONT_EXTRA_LARGE = 0,  /* Base: 24 × device_scale */
    CAT_FONT_LARGE,             /* Base: 16 × device_scale */
    CAT_FONT_MEDIUM,            /* Base: 14 × device_scale */
    CAT_FONT_SMALL,             /* Base: 12 × device_scale */
    CAT_FONT_TINY,              /* Base: 10 × device_scale */
    CAT_FONT_MICRO,             /* Base:  7 × device_scale */
    CAT_FONT_TIER_COUNT
} cat_font_tier;

/* Text alignment */
typedef enum {
    CAT_ALIGN_LEFT = 0,
    CAT_ALIGN_CENTER,
    CAT_ALIGN_RIGHT
} cat_text_align;

/* List actions returned by widgets */
typedef enum {
    CAT_ACTION_SELECTED = 0,
    CAT_ACTION_BACK,
    CAT_ACTION_TRIGGERED,
    CAT_ACTION_SECONDARY_TRIGGERED,
    CAT_ACTION_CONFIRMED,
    CAT_ACTION_TERTIARY_TRIGGERED,
    CAT_ACTION_OPTION_CHANGED,
    CAT_ACTION_REFRESH,
    CAT_ACTION_CUSTOM = CAT_ACTION_TRIGGERED
} cat_list_action;

/* CPU speed presets — platform-transparent, see cat_set_cpu_speed().
 * Approximate frequencies per platform:
 *
 *   Preset           MY355        TG5040       TG5050 (big core)
 *   CAT_CPU_SPEED_MENU       600 MHz      600 MHz      672 MHz
 *   CAT_CPU_SPEED_POWERSAVE 1200 MHz     1200 MHz     1200 MHz
 *   CAT_CPU_SPEED_NORMAL    1608 MHz     1608 MHz     1680 MHz  ← standard pak speed
 *   CAT_CPU_SPEED_PERFORMANCE 2000 MHz  2000 MHz     2160 MHz
 *
 * CAT_CPU_SPEED_DEFAULT (0) means "do not change" and is the zero-init default. */
typedef enum {
    CAT_CPU_SPEED_DEFAULT     = 0, /* do not change at init */
    CAT_CPU_SPEED_MENU,            /* ~600–672 MHz  — light UI / menus    */
    CAT_CPU_SPEED_POWERSAVE,       /* ~1200 MHz     — battery saving       */
    CAT_CPU_SPEED_NORMAL,          /* ~1608–1680 MHz — standard pak speed  */
    CAT_CPU_SPEED_PERFORMANCE,     /* ~2000–2160 MHz — max speed           */
} cat_cpu_speed;

typedef enum {
    CAT_FAN_MODE_UNSUPPORTED = -1,
    CAT_FAN_MODE_MANUAL = 0,
    CAT_FAN_MODE_AUTO_QUIET,
    CAT_FAN_MODE_AUTO_NORMAL,
    CAT_FAN_MODE_AUTO_PERFORMANCE,
} cat_fan_mode;

/* Platform service callbacks supplied by a host app/launcher when Catastrophe
 * should not probe hardware directly. Return negative values for unavailable
 * reads, CAT_ERROR for unhandled actions, and CAT_OK for handled actions. */
typedef struct {
    void *userdata;
    int (*wifi_strength)(void *userdata);      /* 0..3, negative if unknown */
    int (*battery_percent)(void *userdata);    /* 0..100, negative if unknown */
    int (*charging_state)(void *userdata);     /* 0/1, negative if unknown */
    int (*set_fan_mode)(void *userdata, cat_fan_mode mode);
    cat_fan_mode (*get_fan_mode)(void *userdata);
    int (*set_fan_speed)(void *userdata, int percent);
    int (*get_fan_speed)(void *userdata);
    int (*power_suspend)(void *userdata);
    int (*power_shutdown)(void *userdata);
} cat_platform_services;

/* ═══════════════════════════════════════════════════════════════════════════
 * Color Type — matches Allium's RGBA uint32 layout
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * cat_color stores RGBA as a uint32_t:
 *   bits  0–7:  Red
 *   bits  8–15: Green
 *   bits 16–23: Blue
 *   bits 24–31: Alpha
 *
 * Conversion helpers bridge to SDL_Color for rendering. */

typedef uint32_t cat_color;

#define CAT_COLOR_R(c)  ((uint8_t)((c) >> 0))
#define CAT_COLOR_G(c)  ((uint8_t)((c) >> 8))
#define CAT_COLOR_B(c)  ((uint8_t)((c) >> 16))
#define CAT_COLOR_A(c)  ((uint8_t)((c) >> 24))

static inline cat_color cat_color_rgba(uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    return (uint32_t)r | ((uint32_t)g << 8) | ((uint32_t)b << 16) | ((uint32_t)a << 24);
}

static inline cat_color cat_color_rgb(uint8_t r, uint8_t g, uint8_t b) {
    return cat_color_rgba(r, g, b, 0xFF);
}

static inline cat_color cat_color_with_r(cat_color c, uint8_t r) {
    return (c & 0xFFFFFF00) | r;
}

static inline cat_color cat_color_with_g(cat_color c, uint8_t g) {
    return (c & 0xFFFF00FF) | ((uint32_t)g << 8);
}

static inline cat_color cat_color_with_b(cat_color c, uint8_t b) {
    return (c & 0xFF00FFFF) | ((uint32_t)b << 16);
}

static inline cat_color cat_color_with_a(cat_color c, uint8_t a) {
    return (c & 0x00FFFFFF) | ((uint32_t)a << 24);
}

static inline cat_color cat_color_blend(cat_color c, cat_color other, uint8_t alpha) {
    return cat_color_rgb(
        (uint8_t)(((int)CAT_COLOR_R(c) * (255 - alpha) + (int)CAT_COLOR_R(other) * alpha) / 255),
        (uint8_t)(((int)CAT_COLOR_G(c) * (255 - alpha) + (int)CAT_COLOR_G(other) * alpha) / 255),
        (uint8_t)(((int)CAT_COLOR_B(c) * (255 - alpha) + (int)CAT_COLOR_B(other) * alpha) / 255)
    );
}

static inline cat_color cat_color_invert(cat_color c) {
    return cat_color_rgb(255 - CAT_COLOR_R(c), 255 - CAT_COLOR_G(c), 255 - CAT_COLOR_B(c));
}

static inline bool cat_color_is_dark(cat_color c) {
    return CAT_COLOR_R(c) < 128 && CAT_COLOR_G(c) < 128 && CAT_COLOR_B(c) < 128;
}

static inline cat_color cat_color_from_hex(const char *hex) {
    if (!hex) return 0;
    if (hex[0] == '#') hex++;
    else if (hex[0] == '0' && (hex[1] == 'x' || hex[1] == 'X')) hex += 2;
    unsigned long val = strtoul(hex, NULL, 16);
    if (strlen(hex) == 8) {
        /* RRGGBBAA — matches CSS and cat_color_to_hex output */
        uint8_t r = (uint8_t)((val >> 24) & 0xFF);
        uint8_t g = (uint8_t)((val >> 16) & 0xFF);
        uint8_t b = (uint8_t)((val >>  8) & 0xFF);
        uint8_t a = (uint8_t)( val        & 0xFF);
        return cat_color_rgba(r, g, b, a);
    }
    uint8_t r = (uint8_t)((val >> 16) & 0xFF);
    uint8_t g = (uint8_t)((val >>  8) & 0xFF);
    uint8_t b = (uint8_t)( val        & 0xFF);
    return cat_color_rgb(r, g, b);
}

static inline void cat_color_to_hex(cat_color c, char *buf, size_t buf_size) {
    if (!buf || buf_size < 8) return;
    uint8_t a = CAT_COLOR_A(c);
    if (a == 255)
        snprintf(buf, buf_size, "#%02x%02x%02x", CAT_COLOR_R(c), CAT_COLOR_G(c), CAT_COLOR_B(c));
    else
        snprintf(buf, buf_size, "#%02x%02x%02x%02x", CAT_COLOR_R(c), CAT_COLOR_G(c), CAT_COLOR_B(c), a);
}

static inline SDL_Color cat_color_to_sdl(cat_color c) {
    SDL_Color sdl;
    sdl.r = CAT_COLOR_R(c);
    sdl.g = CAT_COLOR_G(c);
    sdl.b = CAT_COLOR_B(c);
    sdl.a = CAT_COLOR_A(c);
    return sdl;
}

/* Immediate drawing color. This intentionally remains SDL_Color-shaped while
 * cat_color is the packed stylesheet/color-storage type above. */
typedef SDL_Color cat_draw_color;

static inline cat_draw_color cat_draw_color_lerp(cat_draw_color from,
                                                  cat_draw_color to, float t) {
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    cat_draw_color out = {
        (uint8_t)(from.r + (to.r - from.r) * t + 0.5f),
        (uint8_t)(from.g + (to.g - from.g) * t + 0.5f),
        (uint8_t)(from.b + (to.b - from.b) * t + 0.5f),
        (uint8_t)(from.a + (to.a - from.a) * t + 0.5f),
    };
    return out;
}

/* Legacy alias retained for existing Apostrophe-era consumers. */
typedef cat_draw_color ap_color;

/* ═══════════════════════════════════════════════════════════════════════════
 * Theme — runtime theme struct populated from cat_stylesheet (6-color + font + bg)
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef struct cat_theme {
    cat_draw_color highlight;         /* Selected item pill background */
    cat_draw_color accent;            /* Footer outer pill, status bar bg */
    cat_draw_color button_label;      /* Text inside footer button pills */
    cat_draw_color button_glyph_bg;   /* Inner A/B/X/Y pill background (defaults to highlight) */
    cat_draw_color text;              /* Default text color */
    cat_draw_color highlighted_text;  /* Text on highlighted/selected items */
    cat_draw_color disabled;          /* Disabled or unavailable list/menu items */
    cat_draw_color hint;              /* Help text, dim text */
    cat_draw_color emphasis;          /* Section headings / status — the highlight hue,
                                         luminance-clamped to read on the background
                                         (computed in cat_finalize_theme_colors) */
    cat_draw_color background;        /* Screen background color */
    char     font_path[512];    /* Primary font file path */
    char     bg_image_path[512];/* Background image path (PNG) */
    /* Pill geometry — sourced from cat_stylesheet.ui */
    int      ui_padding_x;      /* Pill internal horizontal padding (unscaled; pass through CAT_DS) */
    int      ui_padding_y;      /* Pill internal vertical padding (unscaled) */
    float    pill_radius_ratio; /* Corner radius as a fraction of pill height/2. 0=rectangle, 1=full cap. */
    int      pill_corner_mask;  /* Which corners round (CAT_CORNER_* bitmask). 0/ALL = all four (default). */
} cat_theme;

/* Legacy alias retained for existing Apostrophe-era consumers. */
typedef cat_theme ap_theme;

/* ═══════════════════════════════════════════════════════════════════════════
 * Stylesheet — Allium theme data model (JSON-driven)
 * ═══════════════════════════════════════════════════════════════════════════ */

typedef struct {
    char     path[1024];       /* TTF file path */
    int      size;             /* Font point size */
    TTF_Font *font;            /* Loaded font (NULL until loaded) */
} cat_stylesheet_font;

typedef struct {
    int               margin_x;                /* default: 12 */
    int               margin_y;                /* default: 8 */
    int               list_margin;             /* default: 4 */
    int               padding_x;               /* default: 12 */
    int               padding_y;               /* default: 4 */
    cat_stylesheet_font ui_font;
    cat_color         text_color;              /* default: #ffffff */
    cat_color         text_stroke_color;       /* default: #00000000 */
    cat_color         background_color;        /* default: #000000 */
    cat_color         highlight_color;         /* default: #7287fd */
    cat_color         highlight_text_color;    /* default: #ffffff */
    cat_color         highlight_text_stroke_color; /* default: #00000000 */
    cat_color         disabled_color;          /* default: #585b70 */
    /* Catastrophe extension: explicit color for "hint" text (per-row counts, footer status,
       button-hint labels, scrollbars). When unset (0), falls back to disabled_color so all
       existing Allium/Jawaka themes render identically. */
    cat_color         hint_color;              /* default: 0 (= unset → use disabled_color) */
    float             tab_font_size;           /* default: 1.0 */
    cat_color         tab_color;               /* default: #ffffff70 */
    cat_color         tab_stroke_color;        /* default: #00000000 */
    cat_color         tab_selected_color;      /* default: #ffffff */
    cat_color         tab_selected_stroke_color; /* default: #00000000 */
    uint32_t          stroke_width;            /* default: 0 */
    /* Catastrophe extension: pill corner radius as a fraction of pill height/2.
       1.0 = full cap (round pill), 0.0 = sharp rectangle. Allium themes that don't set
       this stay at the default 1.0. */
    float             pill_radius_ratio;       /* default: 1.0 */
} cat_stylesheet_ui;

typedef struct {
    bool              show_battery_level;      /* default: false */
    bool              show_clock;              /* default: true */
    bool              show_wifi;               /* default: false */
    float             font_size;               /* default: 1.0 (multiplier) */
    cat_color         text_color;              /* default: #ffffff */
    cat_color         text_stroke_color;       /* default: #00000000 */
} cat_stylesheet_status_bar;

typedef struct {
    float             button_hint_font_size;   /* default: 0.9 */
    float             button_size;             /* default: 1.0 */
    float             button_text_font_size;   /* default: 0.75 */
    cat_color         button_a_color;          /* default: #eb1a1d */
    cat_color         button_b_color;          /* default: #fece15 */
    cat_color         button_x_color;          /* default: #0749b4 */
    cat_color         button_y_color;          /* default: #008d45 */
    cat_color         button_bg_color;         /* default: #585b70 */
    cat_color         button_text_color;       /* default: #ffffff */
    cat_color         text_color;              /* default: #ffffff */
    /* Catastrophe extension: explicit color for the inner A/B/X/Y pill behind the glyph
       letter. When unset (0), falls back to ui.highlight_color so existing themes are
       unaffected. Letting themes set this independently decouples the button-hint glyph
       from the list-selection bg. */
    cat_color         glyph_bg_color;          /* default: 0 (= unset → use highlight_color) */
} cat_stylesheet_button_hints;

typedef struct {
    bool              use_recents_carousel;    /* default: false */
} cat_stylesheet_recents;

typedef struct {
    uint32_t          boxart_width;            /* default: 250 */
} cat_stylesheet_games;

typedef struct {
    cat_color         background_color;        /* default: #000000 */
    cat_stylesheet_font guide_font;
} cat_stylesheet_menu;

typedef enum {
    CAT_LAUNCHER_TABBED     = 0,  /* tab bar with category switching (default) */
    CAT_LAUNCHER_VERTICAL   = 1,  /* NextUI-style: flat left nav list + right preview */
    CAT_LAUNCHER_HORIZONTAL = 2,  /* kUI-style: horizontal parallelogram carousel */
    CAT_LAUNCHER_COVERFLOW  = 3,  /* icon coverflow carousel */
    CAT_LAUNCHER_GRID       = 4,  /* vertical-scrolling grid of rounded system tiles */
} cat_launcher_layout;

typedef struct {
    cat_launcher_layout layout;              /* default: CAT_LAUNCHER_TABBED */
    float               list_split;         /* vertical: left panel width [0..1], default 0.45 */
    int                 carousel_skew;      /* horizontal: parallelogram skew px, default 30 */
    /* Coverflow tunables (CAT_LAUNCHER_COVERFLOW only) */
    int                 coverflow_icon_size;     /* center icon px (logical), default 256 */
    int                 coverflow_side_size;     /* side icon px (logical), default 160 */
    int                 coverflow_spacing;       /* px between icon centers, default 280 */
    uint8_t             coverflow_side_alpha;    /* default 140 */
    uint32_t            coverflow_anim_ms;       /* slide duration ms, default 180 */
    char                coverflow_icon_dir[128]; /* relative to theme dir, default "system_icons" */
    /* Grid tunables (CAT_LAUNCHER_GRID only). Icon size is NOT a tunable: it is
       derived from cols/rows and the available region so any density fills the
       screen exactly (see plans/grid-view-and-user-themes.md). */
    int                 grid_cols;               /* visible columns, default 4 */
    int                 grid_rows;               /* visible rows, default 3 */
    int                 grid_margin;             /* outer inset px (logical), default 45 */
    int                 grid_gutter;             /* px between tiles, default 48 */
    int                 grid_radius_pct;         /* corner radius as % of tile, default 18 */
    int                 grid_border_w;           /* unfocused border px, 0 = none, default 2 */
    int                 grid_focus_border_w;     /* focused border px, default 3 */
    int                 grid_focus_scale_pct;    /* focused tile size, % of tile, default 110 */
    int                 grid_status_scale_pct;   /* status cluster size, % of the stock bar, default 70 */
    cat_color           grid_border_color;       /* default white @ 60/255 */
    cat_color           grid_focus_border_color; /* default the theme accent */
    uint32_t            grid_anim_ms;            /* row scroll duration ms, default 180 */
    char                grid_icon_dir[128];      /* relative to theme dir, default "system_icons" */
    char                grid_label_dir[128];     /* full-tile overlays, default "grid_labels" */
} cat_stylesheet_launcher;

typedef struct {
    char                      wallpaper[1024]; /* empty = no wallpaper */
    cat_stylesheet_ui         ui;
    cat_stylesheet_status_bar  status_bar;
    cat_stylesheet_button_hints button_hints;
    cat_stylesheet_recents    recents;
    cat_stylesheet_games      games;
    cat_stylesheet_menu       menu;
    cat_stylesheet_font       cjk_font;
    cat_stylesheet_launcher   launcher;
} cat_stylesheet;

/* Stylesheet color slot enum — maps to Allium's StylesheetColor */
typedef enum {
    CAT_SC_FOREGROUND,
    CAT_SC_BACKGROUND,
    CAT_SC_HIGHLIGHT,
    CAT_SC_HIGHLIGHT_TEXT,
    CAT_SC_DISABLED,
    CAT_SC_TAB,
    CAT_SC_TAB_SELECTED,
    CAT_SC_BUTTON_A,
    CAT_SC_BUTTON_B,
    CAT_SC_BUTTON_X,
    CAT_SC_BUTTON_Y,
    CAT_SC_BUTTON_BACKGROUND,
    CAT_SC_BUTTON_TEXT,
    CAT_SC_BUTTON_HINT_TEXT,
    CAT_SC_BACKGROUND_HIGHLIGHT_BLEND,
    CAT_SC_STROKE,
    CAT_SC_HIGHLIGHT_TEXT_STROKE,
    CAT_SC_TAB_STROKE,
    CAT_SC_TAB_SELECTED_STROKE,
    CAT_SC_STATUS_BAR,
    CAT_SC_STATUS_BAR_STROKE,
    CAT_SC_MENU_BACKGROUND,
    CAT_SC_COUNT
} cat_stylesheet_color_slot;

static inline cat_color cat_stylesheet_get_color(const cat_stylesheet *s, cat_stylesheet_color_slot slot) {
    switch (slot) {
        case CAT_SC_FOREGROUND: return s->ui.text_color;
        case CAT_SC_BACKGROUND: return s->ui.background_color;
        case CAT_SC_HIGHLIGHT: return s->ui.highlight_color;
        case CAT_SC_HIGHLIGHT_TEXT: return s->ui.highlight_text_color;
        case CAT_SC_DISABLED: return s->ui.disabled_color;
        case CAT_SC_TAB: return s->ui.tab_color;
        case CAT_SC_TAB_SELECTED: return s->ui.tab_selected_color;
        case CAT_SC_BUTTON_A: return s->button_hints.button_a_color;
        case CAT_SC_BUTTON_B: return s->button_hints.button_b_color;
        case CAT_SC_BUTTON_X: return s->button_hints.button_x_color;
        case CAT_SC_BUTTON_Y: return s->button_hints.button_y_color;
        case CAT_SC_BUTTON_BACKGROUND: return s->button_hints.button_bg_color;
        case CAT_SC_BUTTON_TEXT: return s->button_hints.button_text_color;
        case CAT_SC_BUTTON_HINT_TEXT: return s->button_hints.text_color;
        case CAT_SC_BACKGROUND_HIGHLIGHT_BLEND: return cat_color_blend(s->ui.background_color, s->ui.highlight_color, 128);
        case CAT_SC_STROKE: return s->ui.text_stroke_color;
        case CAT_SC_HIGHLIGHT_TEXT_STROKE: return s->ui.highlight_text_stroke_color;
        case CAT_SC_TAB_STROKE: return s->ui.tab_stroke_color;
        case CAT_SC_TAB_SELECTED_STROKE: return s->ui.tab_selected_stroke_color;
        case CAT_SC_STATUS_BAR: return s->status_bar.text_color;
        case CAT_SC_STATUS_BAR_STROKE: return s->status_bar.text_stroke_color;
        case CAT_SC_MENU_BACKGROUND: return s->menu.background_color;
        default: return 0;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Stylesheet API — loading, applying, scanning
 * ═══════════════════════════════════════════════════════════════════════════ */

void   cat_stylesheet_init_default(cat_stylesheet *s);
int    cat_stylesheet_load_file(cat_stylesheet *s, const char *path);
int    cat_stylesheet_load_theme(cat_stylesheet *s, const char *theme_name);
int    cat_stylesheet_apply(cat_stylesheet *s);
int    cat_stylesheet_available_themes(const char ***out_names, int *out_count);
void   cat_stylesheet_free_theme_list(const char **names, int count);
int    cat_stylesheet_list_wallpapers(const char *theme_dir, const char ***out, int *count);
void   cat_stylesheet_free_string_list(const char **names, int count);
const char *cat_get_active_theme_dir(void);
const char *cat_get_active_theme_name(void);
int    cat_reload_fonts(const char *font_path);
int    cat_theme_state_load(char *out_name, size_t out_size);
int    cat_theme_state_save(const char *theme_name);

/* Input event — unified from all input sources */
typedef struct {
    cat_button button;
    bool      pressed;   /* true = down, false = up */
    bool      repeated;  /* true = generated by hold-repeat, false = fresh press */
} cat_input_event;

/* Text scroll state — horizontal ping-pong scrolling for overflow text */
typedef struct {
    int   offset;
    int   direction;     /* 1 = right-to-left, -1 = left-to-right */
    int   pause_timer;   /* ms remaining in pause at each end */
    bool  active;
} cat_text_scroll;

/* Footer help item — button hint displayed at screen bottom */
typedef struct {
    cat_button    button;
    const char  *label;
    bool         is_confirm;  /* true = right-aligned confirm group */
    const char  *button_text; /* Optional footer pill text override (display-only) */
} cat_footer_item;

/* Global footer overflow behaviour */
typedef struct {
    bool      enabled;  /* When false, footer hints render without overflow handling */
    cat_button chord_a;  /* First button in the hidden-actions chord */
    cat_button chord_b;  /* Second button in the hidden-actions chord */
} cat_footer_overflow_opts;

/* Clock display mode for cat_status_bar_opts.show_clock */
#define CAT_CLOCK_AUTO  0  /* Follow active stylesheet's status_bar.show_clock (default) */
#define CAT_CLOCK_SHOW  1  /* Always show, regardless of stylesheet                       */
#define CAT_CLOCK_HIDE  2  /* Always hide, regardless of stylesheet                       */

/* Status bar options */
typedef struct {
    int          show_clock;    /* CAT_CLOCK_AUTO/SHOW/HIDE (default: CAT_CLOCK_AUTO) */
    bool         use_24h;       /* 24-hour clock if true; 12-hour if false */
    bool         show_battery;  /* Show battery icon from device or desktop preview state */
    bool         show_battery_level; /* Show numeric "85%" next to the battery icon */
    bool         show_wifi;     /* Show wifi icon from device or desktop preview state */
    bool         wifi_supplied; /* If true, use wifi_strength below (caller owns the radio read);
                                   if false, Catastrophe self-reads via wpa_cli/iw (legacy/preview). */
    int          wifi_strength; /* 0=none..3=strong, supplied by the caller when wifi_supplied */
    bool         show_volume;   /* Show speaker icon; volume_percent must be supplied by the caller */
    int          volume_percent;/* 0-100 current volume, or -1 if unknown (icon hidden) */
    bool         show_bluetooth;/* Show bluetooth icon (always when set; appearance tracks bt_state) */
    int          bt_state;      /* 0=off (dim), 1=on, 2=connected — supplied by the caller */
    bool         no_ampm;       /* For 12-hour mode: skip AM/PM suffix */
    bool         no_pill;       /* Draw icons/text inline without pill background */
    bool         use_y;         /* If true, use y_position instead of default padding */
    int          y_position;    /* Custom y position (requires use_y = true) */
    const char  *alert_text;    /* Optional short warning drawn first (e.g. "SD read-only");
                                   not a user toggle, so it shows whatever else is hidden */
} cat_status_bar_opts;

/* Screen fade overlay — per-frame draw state for fade-in / fade-out transitions */
typedef struct {
    uint32_t start_ms;     /* SDL_GetTicks() value when fade began */
    int      duration_ms;  /* Total duration of the fade */
    bool     fade_in;      /* true = black→transparent, false = transparent→black */
    bool     active;       /* false = not animating */
} cat_fade;

/* Texture cache entry */
typedef struct {
    char          key[256];
    SDL_Texture  *texture;
    int           w, h;
    uint32_t      last_used;
} cat_cache_entry;

/* Texture cache (LRU) */
typedef struct {
    cat_cache_entry entries[CAT_TEXTURE_CACHE_SIZE];
    int            count;
} cat_texture_cache;

/* Rendered text texture cache entry */
typedef struct {
    TTF_Font     *font;
    uint32_t      hash;
    char          text[CAT_TEXT_CACHE_MAX_TEXT];
    SDL_Texture  *texture;
    int           w, h;
    uint32_t      last_used;
} cat_text_cache_entry;

/* Rendered text texture cache (LRU) */
typedef struct {
    cat_text_cache_entry entries[CAT_TEXT_CACHE_SIZE];
    int                  count;
} cat_text_cache;

/* Sequence buffer entry (internal) */
typedef struct {
    cat_button  button;
    uint32_t   time_ms;
} cat__seq_entry;

/* Combo types and callbacks */
typedef enum { CAT_COMBO_CHORD, CAT_COMBO_SEQUENCE } cat_combo_type;
typedef void (*cat_combo_callback)(const char *id, cat_combo_type type, void *userdata);

/* Combo registration */
typedef struct {
    char       id[64];
    cat_button  buttons[8];
    int        button_count;
    uint32_t   window_ms;       /* Time window for chord/sequence */
    bool       is_sequence;     /* false = chord (simultaneous), true = sequence */
    bool       strict;          /* For sequences: must be exact order */
    bool       active;
    cat_combo_callback on_trigger;
    cat_combo_callback on_release;   /* Chords only */
    void             *userdata;
} cat_combo;

/* Combo event */
typedef struct {
    const char    *id;
    bool           triggered;
    cat_combo_type  type;
} cat_combo_event;

/* Configuration passed to cat_init() */
typedef struct {
    const char *window_title;     /* Window title (dev mode only) */
    const char *font_path;        /* Path to .ttf font file, NULL = auto */
    const char *bg_image_path;    /* Background image path, NULL = none */
    const char *log_path;         /* Log file path, NULL = stderr only */
    const char *primary_color_hex;/* Override accent color "#RRGGBB", NULL = theme default */
    bool        disable_background;  /* Set true to skip bg.png rendering */
    cat_cpu_speed cpu_speed;       /* Set CPU at init; 0 = CAT_CPU_SPEED_DEFAULT (no-op) */
    bool        disable_font_bump;   /* Set true to disable automatic font bumping */
    bool        start_hidden;        /* Create the window hidden (SDL_WINDOW_HIDDEN);
                                        caller shows it later with cat_show_window().
                                        Lets a daemon warm up a window behind another
                                        fullscreen app without mapping a surface. */
    bool        defer_input_init;    /* Skip the joystick subsystem + device scan in
                                        cat_init (~200ms on device); the caller runs it
                                        later via cat_init_input(), e.g. after the first
                                        frame is on screen. Input polling lazily
                                        initializes it as a fallback. */
} cat_config;

/* ═══════════════════════════════════════════════════════════════════════════
 * Internal State (opaque to user, but defined here for header-only use)
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Tracked input device; gc set => opened via SDL_GameControllerOpen and the
   underlying joystick is owned by it, joy set without gc => raw SDL joystick
   (e.g. the virtual Loong pad) using the platform button map. */
typedef struct {
    SDL_JoystickID      id;   /* -1 = empty slot */
    SDL_GameController *gc;
    SDL_Joystick       *joy;
} cat__pad;

typedef struct {
    /* SDL */
    SDL_Window         *window;
    SDL_Renderer       *renderer;
    SDL_Joystick       *joystick;
    SDL_GameController *controller;
    cat__pad            pads[CAT__MAX_PADS];
    int                 pad_count;
    SDL_Texture        *bg_texture;
    SDL_Texture        *rounded_scratch;     /* render target for cat_draw_image_rounded_ex */
    int                 rounded_scratch_w;
    int                 rounded_scratch_h;
    int                 screen_w;
    int                 screen_h;
    bool                renderer_has_vsync;
    uint32_t            last_present_ms;
    bool                needs_frame;       /* true = render next frame at 60fps */
    uint32_t            next_redraw_ms;    /* absolute time of next scheduled redraw (0 = none) */
    int                 wake_fds[CAT__WAKE_MAX_FDS];      /* evdev fds for idle poll() wake (-1 = unused) */
    char                wake_paths[CAT__WAKE_MAX_FDS][CAT__WAKE_PATH_MAX];
    int                 wake_fd_count;
    uint32_t            wake_rescan_ms;    /* next periodic wake-set rescan */
    bool                wake_dirty;        /* pads added/removed: rebind wake fds early */
    int                 idle_wake_fd;      /* borrowed application wake source, -1 = none */
    bool                input_backend_ready; /* joystick subsystem + devices opened */

    /* Scaling */
    float         scale_factor;

    /* Runtime theme view derived from the active stylesheet. */
    cat_theme     theme;

    /* Stylesheet (Allium 1:1) */
    cat_stylesheet stylesheet;

    /* Themes directory (from CAT_THEMES_DIR env var or platform default) */
    char          themes_dir[1024];

    /* Active theme name (set by cat_stylesheet_load_theme/apply) */
    char          active_theme[256];
    /* Directory containing the active theme's stylesheet.json (parent of active_theme/) */
    char          active_theme_dir[1024];

    /* Fonts */
    TTF_Font     *fonts[CAT_FONT_TIER_COUNT];
    /* Glyph-complete font for UI symbols (keyboard ⇧ ← ↵) the themed family may
       lack. Loaded lazily from the bundled base font.ttf, family-independent. */
    TTF_Font     *symbol_font;
    int           symbol_font_bump;
    /* Same glyph-complete base font, sized for footer/hint pills (not LARGE). */
    TTF_Font     *symbol_font_small;
    int           symbol_font_small_bump;
    /* CJK face from the stylesheet's cjk_font slot, opened lazily per tier. The
       themed families carry no CJK at all, so a Chinese or Japanese ROM name
       renders as tofu without this. Substituted per string, not per glyph:
       whole-string, so no run splitting and no metric surprises. */
    TTF_Font     *cjk_fonts[CAT_FONT_TIER_COUNT];
    int           cjk_font_bump;
    char          cjk_font_rel[512];   /* active face: CAT_CJK_FONT_PATH or stylesheet */
    bool          cjk_font_missing;    /* resolution already failed; stop retrying */

    /* Input state */
    bool          face_buttons_flipped;
    uint32_t      input_delay_ms;
    uint32_t      input_repeat_delay_ms;
    uint32_t      input_repeat_rate_ms;
    uint32_t      last_input_time;

    /* Directional repeat */
    uint8_t       hat_held;
    uint32_t      hat_repeat_time;
    int           axis_held_dir_y;   /* -1=up, +1=down, 0=none */
    int           axis_held_dir_x;   /* -1=left, +1=right, 0=none */
    /* Last canonical stick positions (GameController path), for left/right
       stick arbitration — left wins, right drives only while left is idle. */
    int           axis_stick_lx;
    int           axis_stick_ly;
    int           axis_stick_rx;
    int           axis_stick_ry;
    uint32_t      axis_repeat_time_y;
    uint32_t      axis_repeat_time_x;

    /* Combos */
    cat_combo      combos[CAT_MAX_COMBOS];
    int           combo_count;

    /* Combo event queue */
    cat_combo_event combo_queue[16];
    int            combo_queue_head;
    int            combo_queue_tail;

    /* Sequence detection buffer */
    cat__seq_entry seq_buffer[20];
    int           seq_buffer_count;

    /* Chord held tracking (separate from cat_combo.active which means "registered") */
    bool          combo_held[CAT_MAX_COMBOS];

    /* Button held state for chords */
    bool          buttons_held[CAT_BTN_COUNT];
    uint32_t      button_press_time[CAT_BTN_COUNT];
    uint32_t      button_repeat_time[CAT_BTN_COUNT];
    bool          shoulder_repeat;   /* opt-in: hold-repeat L1/R1 (default off) */

    /* Tab bar: right-side space to leave for an overlaid inline status bar. */
    int           tab_bar_reserved_right;

    /* Footer overflow */
    cat_footer_overflow_opts footer_overflow_opts;
    bool          footer_overflow_active;
    bool          footer_overflow_chord_held;
    bool          footer_overflow_open_requested;
    bool          footer_overflow_overlay_open;
    bool          footer_overflow_swallow[CAT_BTN_COUNT];
    cat_footer_item footer_hidden_items[CAT__MAX_FOOTER_ITEMS];
    int            footer_hidden_count;
    /* One-shot alpha (0 = opaque default) for the NEXT cat_draw_footer's pill
       backgrounds; auto-resets after that draw so it never leaks to other footers. */
    int            footer_bg_opacity;

    /* Texture cache */
    cat_texture_cache tex_cache;
    cat_text_cache    text_cache;

    /* Logging */
    FILE         *log_file;

    /* Status bar assets (icon + pill-cap spritesheet) */
    SDL_Texture  *status_assets;
    int           status_asset_scale;  /* 1–4, matches loaded spritesheet */

    /* Integer device scale (1× or 2× depending on hardware) for pixel-perfect bars */
    int           device_scale;   /* typically 2 (handheld) or 3 (brick) */
    int           device_padding; /* Screen-edge padding constant (unscaled) */
    int           font_bump;     /* additive bump applied to base font sizes (0–5) */
    cat_platform_services platform_services;

    /* WiFi signal strength cache (device only, 5s poll interval) */
    #if CAT_PLATFORM_IS_DEVICE
    int           cached_wifi_strength;     /* 0=off, 1=low, 2=med, 3=high */
    uint32_t      wifi_cache_time_ms;       /* SDL_GetTicks() when last polled */
    #endif

    /* Power button handling */
    bool          power_handler_enabled;
    #if CAT_PLATFORM_IS_DEVICE
    pthread_t     power_thread;
    bool          power_thread_running;
    int           power_fd;
    #endif

    /* Initialization flag */
    bool          initialized;
} cat__state;

/* ═══════════════════════════════════════════════════════════════════════════
 * Macros
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Scale a design-space value to screen space */
#define CAT_S(base) ((int)((base) * cat__g.scale_factor))

/* Scale using integer device scale (for pixel-perfect bar sizing) */
#define CAT_DS(base) ((base) * cat__g.device_scale)

/* Status bar layout constants (all unscaled, multiply by device_scale) */
#define CAT__PILL_SIZE       30  /* Height of status/footer pill */
#define CAT__BUTTON_SIZE     20  /* Button circle size inside footer */
#define CAT__BUTTON_MARGIN    5  /* Margin between pill edge and button / inter-element gap */

/* Float lerp/clamp — used by animation helpers */
static inline float cat__lerpf(float a, float b, float t) { return a + (b - a) * t; }
static inline float cat__clampf(float v, float lo, float hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Lifecycle
 * ═══════════════════════════════════════════════════════════════════════════ */

int            cat_init(cat_config *cfg);
void           cat_quit(void);
/* Initialize the joystick subsystem and open input devices. Implied by
 * cat_init unless cat_config.defer_input_init is set; idempotent. Deferred
 * callers run it once the first frame is on screen — input polling also
 * triggers it lazily as a fallback. */
void           cat_init_input(void);
SDL_Renderer  *cat_get_renderer(void);
SDL_Window    *cat_get_window(void);
int            cat_get_screen_width(void);
int            cat_get_screen_height(void);
/* Show the window and raise it to the foreground (SDL_ShowWindow +
 * SDL_RaiseWindow). Pairs with cat_config.start_hidden for warm-standby use. */
void           cat_show_window(void);
void           cat_hide_window(void);
/* Raise the window and activate the application. Required on macOS when the
 * process was spawned from a background parent (e.g. a daemon). No-op on
 * other platforms. Call immediately after cat_init() + SDL_RaiseWindow(). */
void           cat_activate_window(void);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Scaling
 * ═══════════════════════════════════════════════════════════════════════════ */

float          cat_get_scale_factor(void);
int            cat_scale(int base);
int            cat_device_scale(int base);
int            cat_font_size_for_resolution(int base_size);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Theming
 * ═══════════════════════════════════════════════════════════════════════════ */

cat_theme           *cat_get_theme(void);
const cat_stylesheet *cat_get_stylesheet(void);
int            cat_reload_background(const char *bg_path);
cat_draw_color cat_hex_to_color(const char *hex);
void           cat_set_theme_color(const char *hex);
/* Override the tab-bar text colors on the active stylesheet (inactive +
   selected). Lets the host map them onto its own palette roles. */
void           cat_set_tab_text_colors(cat_color inactive, cat_color selected);
/* Derive the theme fields that aren't stored directly: selected-row text
   auto-contrasts against the selection pill, and the tab-bar text colors track
   the palette. Call after changing any of the color roles. NULL is a no-op. */
void           cat_finalize_theme_colors(cat_theme *t);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Fonts
 * ═══════════════════════════════════════════════════════════════════════════ */

TTF_Font      *cat_get_font(cat_font_tier tier);
/* A glyph-complete font (the bundled base font.ttf) for UI symbols the active
 * font family may not carry — e.g. the keyboard's ⇧ ← ↵ keys. Sized to the LARGE
 * tier and follows the font bump. Falls back to the LARGE tier if unavailable. */
TTF_Font      *cat_get_symbol_font(void);
int            cat_get_font_bump(void);
int            cat_set_font_bump(int bump);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Input
 * ═══════════════════════════════════════════════════════════════════════════ */

bool           cat_poll_input(cat_input_event *event);
void           cat_set_input_delay(uint32_t ms);
void           cat_set_input_repeat(uint32_t delay_ms, uint32_t rate_ms);
void           cat_set_shoulder_repeat(bool enabled);  /* opt-in L1/R1 hold-repeat */
void           cat_flip_face_buttons(bool flip);
const char    *cat_button_name(cat_button btn);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Combos
 * ═══════════════════════════════════════════════════════════════════════════ */

int            cat_register_chord(const char *id, cat_button *buttons, int count, uint32_t window_ms);
int            cat_register_sequence(const char *id, cat_button *buttons, int count, uint32_t timeout_ms, bool strict);
int            cat_register_chord_ex(const char *id, cat_button *buttons, int count,
                                    uint32_t window_ms,
                                    cat_combo_callback on_trigger,
                                    cat_combo_callback on_release,
                                    void *userdata);
int            cat_register_sequence_ex(const char *id, cat_button *buttons, int count,
                                      uint32_t timeout_ms, bool strict,
                                      cat_combo_callback on_trigger,
                                      void *userdata);
void           cat_unregister_combo(const char *id);
void           cat_clear_combos(void);
bool           cat_poll_combo(cat_combo_event *event);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Drawing Primitives
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Cardinal direction, e.g. which way a cat_draw_triangle affordance points. */
typedef enum {
    CAT_DIR_LEFT = 0,
    CAT_DIR_RIGHT,
    CAT_DIR_UP,
    CAT_DIR_DOWN,
} cat_dir;

/* Corner selection bitmask for cat_draw_rounded_rect_ex / pill_corner_mask. */
enum {
    CAT_CORNER_TL  = 1 << 0,
    CAT_CORNER_TR  = 1 << 1,
    CAT_CORNER_BL  = 1 << 2,
    CAT_CORNER_BR  = 1 << 3,
    CAT_CORNER_ALL = CAT_CORNER_TL | CAT_CORNER_TR | CAT_CORNER_BL | CAT_CORNER_BR,
};

void           cat_clear_screen(void);
void           cat_present(void);
void           cat_draw_background(void);
void           cat_draw_rounded_rect(int x, int y, int w, int h, int r, cat_draw_color c);
void           cat_draw_rounded_rect_ex(int x, int y, int w, int h, int r, unsigned corners, cat_draw_color c); /* round only the corners in the CAT_CORNER_* mask; others are square */
void           cat_draw_pill(int x, int y, int w, int h, cat_draw_color c);
void           cat_draw_rect(int x, int y, int w, int h, cat_draw_color c);
void           cat_draw_textured_quad(SDL_Texture *tex, const SDL_FPoint corners[4], const SDL_FPoint uv[4], const SDL_Color col[4]); /* one quad (two triangles) via SDL_RenderGeometry; corners TL,TR,BR,BL. tex NULL = flat fill, uv NULL = full 0..1, col NULL = opaque white. For perspective/tilted cards subdivide into vertical strips to avoid affine-mapping shear. */
void           cat_draw_circle(int cx, int cy, int r, cat_draw_color c);
void           cat_draw_star(int cx, int cy, int outer_r, cat_draw_color c); /* filled 5-point star centered at (cx,cy) */
void           cat_draw_triangle(int x, int y, int w, int h, cat_dir dir, cat_draw_color c); /* solid triangle filling the box, pointing `dir` (cycler/affordance arrows) */
int            cat_draw_text(TTF_Font *font, const char *text, int x, int y, cat_draw_color color);          /* returns rendered width in pixels */
int            cat_draw_text_clipped(TTF_Font *font, const char *text, int x, int y, cat_draw_color color, int max_w); /* returns rendered width */
int            cat_draw_text_ellipsized(TTF_Font *font, const char *text, int x, int y, cat_draw_color color, int max_w); /* truncate with "..." if too wide */
int            cat_draw_text_wrapped(TTF_Font *font, const char *text, int x, int y, int max_w, cat_draw_color color, cat_text_align align); /* returns rendered height */
int            cat_measure_text(TTF_Font *font, const char *text);
int            cat_measure_text_ellipsized(TTF_Font *font, const char *text, int max_w); /* measure width text would occupy when ellipsized to fit max_w */
void           cat_draw_image(SDL_Texture *tex, int x, int y, int w, int h);

/* Device-type glyphs carried in the status-asset atlas (e.g. for the Bluetooth
   device list). Monochrome, recolored to any theme color when drawn. */
typedef enum {
    CAT_DEVICE_ICON_BLUETOOTH = 0,   /* generic / unknown device */
    CAT_DEVICE_ICON_HEADSET,         /* headphones */
    CAT_DEVICE_ICON_CONTROLLER,      /* gamepad */
} cat_device_icon;
int            cat_device_icon_px(void); /* native draw size (square) in px; 0 if the atlas is unavailable */
int            cat_draw_device_icon(cat_device_icon icon, int x, int y, cat_draw_color color); /* draw at (x,y) top-left; returns drawn size in px (0 if unavailable) */
/* Draw an image clipped to a rounded rectangle, rounding only the corners in the
   CAT_CORNER_* mask (so it can match a list pill's shape). Uses a scratch render
   target to multiply the image's alpha by the rounded mask; falls back to a plain
   cat_draw_image when r<=0, corners==0, or no render target is available. */
void           cat_draw_image_rounded_ex(SDL_Texture *tex, int x, int y, int w, int h, int r, unsigned corners);
/* Round off whatever is already in the current render target: multiplies the
   region's alpha by a rounded-rect mask, so the corners outside the shape become
   transparent and everything inside is untouched. For a caller that composes a
   tile itself and wants the shape without a second scratch target. Returns false
   (and changes nothing) where the renderer rejects the custom blend mode. */
bool           cat_mask_rounded_rect(int x, int y, int w, int h, int r, unsigned corners);
SDL_Texture   *cat_load_image(const char *path);
/* Load `path` downscaled so its largest dimension is <= max_dim, backed by an
   on-disk thumbnail at `thumb_path`. Large source images (e.g. ~1MB box-art PNGs)
   are expensive to decode; the first load decodes the full image once, writes the
   downscaled thumbnail, and returns its texture. Later loads decode the small
   thumbnail (much faster) when it exists and is newer than the source. Images
   already <= max_dim are loaded directly with no thumbnail written. Returns a
   texture owned by the caller (destroy it or hand to cat_cache_put), or NULL. */
SDL_Texture   *cat_load_image_thumbnail(const char *path, const char *thumb_path,
                                        int max_dim, int *out_w, int *out_h);
/* True if `thumb_path` exists and is at least as new as `path`. */
bool           cat_thumbnail_is_cached(const char *path, const char *thumb_path);
/* Renderer-free half of cat_load_image_thumbnail: decode + downscale (+ persist the
   thumbnail) into a new SDL_Surface (caller frees). Touches no renderer state, so it
   is safe to call off the main thread; convert to a texture on the render thread. */
SDL_Surface   *cat_decode_thumbnail_surface(const char *path, const char *thumb_path,
                                            int max_dim);
/* Create a renderer texture from a surface (e.g. one decoded on a worker thread).
   Must be called on the render thread. Caller still owns `surf`. */
SDL_Texture   *cat_texture_from_surface(SDL_Surface *surf);
void           cat_draw_scrollbar(int x, int y, int h, int visible, int total, int offset);
/* Draw a horizontal tab bar spanning the full screen width. Uses the theme's
 * tab_color (inactive) and tab_selected_color (active) from the stylesheet,
 * and the theme accent colour as the bar background. */
void           cat_draw_tab_bar(const char *const *labels, int count, int active_index);
int            cat_get_tab_bar_height(void);
/* Reserve right-side width in the tab bar (e.g. for an inline status bar drawn
   over it). When the tabs don't fit the remaining width, cat_draw_tab_bar()
   windows them around the active tab and shows ‹ / › affordances. */
void           cat_set_tab_bar_reserved_right(int px);
/* Blit a texture into an arbitrary screen-space quadrilateral (4 corners in
 * clockwise order: top-left, top-right, bottom-right, bottom-left). Used for
 * the skewed parallelogram carousel panels in horizontal launcher mode.
 * Requires SDL 2.0.18+ for SDL_RenderGeometry. */
void           cat_draw_textured_parallelogram(SDL_Texture *tex,
                                               const SDL_FPoint quad[4],
                                               uint8_t alpha);
void           cat_draw_progress_bar(int x, int y, int w, int h, float progress, cat_draw_color fg, cat_draw_color bg);
SDL_Rect       cat_get_content_rect(bool has_title, bool has_footer, bool has_status_bar);

/* ─── Box model ─────────────────────────────────────────────────────────────
 * A minimal, margin-free layout box: a rectangle plus per-side INTERNAL padding.
 * Boxes tile their parent with no gaps; all visible spacing comes from a box's
 * own padding (never an outside margin). Build a layout by carving fixed-height
 * strips off a box and splitting the remainder into columns. Pure geometry — no
 * drawing, no global state. All sizes are final pixels (callers pre-scale with
 * CAT_S/CAT_DS). See plans/BOX_MODEL.md. */
typedef struct {
    int x, y, w, h;                   /* the box in pixels                     */
    int pad_t, pad_r, pad_b, pad_l;   /* internal padding (inset for contents) */
} cat_box;

/* The drawable area inside the padding (clamped to >= 0). */
SDL_Rect cat_box_content(const cat_box *b);
/* Carve `height` px off the top of b's content area: returns that strip as a box
 * (pad 0), and consumes it from b (b's content top moves down). height <= 0 is a
 * no-op that returns an empty strip — so an absent sub-header / hidden hint bar
 * costs nothing. cat_box_carve_bottom is the same from the bottom edge. */
cat_box  cat_box_carve_top(cat_box *b, int height);
cat_box  cat_box_carve_bottom(cat_box *b, int height);
/* Split b's content into a left column of width `left_w` and a right column that
 * fills the rest, with `gutter` px between them realized as facing-edge padding
 * (left gets pad_r += gutter/2, right gets pad_l += gutter/2) — no sibling
 * margin. Either out pointer may be NULL. */
void     cat_box_split_cols(const cat_box *b, int left_w, int gutter,
                            cat_box *left, cat_box *right);
/* Fit list rows into a box's content area. base_item_h is the natural row height.
 * If *visible_rows > 0 on entry it is taken as the row count (the caller's cached
 * scroll-state value), clamped to what fits at base_item_h so a stale count from a
 * taller box can never compress rows; otherwise the count that fits is computed
 * from the box. The actual count is always written back. When the list overflows
 * (item_count >= rows) the row height is stretched so the rows fill the box
 * exactly — no gap below the last row — and the returned rect is snapped to that
 * whole-rows height; a short list keeps base_item_h. out_item_h receives the
 * (possibly stretched) row height. Returns the row region. */
SDL_Rect cat_box_fit_rows(const cat_box *b, int base_item_h, int item_count,
                          int *visible_rows, int *out_item_h);
void           cat_draw_screen_title(const char *title, cat_status_bar_opts *status_bar);
void           cat_draw_screen_title_centered(const char *title, cat_status_bar_opts *status_bar);
int            cat_measure_wrapped_text_height(TTF_Font *font, const char *text, int max_w);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Text Scrolling
 * ═══════════════════════════════════════════════════════════════════════════ */

void           cat_text_scroll_init(cat_text_scroll *s);
void           cat_text_scroll_update(cat_text_scroll *s, int text_w, int visible_w, uint32_t dt_ms);
void           cat_text_scroll_reset(cat_text_scroll *s);

/* Marquee scroll mode (set cat_marquee.mode before the first draw; 0 = loop). */
typedef enum {
    CAT_MARQUEE_LOOP = 0,    /* scroll left continuously and wrap around       */
    CAT_MARQUEE_PINGPONG     /* scroll to the end, pause, scroll back, repeat  */
} cat_marquee_mode;

/* Marquee: draws text at (x,y) clipped to visible_w. If it fits, draws normally
   and returns false. If it overflows, after a brief initial pause it scrolls —
   either looping (a second copy follows a gap for a seamless wrap) or bouncing
   back and forth, per `mode`. Caller persists `m` across frames, passes elapsed
   dt_ms, and resets m->elapsed_ms to 0 when the text changes. Returns true while
   scrolling so the caller can request another frame. The clip is intersected
   with any clip already in effect and restored on exit, so it composes inside a
   scroll view. */
typedef struct {
    uint32_t         elapsed_ms;
    cat_marquee_mode mode;       /* default 0 = loop */
} cat_marquee;
bool           cat_draw_text_marquee(TTF_Font *font, const char *text, int x, int y,
                                     cat_draw_color color, int visible_w,
                                     cat_marquee *m, uint32_t dt_ms);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Texture Cache
 * ═══════════════════════════════════════════════════════════════════════════ */

SDL_Texture   *cat_cache_get(const char *key, int *w, int *h);
void           cat_cache_put(const char *key, SDL_Texture *tex, int w, int h);
void           cat_cache_clear(void);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Footer & Status Bar
 * ═══════════════════════════════════════════════════════════════════════════ */

void           cat_draw_footer(cat_footer_item *items, int count);
/* Set a one-shot alpha (1..255) for the next cat_draw_footer's pill backgrounds
   (text stays opaque). 0 restores the opaque default. Auto-resets after the draw. */
void           cat_set_footer_bg_opacity(int alpha);
int            cat_get_footer_height(void);
void           cat_set_footer_overflow_opts(const cat_footer_overflow_opts *opts);
void           cat_get_footer_overflow_opts(cat_footer_overflow_opts *out);
void           cat_show_footer_overflow(void);
void           cat_draw_status_bar(cat_status_bar_opts *opts);
/* Draw the status-bar Bluetooth glyph on its own, scaled to (w,h) at (x,y) and
   colored `tint`. For callers that want the icon outside the status bar. */
void           cat_draw_bluetooth_icon(int x, int y, int w, int h, cat_draw_color tint);
int            cat_get_status_bar_height(void);
int            cat_get_status_bar_width(cat_status_bar_opts *opts);
/* Populate *out from the CAT_STATUS_* appearance-snapshot env vars (exported by
   the launcher, or defaulted by env.sh). Returns true if a status bar should be
   drawn — i.e. ANY of wifi, battery icon, battery level (number), or clock is
   enabled. Leaves wifi_supplied=false so the bar self-reads wifi/battery/clock
   live; volume is never shown (no live source in an app). When a var is unset,
   defaults match the launcher (wifi + battery on, 24h clock). */
bool           cat_status_bar_from_env(cat_status_bar_opts *out);
/* CAT_SHOW_HINTS: whether footers/hints should be shown. Default true. */
bool           cat_hints_enabled_from_env(void);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Logging
 * ═══════════════════════════════════════════════════════════════════════════ */

void           cat_log(const char *fmt, ...);
void           cat_set_log_path(const char *path);
const char    *cat_resolve_log_path(const char *app_name);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Power Button
 * ═══════════════════════════════════════════════════════════════════════════ */

void           cat_set_power_handler(bool enabled);
void           cat_set_platform_services(const cat_platform_services *services);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — CPU & Fan
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Set CPU to a named speed preset. Sets the governor to "userspace" first,
 * then writes the platform-specific frequency. Returns CAT_OK on success.
 * No-op (returns CAT_OK) on desktop builds. */
int            cat_set_cpu_speed(cat_cpu_speed speed);

/* Read the current CPU frequency in MHz. Returns -1 on error or desktop. */
int            cat_get_cpu_speed_mhz(void);

/* Read the CPU temperature in °C. Returns -1 on error or desktop. */
int            cat_get_cpu_temp_celsius(void);

/* Set TG5050 fan mode. Manual stops any active auto daemon; auto modes mirror
 * Quiet/normal/performance fancontrol helper.
 * No-op (returns CAT_OK) on platforms without fan hardware. */
int            cat_set_fan_mode(cat_fan_mode mode);

/* Read the current TG5050 fan mode. Returns CAT_FAN_MODE_UNSUPPORTED on
 * platforms without fan hardware or if the current mode cannot be determined. */
cat_fan_mode    cat_get_fan_mode(void);

/* Set fan speed as a 0–100 percentage. Internally maps to 0–31 raw levels.
 * On TG5050 this also stops any active auto fan daemon before applying a
 * fixed speed. Pass -1 to leave the current speed unchanged.
 * Only has effect on TG5050; no-op (returns CAT_OK) on all other platforms. */
int            cat_set_fan_speed(int percent);

/* Read current fan speed as a 0–100 percentage.
 * Returns 0 on non-TG5050 platforms, -1 if the value cannot be read. */
int            cat_get_fan_speed(void);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Screen Fade
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Begin a fade-in: overlay starts fully black and becomes transparent */
static inline void cat_fade_begin_in(cat_fade *f, int duration_ms) {
    if (!f) return;
    f->start_ms    = SDL_GetTicks();
    f->duration_ms = duration_ms > 0 ? duration_ms : 1;
    f->fade_in     = true;
    f->active      = true;
}

/* Begin a fade-out: overlay starts transparent and becomes fully black */
static inline void cat_fade_begin_out(cat_fade *f, int duration_ms) {
    if (!f) return;
    f->start_ms    = SDL_GetTicks();
    f->duration_ms = duration_ms > 0 ? duration_ms : 1;
    f->fade_in     = false;
    f->active      = true;
}

void cat_request_frame(void);  /* forward declaration for cat_fade_draw */

/* Draw the fade overlay. Call AFTER drawing your scene, BEFORE cat_present().
 * Returns true while the fade is still active, false when complete. */
static inline bool cat_fade_draw(cat_fade *f) {
    if (!f || !f->active) return false;
    uint32_t elapsed = SDL_GetTicks() - f->start_ms;
    float t = cat__clampf((float)elapsed / (float)f->duration_ms, 0.0f, 1.0f);
    float alpha_f = f->fade_in ? cat__lerpf(255.0f, 0.0f, t)
                               : cat__lerpf(0.0f, 255.0f, t);
    SDL_Renderer *rend = cat_get_renderer();
    SDL_SetRenderDrawBlendMode(rend, SDL_BLENDMODE_BLEND);
    SDL_SetRenderDrawColor(rend, 0, 0, 0, (Uint8)(int)alpha_f);
    SDL_Rect full = { 0, 0, cat_get_screen_width(), cat_get_screen_height() };
    SDL_RenderFillRect(rend, &full);
    if (t >= 1.0f) { f->active = false; return false; }
    cat_request_frame();  /* keep rendering at 60fps while fade is active */
    return true;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Error Handling
 * ═══════════════════════════════════════════════════════════════════════════ */

const char    *cat_get_error(void);
bool           cat_is_cancelled(int result);

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API — Rendering
 * ═══════════════════════════════════════════════════════════════════════════ */

void  cat_request_frame(void);
void  cat_request_frame_in(uint32_t ms);
/* POSIX: wake cat_present() when fd is readable (e.g. a signal self-pipe).
   The caller owns and drains fd; pass -1 to detach before closing it. */
void  cat_set_idle_wake_fd(int fd);

/* ═══════════════════════════════════════════════════════════════════════════
 * IMPLEMENTATION
 * ═══════════════════════════════════════════════════════════════════════════ */
#ifdef CAT_IMPLEMENTATION

#if defined(__APPLE__)
#include <objc/objc.h>
#include <objc/message.h>
#endif

#include "cjson/cJSON.h"

/* Global state singleton */
static cat__state cat__g = {0};

/* Last error message buffer */
static char cat__error_buf[512] = {0};

/* ─── Internal Helpers ───────────────────────────────────────────────────── */

static void cat__set_error(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(cat__error_buf, sizeof(cat__error_buf), fmt, args);
    va_end(args);
}

static int cat__clamp(int val, int lo, int hi) {
    if (val < lo) return lo;
    if (val > hi) return hi;
    return val;
}

#if !CAT_PLATFORM_IS_DEVICE
static bool cat__env_parse_int(const char *name, int *out) {
    if (!name || !out) return false;

    const char *value = getenv(name);
    if (!value || !value[0]) return false;

    errno = 0;
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (errno != 0 || end == value || (end && *end != '\0')) return false;
    if (parsed < INT_MIN || parsed > INT_MAX) return false;

    *out = (int)parsed;
    return true;
}
#endif

static const char *cat__env_nonempty(const char *name) {
    const char *value = getenv(name);
    return (value && value[0]) ? value : NULL;
}

static bool cat__env_parse_int_range(const char *name, int min_v, int max_v, int *out) {
    const char *value = cat__env_nonempty(name);
    if (!value || !out) return false;

    errno = 0;
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (errno != 0 || end == value || (end && *end != '\0')) return false;
    if (parsed < min_v || parsed > max_v) return false;
    *out = (int)parsed;
    return true;
}

static bool cat__env_parse_float_range(const char *name, float min_v, float max_v, float *out) {
    const char *value = cat__env_nonempty(name);
    if (!value || !out) return false;

    errno = 0;
    char *end = NULL;
    float parsed = strtof(value, &end);
    if (errno != 0 || end == value || (end && *end != '\0')) return false;
    if (parsed < min_v || parsed > max_v) return false;
    *out = parsed;
    return true;
}

/* Boolean snapshot flag: "0" is false, any other non-empty value is true,
   unset falls back to dflt. Mirrors the launcher's `strcmp(val,"0")!=0` test. */
static bool cat__env_flag(const char *name, bool dflt) {
    const char *value = cat__env_nonempty(name);
    if (!value) return dflt;
    return strcmp(value, "0") != 0;
}

static const char *cat__default_sdcard_path(void) {
#if defined(PLATFORM_MLP1)
    return CAT__MLP1_DEFAULT_SDCARD_PATH;
#elif CAT_PLATFORM_IS_DEVICE
    return CAT__RETRO_DEFAULT_SDCARD_PATH;
#else
    return NULL;
#endif
}

static const char *cat__sdcard_path(void) {
    const char *sdcard = cat__env_nonempty("SDCARD_PATH");
    return sdcard ? sdcard : cat__default_sdcard_path();
}

static const char *cat__system_path(char *buf, size_t buf_size) CAT__MAYBE_UNUSED;
static const char *cat__system_path(char *buf, size_t buf_size) {
    const char *path = cat__env_nonempty("SYSTEM_PATH");
    if (path) return path;

    const char *sdcard = cat__sdcard_path();
    if (!sdcard || !buf || buf_size == 0) return NULL;
#if defined(PLATFORM_MLP1)
    snprintf(buf, buf_size, "%s/UMRK/%s", sdcard, CAT_PLATFORM_NAME);
#elif CAT_PLATFORM_IS_DEVICE
    snprintf(buf, buf_size, "%s/.system/%s", sdcard, CAT_PLATFORM_NAME);
#else
    snprintf(buf, buf_size, "%s/UMRK/%s", sdcard, CAT_PLATFORM_NAME);
#endif
    return buf;
}

static const char *cat__launcher_path(char *buf, size_t buf_size) CAT__MAYBE_UNUSED;
static const char *cat__launcher_path(char *buf, size_t buf_size) {
    const char *path = cat__env_nonempty("UMRK_LAUNCHER_PATH");
    if (path) return path;

    const char *sdcard = cat__sdcard_path();
    if (!sdcard || !buf || buf_size == 0) return NULL;
#if defined(PLATFORM_MLP1)
    snprintf(buf, buf_size, "%s/umrk-launcher", sdcard);
#else
    const char *system_path = cat__system_path(buf, buf_size);
    return system_path;
#endif
    return buf;
}

static const char *cat__userdata_path(char *buf, size_t buf_size) CAT__MAYBE_UNUSED;
static const char *cat__userdata_path(char *buf, size_t buf_size) {
    const char *path = cat__env_nonempty("USERDATA_PATH");
    if (path) return path;

    const char *sdcard = cat__sdcard_path();
    if (!sdcard || !buf || buf_size == 0) return NULL;
#if defined(PLATFORM_MLP1)
    snprintf(buf, buf_size, "%s/.userdata/%s", sdcard, CAT_PLATFORM_NAME);
#elif CAT_PLATFORM_IS_DEVICE
    snprintf(buf, buf_size, "%s/.allium/state", sdcard);
#else
    snprintf(buf, buf_size, ".catastrophe/state");
#endif
    return buf;
}

static const char *cat__status_assets_dir(char *buf, size_t buf_size) {
    const char *dir = cat__env_nonempty("CAT_STATUS_ASSETS_DIR");
    if (dir) return dir;

#if CAT_PLATFORM_IS_DEVICE
    #if defined(PLATFORM_MLP1)
    char launcher_buf[PATH_MAX];
    const char *launcher = cat__launcher_path(launcher_buf, sizeof(launcher_buf));
    if (!launcher) return NULL;
    if (snprintf(buf, buf_size, "%s/res/assets", launcher) >= (int)buf_size) return NULL;
    #else
    char system_buf[PATH_MAX];
    const char *system_path = cat__system_path(system_buf, sizeof(system_buf));
    if (!system_path) return NULL;
    if (snprintf(buf, buf_size, "%s/res", system_path) >= (int)buf_size) return NULL;
    #endif
    return buf;
#else
    (void)buf;
    (void)buf_size;
    return NULL;
#endif
}

static int cat__max(int a, int b) { return a > b ? a : b; }

/* Base font sizes — multiplied by device_scale */
static const int cat__font_base_sizes[CAT_FONT_TIER_COUNT] = {
    24, /* EXTRA_LARGE — title/header                       */
    16, /* LARGE       — menu/list items                    */
    14, /* MEDIUM      — single-char button label           */
    12, /* SMALL       — hint text, status clock            */
    10, /* TINY        — multi-char button label            */
     7, /* MICRO       — overlay text                       */
};

/* Font search paths by platform — fallback list used when the active stylesheet provides no font */
static const char *cat__font_search_paths[] = {
    "./font.ttf",
    "./res/font.ttf",
    "../res/font.ttf",
    "./res/fonts/Inter/Inter.ttf",
    "../res/fonts/Inter/Inter.ttf",
#if defined(PLATFORM_MLP1)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
#elif defined(PLATFORM_MAC)
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
#elif defined(PLATFORM_LINUX)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
#elif defined(PLATFORM_WINDOWS)
    "C:\\Windows\\Fonts\\segoeui.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
#endif
    NULL,
};

/* Joystick button mapping — TrimUI raw values (firmware swaps A/B, X/Y) */
#define CAT__JOY_BTN_A       1
#define CAT__JOY_BTN_B       0
#define CAT__JOY_BTN_X       3
#define CAT__JOY_BTN_Y       2
#define CAT__JOY_BTN_L1      4
#define CAT__JOY_BTN_R1      5
#define CAT__JOY_BTN_L2      10
#define CAT__JOY_BTN_R2      11
#define CAT__JOY_BTN_SELECT  6
#define CAT__JOY_BTN_START   7
#define CAT__JOY_BTN_MENU    8

/* TrimUI analog trigger axes (L2/R2 are axes, not buttons, on TG5040/TG5050) */
#define CAT__JOY_AXIS_L2     2   /* ABS_Z   */
#define CAT__JOY_AXIS_R2     5   /* ABS_RZ  */

/* MLP1 Loong Gamepad raw SDL joystick button mapping.
 * Captured on stockOS via evtest /dev/input/event4 on 2026-05-23. SDL raw
 * joystick button indices follow the exposed Linux BTN_* order. */
#define CAT__MLP1_BTN_A       1   /* physical A: BTN_EAST  */
#define CAT__MLP1_BTN_B       0   /* physical B: BTN_SOUTH */
#define CAT__MLP1_BTN_X       2   /* physical X: BTN_NORTH */
#define CAT__MLP1_BTN_Y       3   /* physical Y: BTN_WEST  */
#define CAT__MLP1_BTN_L1      4   /* BTN_TL    */
#define CAT__MLP1_BTN_R1      5   /* BTN_TR    */
#define CAT__MLP1_BTN_L2      6   /* BTN_TL2   */
#define CAT__MLP1_BTN_R2      7   /* BTN_TR2   */
#define CAT__MLP1_BTN_SELECT  8   /* BTN_SELECT */
#define CAT__MLP1_BTN_START   9   /* BTN_START  */
#define CAT__MLP1_BTN_MENU    10  /* BTN_MODE   */
#define CAT__MLP1_BTN_STICK   11  /* BTN_THUMBL */

/* my355 (Miyoo Flip) keyboard scancode mapping.
 * On the Flip, ALL buttons arrive as SDL keyboard scancodes, not joystick. */
#define CAT__MY355_CODE_A       44   /* SDL_SCANCODE_SPACE */
#define CAT__MY355_CODE_B       224  /* SDL_SCANCODE_LCTRL */
#define CAT__MY355_CODE_X       225  /* SDL_SCANCODE_LSHIFT */
#define CAT__MY355_CODE_Y       226  /* SDL_SCANCODE_LALT */
#define CAT__MY355_CODE_UP      82   /* SDL_SCANCODE_UP */
#define CAT__MY355_CODE_DOWN    81   /* SDL_SCANCODE_DOWN */
#define CAT__MY355_CODE_LEFT    80   /* SDL_SCANCODE_LEFT */
#define CAT__MY355_CODE_RIGHT   79   /* SDL_SCANCODE_RIGHT */
#define CAT__MY355_CODE_START   40   /* SDL_SCANCODE_RETURN */
#define CAT__MY355_CODE_SELECT  228  /* SDL_SCANCODE_RCTRL */
#define CAT__MY355_CODE_L1      43   /* SDL_SCANCODE_TAB */
#define CAT__MY355_CODE_R1      42   /* SDL_SCANCODE_BACKSLASH */
#define CAT__MY355_CODE_L2      75   /* SDL_SCANCODE_PAGEUP */
#define CAT__MY355_CODE_R2      78   /* SDL_SCANCODE_PAGEDOWN */
#define CAT__MY355_CODE_MENU    41   /* SDL_SCANCODE_ESCAPE */
#define CAT__MY355_CODE_POWER   102  /* SDL_SCANCODE_POWER */

/* Virtual button names */
static const char *cat__button_names[CAT_BTN_COUNT] = {
    "NONE", "UP", "DOWN", "LEFT", "RIGHT",
    "A", "B", "X", "Y",
    "L1", "L2", "R1", "R2",
    "START", "SELECT", "MENU", "POWER", "QUIT", "STICK"
};

/* ─── Logging ────────────────────────────────────────────────────────────── */

static bool cat__same_output_file(FILE *a, FILE *b) {
    if (!a || !b) return false;
#ifdef _WIN32
    int a_fd = _fileno(a);
    int b_fd = _fileno(b);
    if (a_fd < 0 || b_fd < 0) return false;
    return a_fd == b_fd;
#else
    int a_fd = fileno(a);
    int b_fd = fileno(b);
    if (a_fd < 0 || b_fd < 0) return false;

    struct stat a_st, b_st;
    if (fstat(a_fd, &a_st) != 0) return false;
    if (fstat(b_fd, &b_st) != 0) return false;
    return a_st.st_dev == b_st.st_dev && a_st.st_ino == b_st.st_ino;
#endif
}

void cat_log(const char *fmt, ...) {
    char buf[CAT_MAX_LOG_LEN];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    /* Timestamp */
    time_t now = time(NULL);
    struct tm *t = localtime(&now);
    char ts[32];
    strftime(ts, sizeof(ts), "%H:%M:%S", t);

    fprintf(stderr, "[%s] %s\n", ts, buf);
    if (cat__g.log_file && !cat__same_output_file(cat__g.log_file, stderr)) {
        fprintf(cat__g.log_file, "[%s] %s\n", ts, buf);
        fflush(cat__g.log_file);
    }
}

void cat_set_log_path(const char *path) {
    if (cat__g.log_file && cat__g.log_file != stderr) {
        fclose(cat__g.log_file);
        cat__g.log_file = NULL;
    }
    if (path) {
        cat__g.log_file = fopen(path, "a");
        if (!cat__g.log_file) {
            fprintf(stderr, "Warning: could not open log file: %s\n", path);
        }
    }
}

const char *cat_resolve_log_path(const char *app_name) {
    static char path[1024];
    if (!app_name || !app_name[0]) return NULL;

    const char *logs = getenv("LOGS_PATH");
    if (logs && logs[0]) {
        snprintf(path, sizeof(path), "%s/%s.txt", logs, app_name);
        return path;
    }

    const char *shared = getenv("SHARED_USERDATA_PATH");
    if (shared && shared[0]) {
        snprintf(path, sizeof(path), "%s/logs/%s.txt", shared, app_name);
        return path;
    }

    const char *home = getenv("HOME");
    if (home && home[0]) {
        snprintf(path, sizeof(path), "%s/.userdata/logs/%s.txt", home, app_name);
        return path;
    }

    return NULL;
}

/* ─── Error Handling ─────────────────────────────────────────────────────── */

const char *cat_get_error(void) {
    return cat__error_buf;
}

bool cat_is_cancelled(int result) {
    return result == CAT_CANCELLED;
}

/* ─── Color Utilities ────────────────────────────────────────────────────── */

cat_draw_color cat_hex_to_color(const char *hex) {
    return cat_color_to_sdl(cat_color_from_hex(hex));
}

void cat_set_theme_color(const char *hex) {
    if (hex) {
        cat__g.theme.accent = cat_hex_to_color(hex);
    }
}

void cat_set_tab_text_colors(cat_color inactive, cat_color selected) {
    cat__g.stylesheet.ui.tab_color = inactive;
    cat__g.stylesheet.ui.tab_selected_color = selected;
}

/* WCAG relative luminance (0..1) of an sRGB color. */
static double cat__rel_lum(cat_draw_color c) {
    double ch[3] = { c.r / 255.0, c.g / 255.0, c.b / 255.0 };
    for (int i = 0; i < 3; i++)
        ch[i] = (ch[i] <= 0.03928) ? (ch[i] / 12.92)
                                   : pow((ch[i] + 0.055) / 1.055, 2.4);
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

/* WCAG contrast ratio (1..21) between two colors. */
static double cat__contrast_ratio(cat_draw_color a, cat_draw_color b) {
    double la = cat__rel_lum(a), lb = cat__rel_lum(b);
    if (la < lb) { double tmp = la; la = lb; lb = tmp; }
    return (la + 0.05) / (lb + 0.05);
}

/* Blend `fg` toward the black/white extreme that yields the most contrast with
   `bg`, stopping as soon as `target` is met (clamped at the extreme otherwise).
   Toward black preserves hue+saturation (just darker — best for light schemes);
   toward white desaturates (for dark schemes). Alpha is preserved. */
static cat_draw_color cat__contrast_clamp(cat_draw_color fg, cat_draw_color bg, double target) {
    if (cat__contrast_ratio(fg, bg) >= target) return fg;
    /* contrast-to-black == contrast-to-white where bg luminance ~= 0.179. */
    double anchor = (cat__rel_lum(bg) > 0.179) ? 0.0 : 255.0;
    cat_draw_color out = fg;
    for (int step = 1; step <= 20; step++) {
        double k = step / 20.0;
        out.r = (uint8_t)(fg.r + (anchor - fg.r) * k + 0.5);
        out.g = (uint8_t)(fg.g + (anchor - fg.g) * k + 0.5);
        out.b = (uint8_t)(fg.b + (anchor - fg.b) * k + 0.5);
        if (cat__contrast_ratio(out, bg) >= target) break;
    }
    return out;
}

void cat_finalize_theme_colors(cat_theme *t) {
    if (!t) return;

    int hl_lum = (t->highlight.r * 299 + t->highlight.g * 587 + t->highlight.b * 114) / 1000;
    int text_lum = (t->text.r * 299 + t->text.g * 587 + t->text.b * 114) / 1000;
    int bg_lum   = (t->background.r * 299 + t->background.g * 587 + t->background.b * 114) / 1000;
    /* Selected-row text auto-contrast: pick whichever of text/background reads on
       the highlight pill -- the darker of the two on a bright pill, the lighter on
       a dark one. Handles light schemes (text dark / bg light) as well as dark
       ones; for dark schemes the result is unchanged. */
    if (hl_lum > 140)
        t->highlighted_text = (text_lum <= bg_lum) ? t->text : t->background;
    else
        t->highlighted_text = (text_lum >= bg_lum) ? t->text : t->background;

    /* Emphasis = the theme identity (highlight) hue, luminance-clamped so section
       headings and status text read against the background on every scheme. The
       raw highlight is bright-on-dark (great on dark schemes) but light-on-light
       (near-invisible on light ones); clamping fixes the light schemes while
       leaving the dark ones untouched. */
    t->emphasis = cat__contrast_clamp(t->highlight, t->background, 4.5);

    cat_set_tab_text_colors(
        cat_color_rgba(t->hint.r, t->hint.g, t->hint.b, 0xFF),
        cat_color_rgba(t->text.r, t->text.g, t->text.b, 0xFF));
}

static void cat__apply_env_appearance_overrides(void) {
    cat_theme *t = &cat__g.theme;
    const char *hex;
    float radius = 0.0f;
    int corner_mask = 0;
    bool color_changed = false;

    hex = cat__env_nonempty("CAT_COLOR_ACCENT");
    if (hex) { t->accent = cat_hex_to_color(hex); color_changed = true; }
    hex = cat__env_nonempty("CAT_COLOR_BACKGROUND");
    if (hex) { t->background = cat_hex_to_color(hex); color_changed = true; }
    hex = cat__env_nonempty("CAT_COLOR_TEXT");
    if (hex) { t->text = cat_hex_to_color(hex); color_changed = true; }
    hex = cat__env_nonempty("CAT_COLOR_HINT");
    if (hex) { t->hint = cat_hex_to_color(hex); color_changed = true; }
    hex = cat__env_nonempty("CAT_COLOR_HIGHLIGHT");
    if (hex) { t->highlight = cat_hex_to_color(hex); color_changed = true; }
    hex = cat__env_nonempty("CAT_COLOR_BUTTON_LABEL");
    if (hex) { t->button_label = cat_hex_to_color(hex); color_changed = true; }
    hex = cat__env_nonempty("CAT_COLOR_BUTTON_GLYPH_BG");
    if (hex) { t->button_glyph_bg = cat_hex_to_color(hex); color_changed = true; }

    if (cat__env_parse_float_range("CAT_PILL_RADIUS_RATIO", 0.0f, 1.0f, &radius))
        t->pill_radius_ratio = radius;
    if (cat__env_parse_int_range("CAT_PILL_CORNER_MASK", 0, CAT_CORNER_ALL, &corner_mask))
        t->pill_corner_mask = corner_mask;

    if (color_changed)
        cat_finalize_theme_colors(t);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Stylesheet (Allium Theme) Implementation
 * ═══════════════════════════════════════════════════════════════════════════ */

/* Forward declarations from later in the implementation */
static int cat__load_fonts(const char *user_font_path);
static void cat__input_backend_init(void);
static void cat__text_cache_clear(void);

static void cat__stylesheet_font_init_default(cat_stylesheet_font *f) {
    f->path[0] = '\0';
    f->size = 0;
}

static void cat__stylesheet_ui_init_default(cat_stylesheet_ui *ui) {
    /* Apostrophe-parity defaults: white selection pill on black background, black text on
       the pill. These match the historical runtime theme at first launch with no on-disk stylesheet. */
    ui->margin_x = 12;
    ui->margin_y = 8;
    ui->list_margin = 4;
    ui->padding_x = 12;
    ui->padding_y = 4;
    cat__stylesheet_font_init_default(&ui->ui_font);
    ui->text_color               = cat_color_rgba(0xFF, 0xFF, 0xFF, 0xFF);
    ui->text_stroke_color        = cat_color_rgba(0x00, 0x00, 0x00, 0x00);
    ui->background_color         = cat_color_rgba(0x00, 0x00, 0x00, 0xFF);
    ui->highlight_color          = cat_color_rgba(0xFF, 0xFF, 0xFF, 0xFF);
    ui->highlight_text_color     = cat_color_rgba(0x00, 0x00, 0x00, 0xFF);
    ui->highlight_text_stroke_color = cat_color_rgba(0x00, 0x00, 0x00, 0x00);
    ui->disabled_color           = cat_color_rgba(0xFF, 0xFF, 0xFF, 0xFF);
    ui->hint_color               = 0; /* unset → falls back to disabled_color at theme bind */
    ui->tab_font_size            = 1.0f;
    ui->tab_color                = cat_color_rgba(0xFF, 0xFF, 0xFF, 0x70);
    ui->tab_stroke_color         = cat_color_rgba(0x00, 0x00, 0x00, 0x00);
    ui->tab_selected_color       = cat_color_rgba(0xFF, 0xFF, 0xFF, 0xFF);
    ui->tab_selected_stroke_color = cat_color_rgba(0x00, 0x00, 0x00, 0x00);
    ui->stroke_width             = 0;
    ui->pill_radius_ratio        = 1.0f;
}

static void cat__stylesheet_status_bar_init_default(cat_stylesheet_status_bar *sb) {
    sb->show_battery_level = false;
    sb->show_clock         = true;
    sb->show_wifi          = false;
    sb->font_size          = 1.0f;
    sb->text_color         = cat_color_rgba(0xFF, 0xFF, 0xFF, 0xFF);
    sb->text_stroke_color  = cat_color_rgba(0x00, 0x00, 0x00, 0x00);
}

static void cat__stylesheet_button_hints_init_default(cat_stylesheet_button_hints *bh) {
    /* Apostrophe-parity: a single pink accent (#9B2257) for every face-button cap, dark
       (#1E2329) text on the caps. cat_stylesheet_apply sources theme.accent from
       button_a_color, so this also drives every theme.accent consumer (footer pills,
       section titles, progress bars, etc.). */
    bh->button_hint_font_size = 0.9f;
    bh->button_size           = 1.0f;
    bh->button_text_font_size = 0.75f;
    bh->button_a_color        = cat_color_rgba(0x9B, 0x22, 0x57, 0xFF);
    bh->button_b_color        = cat_color_rgba(0x9B, 0x22, 0x57, 0xFF);
    bh->button_x_color        = cat_color_rgba(0x9B, 0x22, 0x57, 0xFF);
    bh->button_y_color        = cat_color_rgba(0x9B, 0x22, 0x57, 0xFF);
    bh->button_bg_color       = cat_color_rgba(0x58, 0x5B, 0x70, 0xFF);
    bh->button_text_color     = cat_color_rgba(0x1E, 0x23, 0x29, 0xFF);
    bh->text_color            = cat_color_rgba(0xFF, 0xFF, 0xFF, 0xFF);
    bh->glyph_bg_color        = 0; /* unset → falls back to ui.highlight_color at theme bind */
}

static void cat__stylesheet_recents_init_default(cat_stylesheet_recents *r) {
    r->use_recents_carousel = false;
}

static void cat__stylesheet_games_init_default(cat_stylesheet_games *g) {
    g->boxart_width = 250;
}

static void cat__stylesheet_menu_init_default(cat_stylesheet_menu *m) {
    m->background_color = cat_color_rgba(0x00, 0x00, 0x00, 0xFF);
    cat__stylesheet_font_init_default(&m->guide_font);
}

static void cat__stylesheet_launcher_init_default(cat_stylesheet_launcher *l) {
    l->layout        = CAT_LAUNCHER_TABBED;
    l->list_split    = 0.45f;
    l->carousel_skew = 30;
    l->coverflow_icon_size  = 256;
    l->coverflow_side_size  = 160;
    l->coverflow_spacing    = 280;
    l->coverflow_side_alpha = 140;
    l->coverflow_anim_ms    = 180;
    strncpy(l->coverflow_icon_dir, "system_icons", sizeof(l->coverflow_icon_dir) - 1);
    l->coverflow_icon_dir[sizeof(l->coverflow_icon_dir) - 1] = '\0';
    l->grid_cols            = 4;
    l->grid_rows            = 3;
    l->grid_margin          = 45;
    l->grid_gutter          = 48;
    l->grid_radius_pct      = 18;
    l->grid_border_w        = 2;
    l->grid_focus_border_w  = 3;
    l->grid_focus_scale_pct = 110;
    l->grid_status_scale_pct = 70;
    l->grid_border_color       = cat_color_rgba(0xFF, 0xFF, 0xFF, 0x3C);
    /* Alpha 0 is a sentinel: "use the theme accent", resolved at draw time, so a
       theme that sets no focus colour tracks the user's colour scheme. */
    l->grid_focus_border_color = cat_color_rgba(0x00, 0x00, 0x00, 0x00);
    l->grid_anim_ms         = 180;
    strncpy(l->grid_icon_dir, "system_icons", sizeof(l->grid_icon_dir) - 1);
    l->grid_icon_dir[sizeof(l->grid_icon_dir) - 1] = '\0';
    strncpy(l->grid_label_dir, "grid_labels", sizeof(l->grid_label_dir) - 1);
    l->grid_label_dir[sizeof(l->grid_label_dir) - 1] = '\0';
}

void cat_stylesheet_init_default(cat_stylesheet *s) {
    if (!s) return;
    s->wallpaper[0] = '\0';
    cat__stylesheet_ui_init_default(&s->ui);
    cat__stylesheet_status_bar_init_default(&s->status_bar);
    cat__stylesheet_button_hints_init_default(&s->button_hints);
    cat__stylesheet_recents_init_default(&s->recents);
    cat__stylesheet_games_init_default(&s->games);
    cat__stylesheet_menu_init_default(&s->menu);
    cat__stylesheet_font_init_default(&s->cjk_font);
    cat__stylesheet_launcher_init_default(&s->launcher);
}

static void cat__stylesheet_load_color(cat_color *out, cJSON *parent, const char *key) {
    cJSON *item = cJSON_GetObjectItem(parent, key);
    if (cJSON_IsString(item)) {
        *out = cat_color_from_hex(item->valuestring);
    }
}

static void cat__stylesheet_load_font(cat_stylesheet_font *f, cJSON *parent, const char *key) {
    cJSON *obj = cJSON_GetObjectItem(parent, key);
    if (!cJSON_IsObject(obj)) return;
    cJSON *path = cJSON_GetObjectItem(obj, "path");
    if (cJSON_IsString(path)) {
        strncpy(f->path, path->valuestring, sizeof(f->path) - 1);
        f->path[sizeof(f->path) - 1] = '\0';
    }
    cJSON *size = cJSON_GetObjectItem(obj, "size");
    if (cJSON_IsNumber(size)) f->size = size->valuedouble;
}

static void cat__stylesheet_load_ui(cat_stylesheet_ui *ui, cJSON *obj) {
    if (!cJSON_IsObject(obj)) return;
    cJSON *v;

    v = cJSON_GetObjectItem(obj, "margin_x"); if (cJSON_IsNumber(v)) ui->margin_x = v->valueint;
    v = cJSON_GetObjectItem(obj, "margin_y"); if (cJSON_IsNumber(v)) ui->margin_y = v->valueint;
    v = cJSON_GetObjectItem(obj, "list_margin"); if (cJSON_IsNumber(v)) ui->list_margin = v->valueint;
    v = cJSON_GetObjectItem(obj, "padding_x"); if (cJSON_IsNumber(v)) ui->padding_x = v->valueint;
    v = cJSON_GetObjectItem(obj, "padding_y"); if (cJSON_IsNumber(v)) ui->padding_y = v->valueint;

    cat__stylesheet_load_color(&ui->text_color, obj, "text_color");
    cat__stylesheet_load_color(&ui->text_stroke_color, obj, "text_stroke_color");
    cat__stylesheet_load_color(&ui->background_color, obj, "background_color");
    cat__stylesheet_load_color(&ui->highlight_color, obj, "highlight_color");
    cat__stylesheet_load_color(&ui->highlight_text_color, obj, "highlight_text_color");
    cat__stylesheet_load_color(&ui->highlight_text_stroke_color, obj, "highlight_text_stroke_color");
    cat__stylesheet_load_color(&ui->disabled_color, obj, "disabled_color");
    cat__stylesheet_load_color(&ui->hint_color, obj, "hint_color");
    cat__stylesheet_load_color(&ui->tab_color, obj, "tab_color");
    cat__stylesheet_load_color(&ui->tab_stroke_color, obj, "tab_stroke_color");
    cat__stylesheet_load_color(&ui->tab_selected_color, obj, "tab_selected_color");
    cat__stylesheet_load_color(&ui->tab_selected_stroke_color, obj, "tab_selected_stroke_color");

    v = cJSON_GetObjectItem(obj, "tab_font_size"); if (cJSON_IsNumber(v)) ui->tab_font_size = (float)v->valuedouble;
    v = cJSON_GetObjectItem(obj, "stroke_width"); if (cJSON_IsNumber(v)) ui->stroke_width = v->valueint;
    v = cJSON_GetObjectItem(obj, "pill_radius_ratio");
    if (cJSON_IsNumber(v)) {
        float r = (float)v->valuedouble;
        if (r < 0.0f) r = 0.0f;
        if (r > 1.0f) r = 1.0f;
        ui->pill_radius_ratio = r;
    }

    cat__stylesheet_load_font(&ui->ui_font, obj, "ui_font");
}

static void cat__stylesheet_load_status_bar(cat_stylesheet_status_bar *sb, cJSON *obj) {
    if (!cJSON_IsObject(obj)) return;
    cJSON *v;

    v = cJSON_GetObjectItem(obj, "show_battery_level"); if (cJSON_IsBool(v)) sb->show_battery_level = cJSON_IsTrue(v);
    v = cJSON_GetObjectItem(obj, "show_clock"); if (cJSON_IsBool(v)) sb->show_clock = cJSON_IsTrue(v);
    v = cJSON_GetObjectItem(obj, "show_wifi"); if (cJSON_IsBool(v)) sb->show_wifi = cJSON_IsTrue(v);
    v = cJSON_GetObjectItem(obj, "font_size"); if (cJSON_IsNumber(v)) sb->font_size = (float)v->valuedouble;

    cat__stylesheet_load_color(&sb->text_color, obj, "text_color");
    cat__stylesheet_load_color(&sb->text_stroke_color, obj, "text_stroke_color");
}

static void cat__stylesheet_load_button_hints(cat_stylesheet_button_hints *bh, cJSON *obj) {
    if (!cJSON_IsObject(obj)) return;
    cJSON *v;

    v = cJSON_GetObjectItem(obj, "button_hint_font_size"); if (cJSON_IsNumber(v)) bh->button_hint_font_size = (float)v->valuedouble;
    v = cJSON_GetObjectItem(obj, "button_size"); if (cJSON_IsNumber(v)) bh->button_size = (float)v->valuedouble;
    v = cJSON_GetObjectItem(obj, "button_text_font_size"); if (cJSON_IsNumber(v)) bh->button_text_font_size = (float)v->valuedouble;

    cat__stylesheet_load_color(&bh->button_a_color, obj, "button_a_color");
    cat__stylesheet_load_color(&bh->button_b_color, obj, "button_b_color");
    cat__stylesheet_load_color(&bh->button_x_color, obj, "button_x_color");
    cat__stylesheet_load_color(&bh->button_y_color, obj, "button_y_color");
    cat__stylesheet_load_color(&bh->button_bg_color, obj, "button_bg_color");
    cat__stylesheet_load_color(&bh->button_text_color, obj, "button_text_color");
    cat__stylesheet_load_color(&bh->text_color, obj, "text_color");
    cat__stylesheet_load_color(&bh->glyph_bg_color, obj, "glyph_bg_color");
}

static void cat__stylesheet_load_recents(cat_stylesheet_recents *r, cJSON *obj) {
    if (!cJSON_IsObject(obj)) return;
    cJSON *v = cJSON_GetObjectItem(obj, "use_recents_carousel");
    if (cJSON_IsBool(v)) r->use_recents_carousel = cJSON_IsTrue(v);
}

static void cat__stylesheet_load_games(cat_stylesheet_games *g, cJSON *obj) {
    if (!cJSON_IsObject(obj)) return;
    cJSON *v = cJSON_GetObjectItem(obj, "boxart_width");
    if (cJSON_IsNumber(v)) g->boxart_width = v->valueint;
}

static void cat__stylesheet_load_menu(cat_stylesheet_menu *m, cJSON *obj) {
    if (!cJSON_IsObject(obj)) return;
    cat__stylesheet_load_color(&m->background_color, obj, "background_color");
    cat__stylesheet_load_font(&m->guide_font, obj, "guide_font");
}

static void cat__stylesheet_load_launcher(cat_stylesheet_launcher *l, cJSON *obj) {
    if (!cJSON_IsObject(obj)) return;
    cJSON *v = cJSON_GetObjectItem(obj, "layout");
    if (cJSON_IsString(v)) {
        if (strcmp(v->valuestring, "vertical") == 0)
            l->layout = CAT_LAUNCHER_VERTICAL;
        else if (strcmp(v->valuestring, "horizontal") == 0)
            l->layout = CAT_LAUNCHER_HORIZONTAL;
        else if (strcmp(v->valuestring, "coverflow") == 0)
            l->layout = CAT_LAUNCHER_COVERFLOW;
        else if (strcmp(v->valuestring, "grid") == 0)
            l->layout = CAT_LAUNCHER_GRID;
        else
            l->layout = CAT_LAUNCHER_TABBED;
    }
    v = cJSON_GetObjectItem(obj, "list_split");
    if (cJSON_IsNumber(v) && v->valuedouble > 0.0 && v->valuedouble < 1.0)
        l->list_split = (float)v->valuedouble;
    v = cJSON_GetObjectItem(obj, "carousel_skew");
    if (cJSON_IsNumber(v) && v->valueint >= 0)
        l->carousel_skew = v->valueint;
    v = cJSON_GetObjectItem(obj, "coverflow_icon_size");
    if (cJSON_IsNumber(v)) l->coverflow_icon_size = v->valueint;
    v = cJSON_GetObjectItem(obj, "coverflow_side_size");
    if (cJSON_IsNumber(v)) l->coverflow_side_size = v->valueint;
    v = cJSON_GetObjectItem(obj, "coverflow_spacing");
    if (cJSON_IsNumber(v)) l->coverflow_spacing = v->valueint;
    v = cJSON_GetObjectItem(obj, "coverflow_side_alpha");
    if (cJSON_IsNumber(v)) l->coverflow_side_alpha = (uint8_t)v->valueint;
    v = cJSON_GetObjectItem(obj, "coverflow_anim_ms");
    if (cJSON_IsNumber(v)) l->coverflow_anim_ms = (uint32_t)v->valueint;
    v = cJSON_GetObjectItem(obj, "coverflow_icon_dir");
    if (cJSON_IsString(v)) {
        strncpy(l->coverflow_icon_dir, v->valuestring, sizeof(l->coverflow_icon_dir) - 1);
        l->coverflow_icon_dir[sizeof(l->coverflow_icon_dir) - 1] = '\0';
    }
    /* Grid. Counts and pixel values are clamped to sane floors so a bad theme
       cannot divide by zero or draw a negative tile; radius is a percentage. */
    v = cJSON_GetObjectItem(obj, "grid_cols");
    if (cJSON_IsNumber(v) && v->valueint >= 1 && v->valueint <= 8) l->grid_cols = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_rows");
    if (cJSON_IsNumber(v) && v->valueint >= 1 && v->valueint <= 6) l->grid_rows = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_margin");
    if (cJSON_IsNumber(v) && v->valueint >= 0) l->grid_margin = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_gutter");
    if (cJSON_IsNumber(v) && v->valueint >= 0) l->grid_gutter = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_radius_pct");
    if (cJSON_IsNumber(v) && v->valueint >= 0 && v->valueint <= 50) l->grid_radius_pct = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_border_w");
    if (cJSON_IsNumber(v) && v->valueint >= 0) l->grid_border_w = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_focus_border_w");
    if (cJSON_IsNumber(v) && v->valueint >= 0) l->grid_focus_border_w = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_focus_scale_pct");
    if (cJSON_IsNumber(v) && v->valueint >= 100 && v->valueint <= 150) l->grid_focus_scale_pct = v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_status_scale_pct");
    if (cJSON_IsNumber(v) && v->valueint >= 40 && v->valueint <= 100) l->grid_status_scale_pct = v->valueint;
    cat__stylesheet_load_color(&l->grid_border_color, obj, "grid_border_color");
    cat__stylesheet_load_color(&l->grid_focus_border_color, obj, "grid_focus_border_color");
    v = cJSON_GetObjectItem(obj, "grid_anim_ms");
    if (cJSON_IsNumber(v) && v->valueint >= 0) l->grid_anim_ms = (uint32_t)v->valueint;
    v = cJSON_GetObjectItem(obj, "grid_icon_dir");
    if (cJSON_IsString(v)) {
        strncpy(l->grid_icon_dir, v->valuestring, sizeof(l->grid_icon_dir) - 1);
        l->grid_icon_dir[sizeof(l->grid_icon_dir) - 1] = '\0';
    }
    v = cJSON_GetObjectItem(obj, "grid_label_dir");
    if (cJSON_IsString(v)) {
        strncpy(l->grid_label_dir, v->valuestring, sizeof(l->grid_label_dir) - 1);
        l->grid_label_dir[sizeof(l->grid_label_dir) - 1] = '\0';
    }
}

static int cat__stylesheet_load_from_cjson(cat_stylesheet *s, cJSON *root) {
    if (!cJSON_IsObject(root)) return CAT_ERROR;

    cJSON *v = cJSON_GetObjectItem(root, "wallpaper");
    if (cJSON_IsString(v)) {
        strncpy(s->wallpaper, v->valuestring, sizeof(s->wallpaper) - 1);
        s->wallpaper[sizeof(s->wallpaper) - 1] = '\0';
    }

    cat__stylesheet_load_ui(&s->ui, cJSON_GetObjectItem(root, "ui"));
    cat__stylesheet_load_status_bar(&s->status_bar, cJSON_GetObjectItem(root, "status_bar"));
    cat__stylesheet_load_button_hints(&s->button_hints, cJSON_GetObjectItem(root, "button_hints"));
    cat__stylesheet_load_recents(&s->recents, cJSON_GetObjectItem(root, "recents"));
    cat__stylesheet_load_games(&s->games, cJSON_GetObjectItem(root, "games"));
    cat__stylesheet_load_menu(&s->menu, cJSON_GetObjectItem(root, "menu"));
    cat__stylesheet_load_font(&s->cjk_font, root, "cjk_font");
    cat__stylesheet_load_launcher(&s->launcher, cJSON_GetObjectItem(root, "launcher"));

    return CAT_OK;
}

int cat_stylesheet_load_file(cat_stylesheet *s, const char *path) {
    if (!s || !path) return CAT_ERROR;

    FILE *fp = fopen(path, "rb");
    if (!fp) {
        cat__set_error("cat_stylesheet_load_file: cannot open '%s'", path);
        return CAT_ERROR;
    }

    fseek(fp, 0, SEEK_END);
    long len = ftell(fp);
    if (len <= 0) { fclose(fp); return CAT_ERROR; }
    fseek(fp, 0, SEEK_SET);

    char *json = (char *)malloc((size_t)len + 1);
    if (!json) { fclose(fp); return CAT_ERROR; }

    size_t nread = fread(json, 1, (size_t)len, fp);
    fclose(fp);
    json[nread] = '\0';

    cJSON *root = cJSON_Parse(json);
    free(json);

    if (!root) {
        const char *err = cJSON_GetErrorPtr();
        cat__set_error("cat_stylesheet_load_file: JSON parse error at '%s'", err ? err : "unknown");
        return CAT_ERROR;
    }

    cat_stylesheet_init_default(s);
    int rc = cat__stylesheet_load_from_cjson(s, root);
    cJSON_Delete(root);
    return rc;
}

/* Ordered list of directories scanned for themes. Lookup order:
   1. $CAT_THEMES_DIR (set via getenv at init) — explicit override
   2. ./res/themes/                            — bundled defaults (Catastrophe, Catastrophe-Demo)
   3. ./themes/Allium-Themes/Themes/           — git submodule
   4. platform env-contract paths              — bundled/device themes */
static int cat__theme_dirs(const char *out[], int max) {
    int n = 0;
    if (cat__g.themes_dir[0] && n < max) out[n++] = cat__g.themes_dir;
    if (n < max) out[n++] = "./res/themes";
    if (n < max) out[n++] = "./themes/Allium-Themes/Themes";
#if defined(PLATFORM_TG5040) || defined(PLATFORM_TG5050) || defined(PLATFORM_MY355)
    static char allium_themes[PATH_MAX];
    const char *sdcard = cat__sdcard_path();
    if (sdcard && n < max) {
        snprintf(allium_themes, sizeof(allium_themes), "%s/Themes", sdcard);
        out[n++] = allium_themes;
    }
#elif defined(PLATFORM_MLP1)
    static char launcher_themes[PATH_MAX];
    char launcher_buf[PATH_MAX];
    const char *launcher = cat__launcher_path(launcher_buf, sizeof(launcher_buf));
    if (launcher && n < max &&
        snprintf(launcher_themes, sizeof(launcher_themes), "%s/res/themes", launcher)
            < (int)sizeof(launcher_themes)) {
        out[n++] = launcher_themes;
    }
#endif
    return n;
}

/* Find the first directory in cat__theme_dirs() that contains <theme>/stylesheet.json.
   Writes the parent directory into `out_dir`. Returns true if found. */
static bool cat__find_theme_dir(const char *theme_name, char *out_dir, size_t out_size) {
    const char *dirs[8];
    int n = cat__theme_dirs(dirs, 8);
    for (int i = 0; i < n; i++) {
        char ss[1152];
        snprintf(ss, sizeof(ss), "%s/%s/stylesheet.json", dirs[i], theme_name);
        FILE *f = fopen(ss, "rb");
        if (f) {
            fclose(f);
            strncpy(out_dir, dirs[i], out_size - 1);
            out_dir[out_size - 1] = '\0';
            return true;
        }
    }
    return false;
}

int cat_stylesheet_load_theme(cat_stylesheet *s, const char *theme_name) {
    if (!s || !theme_name) return CAT_ERROR;

    char theme_dir[1024];
    if (!cat__find_theme_dir(theme_name, theme_dir, sizeof(theme_dir))) {
        cat__set_error("Theme not found: %s", theme_name);
        return CAT_ERROR;
    }

    char path[1280];
    snprintf(path, sizeof(path), "%s/%s/stylesheet.json", theme_dir, theme_name);

    int rc = cat_stylesheet_load_file(s, path);
    if (rc != CAT_OK) return rc;

    strncpy(cat__g.active_theme, theme_name, sizeof(cat__g.active_theme) - 1);
    cat__g.active_theme[sizeof(cat__g.active_theme) - 1] = '\0';
    strncpy(cat__g.active_theme_dir, theme_dir, sizeof(cat__g.active_theme_dir) - 1);
    cat__g.active_theme_dir[sizeof(cat__g.active_theme_dir) - 1] = '\0';

    char override_path[1280];
    snprintf(override_path, sizeof(override_path), "%s/%s/stylesheet.override.json",
             theme_dir, theme_name);

    FILE *ov = fopen(override_path, "rb");
    if (ov) {
        fclose(ov);
        cJSON *root = NULL;
        FILE *fp = fopen(override_path, "rb");
        if (fp) {
            fseek(fp, 0, SEEK_END);
            long olen = ftell(fp);
            fseek(fp, 0, SEEK_SET);
            if (olen > 0) {
                char *ojson = (char *)malloc((size_t)olen + 1);
                if (ojson) {
                    size_t onread = fread(ojson, 1, (size_t)olen, fp);
                    ojson[onread] = '\0';
                    root = cJSON_Parse(ojson);
                    free(ojson);
                }
            }
            fclose(fp);
        }
        if (root) {
            cat__stylesheet_load_from_cjson(s, root);
            cJSON_Delete(root);
        }
    }

    return CAT_OK;
}

/* Copy src into a fixed dst buffer, always NUL-terminating. Truncates if src is
   too long (callers tolerate an unresolved path). Checking the snprintf result
   keeps the build clean under -Wformat-truncation. */
/* memmove, not snprintf: callers may pass a src inside dst (cat_set_font_bump
   reloads from cat__g.theme.font_path into itself), and glibc's snprintf
   clears dst before reading src, leaving an empty string. */
static void cat__str_copy(char *dst, size_t dst_size, const char *src) {
    if (!dst || dst_size == 0) return;
    if (!src) src = "";
    size_t n = strlen(src);
    if (n >= dst_size) n = dst_size - 1;
    memmove(dst, src, n);
    dst[n] = '\0';
}

static void cat__stylesheet_to_theme(const cat_stylesheet *s, cat_theme *t) {
    t->highlight        = cat_color_to_sdl(s->ui.highlight_color);
    /* Accent (used for footer button-hint pills, section titles, queue/progress fills) is
       sourced from button_hints.button_a_color — the closest Allium analogue to a single
       primary accent. Apostrophe parity = #9B2257 set in the C defaults. */
    t->accent           = cat_color_to_sdl(s->button_hints.button_a_color);
    t->button_label     = cat_color_to_sdl(s->button_hints.button_text_color);
    /* Inner button-glyph pill: prefer explicit glyph_bg_color, fall back to highlight_color
       so themes that don't opt in (Allium, older Jawaka) render exactly as before. */
    t->button_glyph_bg  = cat_color_to_sdl(
        s->button_hints.glyph_bg_color ? s->button_hints.glyph_bg_color
                                       : s->ui.highlight_color);
    t->text             = cat_color_to_sdl(s->ui.text_color);
    t->highlighted_text = cat_color_to_sdl(s->ui.highlight_text_color);
    t->disabled         = cat_color_to_sdl(s->ui.disabled_color);
    /* Hint text: prefer explicit hint_color, fall back to disabled_color (Allium parity). */
    t->hint             = cat_color_to_sdl(
        s->ui.hint_color ? s->ui.hint_color : s->ui.disabled_color);
    t->background       = cat_color_to_sdl(s->ui.background_color);
    t->ui_padding_x      = s->ui.padding_x;
    t->ui_padding_y      = s->ui.padding_y;
    t->pill_radius_ratio = s->ui.pill_radius_ratio;

    cat__str_copy(t->font_path, sizeof(t->font_path), s->ui.ui_font.path);
    cat__str_copy(t->bg_image_path, sizeof(t->bg_image_path), s->wallpaper);
}

const char *cat_get_active_theme_dir(void)  { return cat__g.active_theme_dir; }
const char *cat_get_active_theme_name(void) { return cat__g.active_theme; }

int cat_stylesheet_apply(cat_stylesheet *s) {
    if (!s) return CAT_ERROR;

    /* Persist the active stylesheet so cat_get_stylesheet() returns what was
       just applied (not the init-time defaults). Consumers like the Jawaka
       launcher read `launcher.layout`, `tab_color`, etc. from this. */
    cat__g.stylesheet = *s;

    cat__stylesheet_to_theme(s, &cat__g.theme);

    /* Font path is stored but NOT reloaded here — TTF_CloseFont/OpenFont
       cycle mid-execution causes segfaults. Font changes take effect on restart. */
    if (s->ui.ui_font.path[0]) {
        strncpy(cat__g.theme.font_path, s->ui.ui_font.path, sizeof(cat__g.theme.font_path) - 1);
        cat__g.theme.font_path[sizeof(cat__g.theme.font_path) - 1] = '\0';
    }

    /* Load wallpaper if present; otherwise clear stale texture so
       cat_draw_background() falls through to the solid ui.background_color. */
    if (s->wallpaper[0]) {
        char wpath[sizeof(cat__g.active_theme_dir) +
                   sizeof(cat__g.active_theme) +
                   sizeof(s->wallpaper) + 2u];
        int needed = 0;
        if (s->wallpaper[0] == '/') {
            needed = snprintf(wpath, sizeof(wpath), "%s", s->wallpaper);
        } else if (cat__g.active_theme_dir[0] && cat__g.active_theme[0]) {
            needed = snprintf(wpath, sizeof(wpath), "%s/%s/%s",
                              cat__g.active_theme_dir, cat__g.active_theme, s->wallpaper);
        } else {
            needed = snprintf(wpath, sizeof(wpath), "%s", s->wallpaper);
        }
        if (needed < 0 || (size_t)needed >= sizeof(wpath)) {
            cat__set_error("Wallpaper path too long: %s", s->wallpaper);
            return CAT_ERROR;
        }
        cat_reload_background(wpath);
    } else if (cat__g.bg_texture) {
        SDL_DestroyTexture(cat__g.bg_texture);
        cat__g.bg_texture = NULL;
    }

    return CAT_OK;
}

int cat_stylesheet_available_themes(const char ***out_names, int *out_count) {
    if (!out_names || !out_count) return CAT_ERROR;

    int cap = 32;
    int cnt = 0;
    const char **names = (const char **)malloc((size_t)cap * sizeof(char *));
    if (!names) return CAT_ERROR;

    const char *dirs[8];
    int n_dirs = cat__theme_dirs(dirs, 8);
    for (int di = 0; di < n_dirs; di++) {
        DIR *d = opendir(dirs[di]);
        if (!d) continue;

        struct dirent *entry;
        while ((entry = readdir(d)) != NULL) {
            if (entry->d_name[0] == '.') continue;

            char ss_path[1152];
            snprintf(ss_path, sizeof(ss_path), "%s/%s/stylesheet.json", dirs[di], entry->d_name);
            FILE *f = fopen(ss_path, "rb");
            if (!f) continue;
            fclose(f);

            /* Skip duplicates — first directory wins (matches the resolve order) */
            bool dup = false;
            for (int i = 0; i < cnt; i++) {
                if (names[i] && strcmp(names[i], entry->d_name) == 0) { dup = true; break; }
            }
            if (dup) continue;

            if (cnt >= cap) {
                cap *= 2;
                const char **tmp = (const char **)realloc((void *)names, (size_t)cap * sizeof(char *));
                if (!tmp) {
                    for (int i = 0; i < cnt; i++) free((void *)names[i]);
                    free((void *)names);
                    closedir(d);
                    return CAT_ERROR;
                }
                names = tmp;
            }

            names[cnt] = strdup(entry->d_name);
            if (names[cnt]) cnt++;
        }
        closedir(d);
    }

    *out_names = names;
    *out_count = cnt;
    return CAT_OK;
}

void cat_stylesheet_free_theme_list(const char **names, int count) {
    if (!names) return;
    for (int i = 0; i < count; i++) {
        if (names[i]) free((void *)names[i]);
    }
    free((void *)names);
}

void cat_stylesheet_free_string_list(const char **names, int count) {
    cat_stylesheet_free_theme_list(names, count);
}

int cat_stylesheet_list_wallpapers(const char *theme_dir, const char ***out, int *count) {
    if (!out || !count) return CAT_ERROR;
    *out = NULL;
    *count = 0;
    if (!theme_dir || !theme_dir[0]) return CAT_OK;

    DIR *d = opendir(theme_dir);
    if (!d) return CAT_OK;

    int cap = 16;
    int cnt = 0;
    const char **names = (const char **)malloc((size_t)cap * sizeof(char *));
    if (!names) { closedir(d); return CAT_ERROR; }

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL) {
        if (entry->d_name[0] == '.') continue;
        const char *ext = strrchr(entry->d_name, '.');
        if (!ext) continue;
        if (strcasecmp(ext, ".png") != 0 && strcasecmp(ext, ".jpg") != 0 && strcasecmp(ext, ".jpeg") != 0)
            continue;

        if (cnt >= cap) {
            cap *= 2;
            const char **tmp = (const char **)realloc((void *)names, (size_t)cap * sizeof(char *));
            if (!tmp) {
                for (int i = 0; i < cnt; i++) free((void *)names[i]);
                free((void *)names);
                closedir(d);
                return CAT_ERROR;
            }
            names = tmp;
        }
        names[cnt] = strdup(entry->d_name);
        if (names[cnt]) cnt++;
    }
    closedir(d);
    *out = names;
    *count = cnt;
    return CAT_OK;
}

int cat_reload_fonts(const char *font_path) {
    /* A reload usually means a language or theme change, which can retire the
       CJK face too. Its cache otherwise waits for the next CJK string to notice,
       and a UI that has just left Japanese may never draw one, so the old face
       would stay open for the life of the process. Clearing the remembered path
       makes the next CJK lookup start over. */
    for (int i = 0; i < CAT_FONT_TIER_COUNT; i++) {
        if (cat__g.cjk_fonts[i]) {
            TTF_CloseFont(cat__g.cjk_fonts[i]);
            cat__g.cjk_fonts[i] = NULL;
        }
    }
    cat__g.cjk_font_rel[0] = '\0';
    /* Uses the internal cat__load_fonts which already handles old-font cleanup */
    return cat__load_fonts(font_path);
}

static void cat__state_file_path(char *buf, size_t buf_size) {
#if CAT_PLATFORM_IS_DEVICE
    const char *explicit_path = cat__env_nonempty("CAT_THEME_STATE_PATH");
    if (explicit_path) {
        snprintf(buf, buf_size, "%s", explicit_path);
        return;
    }
    const char *base = cat__env_nonempty("ALLIUM_BASE_DIR");
    if (base && base[0]) {
        if (snprintf(buf, buf_size, "%s/state/theme", base) >= (int)buf_size)
            buf[0] = '\0';
    } else {
        char userdata_buf[PATH_MAX];
        const char *userdata = cat__userdata_path(userdata_buf, sizeof(userdata_buf));
        if (snprintf(buf, buf_size, "%s/theme", userdata ? userdata : ".catastrophe/state") >= (int)buf_size)
            buf[0] = '\0';
    }
#else
    snprintf(buf, buf_size, ".catastrophe/state/theme");
#endif
}

int cat_theme_state_load(char *out_name, size_t out_size) {
    if (!out_name || out_size == 0) return CAT_ERROR;
    out_name[0] = '\0';

    char path[512];
    cat__state_file_path(path, sizeof(path));

    FILE *f = fopen(path, "r");
    if (!f) {
        strncpy(out_name, "Catastrophe", out_size - 1);
        out_name[out_size - 1] = '\0';
        return CAT_OK;
    }

    if (!fgets(out_name, (int)out_size, f)) out_name[0] = '\0';
    fclose(f);

    size_t len = strlen(out_name);
    while (len > 0 && (out_name[len - 1] == '\n' || out_name[len - 1] == '\r'))
        out_name[--len] = '\0';

    if (out_name[0] == '\0') {
        strncpy(out_name, "Catastrophe", out_size - 1);
        out_name[out_size - 1] = '\0';
    }
    return CAT_OK;
}

int cat_theme_state_save(const char *theme_name) {
    if (!theme_name || !theme_name[0]) return CAT_ERROR;

    char path[512];
    cat__state_file_path(path, sizeof(path));

    char *slash = strrchr(path, '/');
    if (slash) {
        *slash = '\0';
        mkdir(path, 0755);
        *slash = '/';
    }

    FILE *f = fopen(path, "w");
    if (!f) return CAT_ERROR;
    fprintf(f, "%s\n", theme_name);
    fclose(f);
    return CAT_OK;
}

/* ─── Theme Loading ──────────────────────────────────────────────────────── */

cat_theme *cat_get_theme(void) {
    return &cat__g.theme;
}

const cat_stylesheet *cat_get_stylesheet(void) {
    return &cat__g.stylesheet;
}

int cat_reload_background(const char *bg_path) {
    const char *resolved = bg_path;

    if (!resolved || !resolved[0]) {
        resolved = getenv("CAT_BACKGROUND_PATH");
    #if CAT_PLATFORM_IS_DEVICE
        if (!resolved || !resolved[0]) {
            static char bg_buf[PATH_MAX];
            const char *sdcard = cat__sdcard_path();
            if (sdcard) {
                snprintf(bg_buf, sizeof(bg_buf), "%s/bg.png", sdcard);
                resolved = bg_buf;
            }
        }
    #endif
    }

    if (!resolved || !resolved[0]) {
        return CAT_OK;
    }

    SDL_Texture *new_tex = cat_load_image(resolved);
    if (!new_tex) {
        cat_log("Warning: could not reload background: %s", resolved);
        return CAT_ERROR;
    }

    if (cat__g.bg_texture) {
        SDL_DestroyTexture(cat__g.bg_texture);
    }
    cat__g.bg_texture = new_tex;

    strncpy(cat__g.theme.bg_image_path, resolved, sizeof(cat__g.theme.bg_image_path) - 1);
    cat__g.theme.bg_image_path[sizeof(cat__g.theme.bg_image_path) - 1] = '\0';
    cat_log("Reloaded background: %s", resolved);
    return CAT_OK;
}

/* ─── Scaling ────────────────────────────────────────────────────────────── */

float cat_get_scale_factor(void) {
    return cat__g.scale_factor;
}

int cat_scale(int base) {
    return (int)(base * cat__g.scale_factor);
}

int cat_device_scale(int base) {
    return base * cat__g.device_scale;
}

int cat_font_size_for_resolution(int base_size) {
    int scale = cat__g.device_scale ? cat__g.device_scale : 2;
    return base_size * scale;
}

static void cat__compute_scale_factor(void) {
    float raw = (float)cat__g.screen_w / (float)CAT_REFERENCE_WIDTH;
    if (raw > 1.0f)
        cat__g.scale_factor = 1.0f + (raw - 1.0f) * CAT_SCALE_DAMPING;
    else
        cat__g.scale_factor = raw;
}

static void cat__resolve_device_metrics(void) {
#if defined(PLATFORM_MLP1)
    cat__g.device_scale = 2;
    cat__g.device_padding = 10;
#elif defined(PLATFORM_TG5040) || !CAT_PLATFORM_IS_DEVICE
    /* Match TG5040 preview profiles by resolution: Brick 1024×768 uses 3/5,
       handheld-style layouts use 2/10. Desktop previews follow the same rule. */
    if (cat__g.screen_w <= 1024 && cat__g.screen_h >= 768) {
        cat__g.device_scale = 3;
        cat__g.device_padding = 5;
    } else {
        cat__g.device_scale = 2;
        cat__g.device_padding = 10;
    }
#elif defined(PLATFORM_TG5050)
    cat__g.device_scale = 2;
    cat__g.device_padding = 10;
#elif defined(PLATFORM_MY355)
    cat__g.device_scale = 2;
    cat__g.device_padding = 10;
#else
    cat__g.device_scale = 2;
    cat__g.device_padding = 10;
#endif
}

/* ─── Font Management ────────────────────────────────────────────────────── */

static int cat__compute_font_bump(void) {
    int ds = cat__g.device_scale ? cat__g.device_scale : 2;
    int logical_w = cat__g.screen_w / ds;
    int logical_h = cat__g.screen_h / ds;
    int bump_w = ((logical_w - CAT_FONT_BUMP_REF_LOGICAL_W) * CAT_FONT_BUMP_MAX)
                 / CAT_FONT_BUMP_REF_LOGICAL_W;
    int bump_h = ((logical_h - CAT_FONT_BUMP_REF_LOGICAL_H) * CAT_FONT_BUMP_MAX)
                 / CAT_FONT_BUMP_REF_LOGICAL_H;
    int bump = (bump_w < bump_h) ? bump_w : bump_h;
    return cat__clamp(bump, 0, CAT_FONT_BUMP_MAX);
}

static TTF_Font *cat__open_font(const char *path, int size) {
    TTF_Font *f = TTF_OpenFont(path, size);
    if (f) TTF_SetFontStyle(f, TTF_STYLE_BOLD); /* all fonts loaded bold for clarity on small handheld screens */
    return f;
}

static int cat__load_fonts(const char *user_font_path) {
    /* Determine font path */
    const char *font_path = NULL;

    /* 1. User-specified path (allows font override via cat_config.font_path) */
    if (user_font_path && user_font_path[0]) {
        if (access(user_font_path, R_OK) == 0) {
            font_path = user_font_path;
        }
    }

    /* 1b. Try CAT_FONTS_DIR env var for relative font paths (Allium themes use "Nunito.ttf") */
    if (!font_path && user_font_path && user_font_path[0] && user_font_path[0] != '/') {
        const char *fd = getenv("CAT_FONTS_DIR");
        if (fd && fd[0]) {
            static char fd_buf[1152];
            snprintf(fd_buf, sizeof(fd_buf), "%s/%s", fd, user_font_path);
            if (access(fd_buf, R_OK) == 0) {
                font_path = fd_buf;
            }
        }
    }

    /* 1c. Shared fonts tree — res/fonts/<path> relative to cwd or parent.
     * Themes reference bundled fonts as e.g. "fonts/SpaceGrotesk/SpaceGrotesk-Regular.ttf". */
    if (!font_path && user_font_path && user_font_path[0] && user_font_path[0] != '/') {
        static char rf_buf[1152];
        const char *rf_prefixes[] = {
            "./res/", "../res/", "../../res/", NULL
        };
        for (int i = 0; rf_prefixes[i] && !font_path; i++) {
            snprintf(rf_buf, sizeof(rf_buf), "%s%s", rf_prefixes[i], user_font_path);
            if (access(rf_buf, R_OK) == 0)
                font_path = rf_buf;
        }
    }

    /* 1d. Runtime contract paths for bundled/device fonts. */
    if (!font_path) {
        static char candidates[8][PATH_MAX];
        int candidate_count = 0;
        const char *fd = cat__env_nonempty("CAT_FONTS_DIR");
        if (fd && candidate_count < 8) {
            snprintf(candidates[candidate_count++], PATH_MAX, "%s/font.ttf", fd);
        }
        if (fd && candidate_count < 8) {
            snprintf(candidates[candidate_count++], PATH_MAX, "%s/Nunito.ttf", fd);
        }
#if defined(PLATFORM_MLP1)
        char launcher_buf[PATH_MAX];
        const char *launcher = cat__launcher_path(launcher_buf, sizeof(launcher_buf));
        if (launcher && candidate_count < 8 &&
            snprintf(candidates[candidate_count], PATH_MAX, "%s/res/font.ttf", launcher) < PATH_MAX) {
            candidate_count++;
        }
        if (launcher && candidate_count < 8 &&
            snprintf(candidates[candidate_count], PATH_MAX,
                     "%s/res/fonts/SpaceGrotesk/SpaceGrotesk-Regular.ttf", launcher) < PATH_MAX) {
            candidate_count++;
        }
#elif defined(PLATFORM_TG5040) || defined(PLATFORM_TG5050) || defined(PLATFORM_MY355)
        const char *sdcard = cat__sdcard_path();
        if (sdcard && candidate_count < 8) {
            snprintf(candidates[candidate_count++], PATH_MAX,
                     "%s/.allium/fonts/Nunito.ttf", sdcard);
        }
        if (sdcard && candidate_count < 8) {
            snprintf(candidates[candidate_count++], PATH_MAX,
                     "%s/Themes/Allium/fonts/Nunito.ttf", sdcard);
        }
        if (sdcard && candidate_count < 8) {
            snprintf(candidates[candidate_count++], PATH_MAX,
                     "%s/.allium/fonts/font.ttf", sdcard);
        }
#endif
        for (int i = 0; i < candidate_count; i++) {
            if (access(candidates[i], R_OK) == 0) {
                font_path = candidates[i];
                break;
            }
        }
    }

    /* 2. Search fallback paths */
    if (!font_path) {
        for (int i = 0; cat__font_search_paths[i]; i++) {
            if (access(cat__font_search_paths[i], R_OK) == 0) {
                font_path = cat__font_search_paths[i];
                break;
            }
        }
    }

    if (!font_path) {
        cat__set_error("No font file found");
        cat_log("ERROR: No font file found in any search path");
        return CAT_ERROR;
    }

    /* Store in theme */
    cat__str_copy(cat__g.theme.font_path, sizeof(cat__g.theme.font_path), font_path);
    cat_log("Loading font: %s", font_path);

    /* Open all tiers into temp handles first, so a failure doesn't destroy old fonts */
    TTF_Font *new_fonts[CAT_FONT_TIER_COUNT];
    for (int i = 0; i < CAT_FONT_TIER_COUNT; i++) {
        int size = cat_font_size_for_resolution(cat__font_base_sizes[i] + cat__g.font_bump);
        if (size < 8) size = 8;
        new_fonts[i] = cat__open_font(font_path, size);
        if (!new_fonts[i]) {
            cat__set_error("Failed to open font at size %d: %s", size, TTF_GetError());
            cat_log("ERROR: Failed to open font tier %d (size %d): %s", i, size, TTF_GetError());
            for (int j = 0; j < i; j++) TTF_CloseFont(new_fonts[j]);
            return CAT_ERROR;
        }
    }

    /* All opened successfully — swap in and close old */
    cat__text_cache_clear();
    for (int i = 0; i < CAT_FONT_TIER_COUNT; i++) {
        if (cat__g.fonts[i]) TTF_CloseFont(cat__g.fonts[i]);
        cat__g.fonts[i] = new_fonts[i];
    }

    return CAT_OK;
}

TTF_Font *cat_get_font(cat_font_tier tier) {
    if (tier < 0 || tier >= CAT_FONT_TIER_COUNT) return cat__g.fonts[CAT_FONT_SMALL];
    return cat__g.fonts[tier];
}

/* Resolve the always-bundled base font.ttf (a glyph-complete Nerd Font) so UI
 * symbols the themed family lacks still render. Mirrors the font.ttf candidate
 * search in cat__load_fonts. */
static bool cat__resolve_symbol_font_path(char *out, size_t out_size) {
    const char *fd = cat__env_nonempty("CAT_FONTS_DIR");
    if (fd) {
        snprintf(out, out_size, "%s/font.ttf", fd);
        if (access(out, R_OK) == 0) return true;
    }
#if defined(PLATFORM_MLP1)
    char launcher_buf[PATH_MAX];
    const char *launcher = cat__launcher_path(launcher_buf, sizeof(launcher_buf));
    if (launcher &&
        snprintf(out, out_size, "%s/res/font.ttf", launcher) < (int)out_size &&
        access(out, R_OK) == 0) {
        return true;
    }
#endif
    const char *rel[] = { "./res/font.ttf", "../res/font.ttf", "../../res/font.ttf", NULL };
    for (int i = 0; rel[i]; i++) {
        snprintf(out, out_size, "%s", rel[i]);
        if (access(out, R_OK) == 0) return true;
    }
    return false;
}

TTF_Font *cat_get_symbol_font(void) {
    int bump = cat__g.font_bump;
    if (!cat__g.symbol_font || cat__g.symbol_font_bump != bump) {
        if (cat__g.symbol_font) {
            cat__text_cache_clear();
            TTF_CloseFont(cat__g.symbol_font);
            cat__g.symbol_font = NULL;
        }
        char path[PATH_MAX];
        if (cat__resolve_symbol_font_path(path, sizeof(path))) {
            int size = cat_font_size_for_resolution(cat__font_base_sizes[CAT_FONT_LARGE] + bump);
            if (size < 8) size = 8;
            cat__g.symbol_font = cat__open_font(path, size);
        }
        cat__g.symbol_font_bump = bump;
    }
    return cat__g.symbol_font ? cat__g.symbol_font : cat_get_font(CAT_FONT_LARGE);
}

/* Footer/hint-sized symbol font: the same glyph-complete base font.ttf as
   cat_get_symbol_font, but at CAT_FONT_SMALL so it fits a footer pill (LARGE is
   too tall there). Used for footer button glyphs the themed family lacks. */
static TTF_Font *cat__get_symbol_font_small(void) {
    int bump = cat__g.font_bump;
    if (!cat__g.symbol_font_small || cat__g.symbol_font_small_bump != bump) {
        if (cat__g.symbol_font_small) {
            cat__text_cache_clear();
            TTF_CloseFont(cat__g.symbol_font_small);
            cat__g.symbol_font_small = NULL;
        }
        char path[PATH_MAX];
        if (cat__resolve_symbol_font_path(path, sizeof(path))) {
            int size = cat_font_size_for_resolution(cat__font_base_sizes[CAT_FONT_SMALL] + bump);
            if (size < 8) size = 8;
            cat__g.symbol_font_small = cat__open_font(path, size);
        }
        cat__g.symbol_font_small_bump = bump;
    }
    return cat__g.symbol_font_small ? cat__g.symbol_font_small : cat_get_font(CAT_FONT_SMALL);
}

/* ─── CJK text fallback ──────────────────────────────────────────────────
 * None of the themed families carry CJK, so a Chinese or Japanese ROM name
 * renders as tofu. Rather than split a string into per-script runs (which would
 * mean measuring and positioning each run, and rewriting every draw entry
 * point), substitute the whole string onto the stylesheet's cjk_font when it
 * contains any CJK codepoint. Mixed "游戏 Game" strings render entirely in the
 * CJK face, which is fine: Source Han Sans has full Latin coverage.
 *
 * The substitution MUST happen identically in drawing and in measurement, or
 * ellipsizing and the marquee compute widths against a different face than the
 * one that renders. Both go through cat__font_for_text for that reason. */

/* True if the UTF-8 string contains a codepoint that the Latin families lack.
   Covers CJK Unified plus Ext-A, compatibility ideographs, CJK punctuation,
   fullwidth forms, and kana -- Japanese ROM names are as common as Chinese
   ones. Decodes only far enough to classify; malformed bytes are skipped. */
static bool cat__text_has_cjk(const char *s) {
    if (!s) return false;
    for (const unsigned char *p = (const unsigned char *)s; *p; ) {
        uint32_t cp;
        int len;
        if (*p < 0x80)            { p++; continue; }        /* ASCII fast path */
        else if ((*p & 0xE0) == 0xC0) { cp = *p & 0x1Fu; len = 2; }
        else if ((*p & 0xF0) == 0xE0) { cp = *p & 0x0Fu; len = 3; }
        else if ((*p & 0xF8) == 0xF0) { cp = *p & 0x07u; len = 4; }
        else                      { p++; continue; }        /* stray continuation */
        int i = 1;
        for (; i < len; i++) {
            if ((p[i] & 0xC0) != 0x80) break;               /* truncated sequence */
            cp = (cp << 6) | (uint32_t)(p[i] & 0x3Fu);
        }
        if (i < len) { p++; continue; }
        p += len;
        if ((cp >= 0x3000u && cp <= 0x30FFu) ||   /* CJK punctuation + kana     */
            (cp >= 0x3400u && cp <= 0x4DBFu) ||   /* CJK Unified Ext-A          */
            (cp >= 0x4E00u && cp <= 0x9FFFu) ||   /* CJK Unified                */
            (cp >= 0xF900u && cp <= 0xFAFFu) ||   /* compatibility ideographs   */
            (cp >= 0xFF00u && cp <= 0xFFEFu))     /* fullwidth / halfwidth forms*/
            return true;
    }
    return false;
}

/* Resolve the stylesheet's cjk_font path the same way cat__load_fonts resolves a
   themed family: absolute, then CAT_FONTS_DIR, then the shared res/fonts tree. */
static bool cat__resolve_cjk_font_path(const char *rel, char *out, size_t out_size) {
    if (!rel || !rel[0]) return false;
    if (rel[0] == '/') {
        snprintf(out, out_size, "%s", rel);
        return access(out, R_OK) == 0;
    }
    const char *fd = cat__env_nonempty("CAT_FONTS_DIR");
    if (fd) {
        snprintf(out, out_size, "%s/%s", fd, rel);
        if (access(out, R_OK) == 0) return true;
    }
#if defined(PLATFORM_MLP1)
    char launcher_buf[PATH_MAX];
    const char *launcher = cat__launcher_path(launcher_buf, sizeof(launcher_buf));
    if (launcher &&
        snprintf(out, out_size, "%s/res/%s", launcher, rel) < (int)out_size &&
        access(out, R_OK) == 0)
        return true;
#endif
    const char *prefixes[] = { "./res/", "../res/", "../../res/", NULL };
    for (int i = 0; prefixes[i]; i++) {
        snprintf(out, out_size, "%s%s", prefixes[i], rel);
        if (access(out, R_OK) == 0) return true;
    }
    return false;
}

/* The CJK face at the same size as `base`. Tier is recovered by pointer
   identity against the loaded tiers, which is exact -- deriving a size from
   TTF_FontHeight would guess. Returns `base` unchanged when no cjk_font is
   declared, when it cannot be resolved, or when `base` is not a tier font
   (the symbol fonts, which are already glyph-complete). */
static TTF_Font *cat__cjk_font_for(TTF_Font *base) {
    if (!base) return base;

    /* CAT_CJK_FONT_PATH beats the stylesheet. The theme's face is a regional
       choice (Simplified Chinese glyph forms), and a host running a Japanese UI
       needs Japanese forms for the same codepoints. Read per call rather than
       cached so a host that switches language in-process only has to setenv;
       the path compare below then drops the cache. */
    const cat_stylesheet *ss = cat_get_stylesheet();
    const char *rel = cat__env_nonempty("CAT_CJK_FONT_PATH");
    if (!rel) rel = ss ? ss->cjk_font.path : NULL;
    if (!rel || !rel[0]) return base;

    /* A theme or language switch changes the face, and a font-size change
       invalidates every open size. Either drops the whole cache, and a new face
       gets a fresh chance to resolve even if the previous one could not. */
    if (cat__g.cjk_font_bump != cat__g.font_bump ||
        strcmp(cat__g.cjk_font_rel, rel) != 0) {
        for (int i = 0; i < CAT_FONT_TIER_COUNT; i++) {
            if (cat__g.cjk_fonts[i]) {
                TTF_CloseFont(cat__g.cjk_fonts[i]);
                cat__g.cjk_fonts[i] = NULL;
            }
        }
        cat__text_cache_clear();
        cat__str_copy(cat__g.cjk_font_rel, sizeof(cat__g.cjk_font_rel), rel);
        cat__g.cjk_font_bump = cat__g.font_bump;
        cat__g.cjk_font_missing = false;
    }
    if (cat__g.cjk_font_missing) return base;

    int tier = -1;
    for (int i = 0; i < CAT_FONT_TIER_COUNT; i++) {
        if (cat__g.fonts[i] == base) { tier = i; break; }
    }
    if (tier < 0) return base;

    if (!cat__g.cjk_fonts[tier]) {
        char path[PATH_MAX];
        if (!cat__resolve_cjk_font_path(rel, path, sizeof(path))) {
            cat__g.cjk_font_missing = true;   /* warn once by omission, never per frame */
            return base;
        }
        int size = cat_font_size_for_resolution(cat__font_base_sizes[tier] + cat__g.font_bump);
        if (size < 8) size = 8;
        cat__g.cjk_fonts[tier] = cat__open_font(path, size);
        if (!cat__g.cjk_fonts[tier]) { cat__g.cjk_font_missing = true; return base; }
        /* Undo the synthetic bold every other font gets. Emboldening is an
           algorithmic smear, and a Han glyph already spends its em box on
           strokes -- thickening them closes the counters and the character
           turns into a blob at the sizes this runs at. Latin has room to
           absorb it; CJK does not. Native speakers reported the result reading
           too heavy, which is the same finding from the other direction. */
        TTF_SetFontStyle(cat__g.cjk_fonts[tier], TTF_STYLE_NORMAL);
    }
    return cat__g.cjk_fonts[tier];
}

/* The single substitution point. Cheap for the common case: an ASCII-only
   string exits cat__text_has_cjk on the fast path without touching the
   stylesheet or the font cache. */
static TTF_Font *cat__font_for_text(TTF_Font *font, const char *text) {
    if (!text || !text[0] || !cat__text_has_cjk(text)) return font;
    return cat__cjk_font_for(font);
}

int cat_get_font_bump(void) {
    return cat__g.font_bump;
}

int cat_set_font_bump(int bump) {
    if (bump < 0) bump = 0;
    if (bump > CAT_FONT_BUMP_MAX) bump = CAT_FONT_BUMP_MAX;
    if (cat__g.font_bump == bump && cat__g.fonts[CAT_FONT_SMALL])
        return CAT_OK;
    cat__g.font_bump = bump;
    return cat__load_fonts(cat__g.theme.font_path[0] ? cat__g.theme.font_path : NULL);
}

/* ─── Input System ───────────────────────────────────────────────────────── */

/* Map SDL joystick button to virtual button (raw joystick — used on TrimUI) */
#if !defined(PLATFORM_MY355)
static cat_button cat__map_joy_button(uint8_t btn) {
#if defined(PLATFORM_MLP1)
    switch (btn) {
        case CAT__MLP1_BTN_A:      return CAT_BTN_A;
        case CAT__MLP1_BTN_B:      return CAT_BTN_B;
        case CAT__MLP1_BTN_X:      return CAT_BTN_X;
        case CAT__MLP1_BTN_Y:      return CAT_BTN_Y;
        case CAT__MLP1_BTN_L1:     return CAT_BTN_L1;
        case CAT__MLP1_BTN_R1:     return CAT_BTN_R1;
        case CAT__MLP1_BTN_L2:     return CAT_BTN_L2;
        case CAT__MLP1_BTN_R2:     return CAT_BTN_R2;
        case CAT__MLP1_BTN_SELECT: return CAT_BTN_SELECT;
        case CAT__MLP1_BTN_START:  return CAT_BTN_START;
        case CAT__MLP1_BTN_MENU:   return CAT_BTN_MENU;
        case CAT__MLP1_BTN_STICK:  return CAT_BTN_STICK;
        default:
            cat_log("MLP1 input: unmapped joystick button=%u", (unsigned)btn);
            return CAT_BTN_NONE;
    }
#else
    if (cat__g.face_buttons_flipped) {
        if (btn == CAT__JOY_BTN_A) return CAT_BTN_B;
        if (btn == CAT__JOY_BTN_B) return CAT_BTN_A;
    }
    switch (btn) {
        case CAT__JOY_BTN_A:      return CAT_BTN_A;
        case CAT__JOY_BTN_B:      return CAT_BTN_B;
        case CAT__JOY_BTN_X:      return CAT_BTN_X;
        case CAT__JOY_BTN_Y:      return CAT_BTN_Y;
        case CAT__JOY_BTN_L1:     return CAT_BTN_L1;
        case CAT__JOY_BTN_R1:     return CAT_BTN_R1;
        case CAT__JOY_BTN_L2:     return CAT_BTN_L2;
        case CAT__JOY_BTN_R2:     return CAT_BTN_R2;
        case CAT__JOY_BTN_SELECT: return CAT_BTN_SELECT;
        case CAT__JOY_BTN_START:  return CAT_BTN_START;
        case CAT__JOY_BTN_MENU:   return CAT_BTN_MENU;
        default:                 return CAT_BTN_NONE;
    }
#endif
}
#endif

/* Map SDL GameController button to virtual button (used on macOS and for any
 * SDL-recognised external controller, e.g. Bluetooth pads on MLP1) */
static cat_button cat__map_controller_button(uint8_t btn) {
    cat_button mapped = CAT_BTN_NONE;
    switch (btn) {
        case SDL_CONTROLLER_BUTTON_A:             mapped = CAT_BTN_A;      break;
        case SDL_CONTROLLER_BUTTON_B:             mapped = CAT_BTN_B;      break;
        case SDL_CONTROLLER_BUTTON_X:             mapped = CAT_BTN_X;      break;
        case SDL_CONTROLLER_BUTTON_Y:             mapped = CAT_BTN_Y;      break;
        case SDL_CONTROLLER_BUTTON_LEFTSHOULDER:  mapped = CAT_BTN_L1;     break;
        case SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: mapped = CAT_BTN_R1;     break;
        case SDL_CONTROLLER_BUTTON_BACK:          mapped = CAT_BTN_SELECT; break;
        case SDL_CONTROLLER_BUTTON_START:         mapped = CAT_BTN_START;  break;
        case SDL_CONTROLLER_BUTTON_GUIDE:         mapped = CAT_BTN_MENU;   break;
        case SDL_CONTROLLER_BUTTON_LEFTSTICK:     mapped = CAT_BTN_STICK;  break;
        case SDL_CONTROLLER_BUTTON_DPAD_UP:       mapped = CAT_BTN_UP;     break;
        case SDL_CONTROLLER_BUTTON_DPAD_DOWN:     mapped = CAT_BTN_DOWN;   break;
        case SDL_CONTROLLER_BUTTON_DPAD_LEFT:     mapped = CAT_BTN_LEFT;   break;
        case SDL_CONTROLLER_BUTTON_DPAD_RIGHT:    mapped = CAT_BTN_RIGHT;  break;
        default: break;
    }
    /* Apply face-button flip (Nintendo-style A↔B, X↔Y) */
    if (cat__g.face_buttons_flipped) {
        if (mapped == CAT_BTN_A) return CAT_BTN_B;
        if (mapped == CAT_BTN_B) return CAT_BTN_A;
        if (mapped == CAT_BTN_X) return CAT_BTN_Y;
        if (mapped == CAT_BTN_Y) return CAT_BTN_X;
    }
    return mapped;
}

/* Map SDL keyboard to virtual button.
 * On my355 we match by scancode (the Flip sends buttons as keyboard HID scancodes).
 * On all other platforms we match by keycode for developer convenience. */
#if defined(PLATFORM_MY355)
static cat_button cat__map_key_event(SDL_KeyboardEvent *kev) {
    uint8_t sc = (uint8_t)kev->keysym.scancode;
    cat_button mapped = CAT_BTN_NONE;
    switch (sc) {
        case CAT__MY355_CODE_UP:     mapped = CAT_BTN_UP;     break;
        case CAT__MY355_CODE_DOWN:   mapped = CAT_BTN_DOWN;   break;
        case CAT__MY355_CODE_LEFT:   mapped = CAT_BTN_LEFT;   break;
        case CAT__MY355_CODE_RIGHT:  mapped = CAT_BTN_RIGHT;  break;
        case CAT__MY355_CODE_A:      mapped = CAT_BTN_A;      break;
        case CAT__MY355_CODE_B:      mapped = CAT_BTN_B;      break;
        case CAT__MY355_CODE_X:      mapped = CAT_BTN_X;      break;
        case CAT__MY355_CODE_Y:      mapped = CAT_BTN_Y;      break;
        case CAT__MY355_CODE_L1:     mapped = CAT_BTN_L1;     break;
        case CAT__MY355_CODE_R1:     mapped = CAT_BTN_R1;     break;
        case CAT__MY355_CODE_L2:     mapped = CAT_BTN_L2;     break;
        case CAT__MY355_CODE_R2:     mapped = CAT_BTN_R2;     break;
        case CAT__MY355_CODE_START:  mapped = CAT_BTN_START;  break;
        case CAT__MY355_CODE_SELECT: mapped = CAT_BTN_SELECT; break;
        case CAT__MY355_CODE_MENU:   mapped = CAT_BTN_MENU;   break;
        case CAT__MY355_CODE_POWER:  mapped = CAT_BTN_POWER;  break;
        default: break;
    }
    if (cat__g.face_buttons_flipped) {
        if (mapped == CAT_BTN_A) return CAT_BTN_B;
        if (mapped == CAT_BTN_B) return CAT_BTN_A;
        if (mapped == CAT_BTN_X) return CAT_BTN_Y;
        if (mapped == CAT_BTN_Y) return CAT_BTN_X;
    }
    return mapped;
}
#else
static cat_button cat__map_key_event(SDL_KeyboardEvent *kev) {
    /* Match Gabagool DefaultInputMapping() — letter keys for face buttons */
    cat_button mapped = CAT_BTN_NONE;
    switch (kev->keysym.sym) {
        case SDLK_UP:        mapped = CAT_BTN_UP;     break;
        case SDLK_DOWN:      mapped = CAT_BTN_DOWN;   break;
        case SDLK_LEFT:      mapped = CAT_BTN_LEFT;   break;
        case SDLK_RIGHT:     mapped = CAT_BTN_RIGHT;  break;
        case SDLK_a:         mapped = CAT_BTN_A;      break;
        case SDLK_b:         mapped = CAT_BTN_B;      break;
        case SDLK_x:         mapped = CAT_BTN_X;      break;
        case SDLK_y:         mapped = CAT_BTN_Y;      break;
        case SDLK_l:         mapped = CAT_BTN_L1;     break;
        case SDLK_SEMICOLON: mapped = CAT_BTN_L2;     break;
        case SDLK_r:         mapped = CAT_BTN_R1;     break;
        case SDLK_t:         mapped = CAT_BTN_R2;     break;
        case SDLK_RETURN:    mapped = CAT_BTN_START;  break;
        case SDLK_SPACE:     mapped = CAT_BTN_SELECT; break;
        case SDLK_h:         mapped = CAT_BTN_MENU;   break;
#if !CAT_PLATFORM_IS_DEVICE
        case SDLK_q:         mapped = CAT_BTN_QUIT;   break;
#endif
        default: break;
    }
    if (cat__g.face_buttons_flipped) {
        if (mapped == CAT_BTN_A) return CAT_BTN_B;
        if (mapped == CAT_BTN_B) return CAT_BTN_A;
        if (mapped == CAT_BTN_X) return CAT_BTN_Y;
        if (mapped == CAT_BTN_Y) return CAT_BTN_X;
    }
    return mapped;
}
#endif

/* Internal input event buffer. The parallel _ms array records when each event
   arrived, not when it is popped: a long frame drains several frames' worth of
   events in one burst, and debouncing on pop time would collapse their real
   spacing to zero and discard everything after the first. */
static cat_input_event cat__input_queue[64];
static uint32_t cat__input_queue_ms[64];
static int cat__input_head = 0;
static int cat__input_tail = 0;

static void cat__footer_overflow_show_hidden_actions(void);
static bool cat__footer_overflow_consume_open_request(void);

static void cat__footer_overflow_clear_visible_state(void) {
    cat__g.footer_overflow_active = false;
    cat__g.footer_hidden_count = 0;
}

static void cat__footer_overflow_begin_frame(void) {
    if (cat__g.footer_overflow_overlay_open) return;
    cat__footer_overflow_clear_visible_state();
}

static bool cat__footer_overflow_enabled(void) {
    if (!cat__g.footer_overflow_opts.enabled) return false;
    if (cat__g.footer_overflow_overlay_open) return false;
    if (!cat__g.footer_overflow_active || cat__g.footer_hidden_count <= 0) return false;
    if (cat__g.footer_overflow_opts.chord_a == CAT_BTN_NONE ||
        cat__g.footer_overflow_opts.chord_b == CAT_BTN_NONE) {
        return false;
    }
    if (cat__g.footer_overflow_opts.chord_a == cat__g.footer_overflow_opts.chord_b) return false;
    return true;
}

static void cat__input_remove_buttons(cat_button a, cat_button b) {
    cat_input_event filtered[64];
    uint32_t filtered_ms[64];
    int filtered_count = 0;
    int idx = cat__input_tail;

    while (idx != cat__input_head && filtered_count < 64) {
        cat_input_event ev = cat__input_queue[idx];
        uint32_t ev_ms = cat__input_queue_ms[idx];
        idx = (idx + 1) % 64;
        if (!ev.repeated && (ev.button == a || ev.button == b)) continue;
        filtered_ms[filtered_count] = ev_ms;
        filtered[filtered_count++] = ev;
    }

    cat__input_tail = 0;
    cat__input_head = filtered_count % 64;
    for (int i = 0; i < filtered_count; i++) {
        cat__input_queue[i] = filtered[i];
        cat__input_queue_ms[i] = filtered_ms[i];
    }
}

static bool cat__footer_overflow_note_button(cat_button btn, bool pressed) {
    if (btn == CAT_BTN_NONE) return false;

    if (!pressed && cat__g.footer_overflow_swallow[btn]) {
        cat__g.footer_overflow_swallow[btn] = false;
        if (btn == cat__g.footer_overflow_opts.chord_a || btn == cat__g.footer_overflow_opts.chord_b) {
            if (!cat__g.buttons_held[cat__g.footer_overflow_opts.chord_a] &&
                !cat__g.buttons_held[cat__g.footer_overflow_opts.chord_b]) {
                cat__g.footer_overflow_chord_held = false;
            }
        }
        return true;
    }

    if (!pressed) {
        if ((btn == cat__g.footer_overflow_opts.chord_a || btn == cat__g.footer_overflow_opts.chord_b) &&
            !cat__g.buttons_held[cat__g.footer_overflow_opts.chord_a] &&
            !cat__g.buttons_held[cat__g.footer_overflow_opts.chord_b]) {
            cat__g.footer_overflow_chord_held = false;
        }
        return false;
    }

    if (!cat__footer_overflow_enabled() || cat__g.footer_overflow_chord_held) return false;
    if (btn != cat__g.footer_overflow_opts.chord_a && btn != cat__g.footer_overflow_opts.chord_b) return false;

    if (cat__g.buttons_held[cat__g.footer_overflow_opts.chord_a] &&
        cat__g.buttons_held[cat__g.footer_overflow_opts.chord_b]) {
        cat__g.footer_overflow_chord_held = true;
        cat__g.footer_overflow_open_requested = true;
        cat__g.footer_overflow_swallow[cat__g.footer_overflow_opts.chord_a] = true;
        cat__g.footer_overflow_swallow[cat__g.footer_overflow_opts.chord_b] = true;
        cat__input_remove_buttons(cat__g.footer_overflow_opts.chord_a,
                                 cat__g.footer_overflow_opts.chord_b);
        return true;
    }

    return false;
}

/* ─── Combo Detection Helpers ────────────────────────────────────────────── */

static void cat__combo_push_event(cat_combo *c, bool triggered, cat_combo_type type) {
    /* Fire callback if registered */
    if (triggered && c->on_trigger)
        c->on_trigger(c->id, type, c->userdata);
    else if (!triggered && c->on_release)
        c->on_release(c->id, type, c->userdata);

    /* Enqueue for polling */
    int next = (cat__g.combo_queue_head + 1) % 16;
    if (next == cat__g.combo_queue_tail) return; /* queue full */
    cat__g.combo_queue[cat__g.combo_queue_head].id = c->id;
    cat__g.combo_queue[cat__g.combo_queue_head].triggered = triggered;
    cat__g.combo_queue[cat__g.combo_queue_head].type = type;
    cat__g.combo_queue_head = next;
}

static void cat__combo_add_seq_entry(cat_button btn, uint32_t now) {
    if (cat__g.seq_buffer_count >= 20) {
        memmove(&cat__g.seq_buffer[0], &cat__g.seq_buffer[1],
                19 * sizeof(cat__seq_entry));
        cat__g.seq_buffer_count = 19;
    }
    cat__g.seq_buffer[cat__g.seq_buffer_count].button = btn;
    cat__g.seq_buffer[cat__g.seq_buffer_count].time_ms = now;
    cat__g.seq_buffer_count++;
}

static void cat__combo_check_chords(uint32_t now) {
    (void)now;
    for (int i = 0; i < cat__g.combo_count; i++) {
        cat_combo *c = &cat__g.combos[i];
        if (!c->active || c->is_sequence || cat__g.combo_held[i]) continue;

        bool all_pressed = true;
        uint32_t earliest = UINT32_MAX;
        uint32_t latest = 0;

        for (int j = 0; j < c->button_count; j++) {
            cat_button btn = c->buttons[j];
            if (!cat__g.buttons_held[btn]) {
                all_pressed = false;
                break;
            }
            uint32_t t = cat__g.button_press_time[btn];
            if (t < earliest) earliest = t;
            if (t > latest) latest = t;
        }

        if (all_pressed && (latest - earliest) <= c->window_ms) {
            cat__g.combo_held[i] = true;
            cat__combo_push_event(c, true, CAT_COMBO_CHORD);
        }
    }
}

static void cat__combo_check_chord_releases(void) {
    for (int i = 0; i < cat__g.combo_count; i++) {
        cat_combo *c = &cat__g.combos[i];
        if (!c->active || c->is_sequence || !cat__g.combo_held[i]) continue;

        for (int j = 0; j < c->button_count; j++) {
            if (!cat__g.buttons_held[c->buttons[j]]) {
                cat__g.combo_held[i] = false;
                cat__combo_push_event(c, false, CAT_COMBO_CHORD);
                break;
            }
        }
    }
}

static void cat__combo_check_sequences(void) {
    for (int i = 0; i < cat__g.combo_count; i++) {
        cat_combo *c = &cat__g.combos[i];
        if (!c->active || !c->is_sequence) continue;
        if (cat__g.seq_buffer_count < c->button_count) continue;

        int start = cat__g.seq_buffer_count - c->button_count;
        bool match = true;

        for (int j = 0; j < c->button_count; j++) {
            cat__seq_entry *entry = &cat__g.seq_buffer[start + j];
            if (entry->button != c->buttons[j]) {
                match = false;
                break;
            }
            if (j > 0) {
                cat__seq_entry *prev = &cat__g.seq_buffer[start + j - 1];
                if (entry->time_ms - prev->time_ms > c->window_ms) {
                    match = false;
                    break;
                }
            }
        }

        /* Strict mode: reject if an extraneous button was pressed just before
         * the matched range but within the same time window.  Buffer entries
         * are chronological, so checking only the entry immediately before the
         * matched range is sufficient — any earlier entry has an equal or
         * earlier timestamp. */
        if (match && c->strict && start > 0) {
            uint32_t seq_start_time = cat__g.seq_buffer[start].time_ms;
            if (cat__g.seq_buffer[start - 1].time_ms >= seq_start_time) {
                match = false;
            }
        }

        if (match) {
            cat__combo_push_event(c, true, CAT_COMBO_SEQUENCE);
            cat__g.seq_buffer_count = 0;
            return;
        }
    }
}

/* ─── Input Queue ────────────────────────────────────────────────────────── */

static void cat__input_push(cat_button btn, bool pressed) {
    if (btn == CAT_BTN_NONE) return;

    uint32_t now = SDL_GetTicks();
    bool suppress_event = false;

    /* Combo detection — only on real presses/releases, not auto-repeats */
    if (pressed && !cat__g.buttons_held[btn]) {
        cat__g.buttons_held[btn] = true;
        cat__g.button_press_time[btn] = now;
        cat__combo_add_seq_entry(btn, now);
        cat__combo_check_chords(now);
        cat__combo_check_sequences();
    } else if (!pressed && cat__g.buttons_held[btn]) {
        cat__g.buttons_held[btn] = false;
        cat__combo_check_chord_releases();
    }

    suppress_event = cat__footer_overflow_note_button(btn, pressed);
    if (suppress_event) return;

    /* Push to input queue */
    int next = (cat__input_head + 1) % 64;
    if (next == cat__input_tail) return; /* queue full */
    cat__input_queue[cat__input_head] = (cat_input_event){ btn, pressed, false };
    cat__input_queue_ms[cat__input_head] = now;
    cat__input_head = next;
    cat__g.needs_frame = true;
}

static bool cat__is_direction_button(cat_button btn) {
    return btn == CAT_BTN_UP || btn == CAT_BTN_DOWN ||
           btn == CAT_BTN_LEFT || btn == CAT_BTN_RIGHT;
}

static void cat__arm_direction_repeat(cat_button btn, uint32_t now) {
    if (!cat__is_direction_button(btn)) return;
    cat__g.button_repeat_time[btn] = now + cat__g.input_repeat_delay_ms;
}

static void cat__advance_repeat_deadline(uint32_t *deadline, uint32_t now) {
    if (!deadline) return;
    /* Skip missed repeats instead of replaying them in a burst after a slow frame. */
    *deadline = now + cat__g.input_repeat_rate_ms;
}

static void cat__advance_direction_repeat(cat_button btn, uint32_t now) {
    if (!cat__is_direction_button(btn)) return;
    cat__advance_repeat_deadline(&cat__g.button_repeat_time[btn], now);
}

static void cat__clear_direction_repeat(cat_button btn) {
    if (!cat__is_direction_button(btn)) return;
    cat__g.button_repeat_time[btn] = 0;
}

static void cat__push_button_press(cat_button btn, uint32_t now) {
    cat__arm_direction_repeat(btn, now);
    cat__input_push(btn, true);
}

static void cat__push_button_release(cat_button btn) {
    cat__clear_direction_repeat(btn);
    cat__input_push(btn, false);
}

static bool cat__repeat_direction(cat_button btn, uint32_t now) {
    if (!cat__is_direction_button(btn)) return false;
    int next = (cat__input_head + 1) % 64;
    if (next != cat__input_tail) {
        cat__input_queue[cat__input_head] = (cat_input_event){ btn, true, true };
        cat__input_queue_ms[cat__input_head] = now;
        cat__input_head = next;
        cat__g.needs_frame = true;
    }
    cat__advance_direction_repeat(btn, now);
    return true;
}

#if !defined(PLATFORM_MY355)
static void cat__set_hat_state(uint8_t hat, uint32_t now) {
    uint8_t prev = cat__g.hat_held;

    if (!(hat & SDL_HAT_UP) && (prev & SDL_HAT_UP))
        cat__push_button_release(CAT_BTN_UP);
    if (!(hat & SDL_HAT_DOWN) && (prev & SDL_HAT_DOWN))
        cat__push_button_release(CAT_BTN_DOWN);
    if (!(hat & SDL_HAT_LEFT) && (prev & SDL_HAT_LEFT))
        cat__push_button_release(CAT_BTN_LEFT);
    if (!(hat & SDL_HAT_RIGHT) && (prev & SDL_HAT_RIGHT))
        cat__push_button_release(CAT_BTN_RIGHT);

    if ((hat & SDL_HAT_UP) && !(prev & SDL_HAT_UP))
        cat__push_button_press(CAT_BTN_UP, now);
    if ((hat & SDL_HAT_DOWN) && !(prev & SDL_HAT_DOWN))
        cat__push_button_press(CAT_BTN_DOWN, now);
    if ((hat & SDL_HAT_LEFT) && !(prev & SDL_HAT_LEFT))
        cat__push_button_press(CAT_BTN_LEFT, now);
    if ((hat & SDL_HAT_RIGHT) && !(prev & SDL_HAT_RIGHT))
        cat__push_button_press(CAT_BTN_RIGHT, now);

    cat__g.hat_held = hat;
    cat__g.hat_repeat_time = hat ? (now + cat__g.input_repeat_delay_ms) : 0;
}
#endif

static void cat__set_axis_direction_y(int dir, uint32_t now) {
    if (cat__g.axis_held_dir_y == dir) return;

    if (cat__g.axis_held_dir_y == -1) {
        cat__push_button_release(CAT_BTN_UP);
    } else if (cat__g.axis_held_dir_y == 1) {
        cat__push_button_release(CAT_BTN_DOWN);
    }

    cat__g.axis_held_dir_y = dir;
    if (dir < 0) {
        cat__push_button_press(CAT_BTN_UP, now);
        cat__g.axis_repeat_time_y = now + cat__g.input_repeat_delay_ms;
    } else if (dir > 0) {
        cat__push_button_press(CAT_BTN_DOWN, now);
        cat__g.axis_repeat_time_y = now + cat__g.input_repeat_delay_ms;
    } else {
        cat__g.axis_repeat_time_y = 0;
    }
}

static void cat__set_axis_direction_x(int dir, uint32_t now) {
    if (cat__g.axis_held_dir_x == dir) return;

    if (cat__g.axis_held_dir_x == -1) {
        cat__push_button_release(CAT_BTN_LEFT);
    } else if (cat__g.axis_held_dir_x == 1) {
        cat__push_button_release(CAT_BTN_RIGHT);
    }

    cat__g.axis_held_dir_x = dir;
    if (dir < 0) {
        cat__push_button_press(CAT_BTN_LEFT, now);
        cat__g.axis_repeat_time_x = now + cat__g.input_repeat_delay_ms;
    } else if (dir > 0) {
        cat__push_button_press(CAT_BTN_RIGHT, now);
        cat__g.axis_repeat_time_x = now + cat__g.input_repeat_delay_ms;
    } else {
        cat__g.axis_repeat_time_x = 0;
    }
}

/* ─── Pad registry + hotplug ───────────────────────────────────────────────
 * Tracks every opened joystick/controller by SDL instance id so any connected
 * pad can drive the focused view. Pads SDL recognises as game controllers
 * (SDL_IsGameController, e.g. Bluetooth Xbox pads) are opened via
 * SDL_GameControllerOpen and use the canonical mapping; the built-in pad and
 * Jawaka's virtual "Loong Gamepad" clone stay on the raw platform map even
 * though SDL recognises them, because SDL's mapping for that name reports the
 * face buttons swapped relative to the hardware. */

static int cat__pad_find(SDL_JoystickID id) {
    for (int i = 0; i < CAT__MAX_PADS; i++) {
        if (cat__g.pads[i].id == id) return i;
    }
    return -1;
}

/* True only for pads driven through the GameController API, whose input
 * already arrives as canonical CONTROLLERBUTTON/AXIS events — the duplicate
 * raw joystick events must be dropped. Pads left on the platform map are
 * registered too, so testing mere registration would silently swallow every
 * button the built-in pad sends. */
static bool cat__pad_is_controller(SDL_JoystickID id) CAT__MAYBE_UNUSED;
static bool cat__pad_is_controller(SDL_JoystickID id) {
    int i = cat__pad_find(id);
    return i >= 0 && cat__g.pads[i].gc != NULL;
}

/* The built-in pad and Jawaka's calibrated uinput clone of it share this
 * name. SDL carries a built-in controller mapping for it, but that mapping
 * disagrees with the hardware: the device reports Xbox-style button indices
 * while its face buttons sit in the Nintendo positions (A is East). The
 * platform raw map already encodes that layout, so this pad stays off the
 * GameController path no matter what SDL_IsGameController claims — otherwise
 * A and B come back swapped throughout the UI. */
#define CAT__BUILTIN_PAD_NAME "Loong Gamepad"

static bool cat__pad_prefers_raw_map(int device_index) {
    const char *name = SDL_JoystickNameForIndex(device_index);
    return name && SDL_strcmp(name, CAT__BUILTIN_PAD_NAME) == 0;
}

/* Register an SDL joystick device (device index, not instance id). */
static void cat__pad_add_index(int device_index) CAT__MAYBE_UNUSED;
static void cat__pad_add_index(int device_index) {
    SDL_JoystickID existing = SDL_JoystickGetDeviceInstanceID(device_index);
    if (existing >= 0 && cat__pad_find(existing) >= 0) return;
    for (int i = 0; i < CAT__MAX_PADS; i++) {
        if (cat__g.pads[i].id != -1) continue;
        cat__pad *p = &cat__g.pads[i];
        if (SDL_IsGameController(device_index) &&
            !cat__pad_prefers_raw_map(device_index)) {
            p->gc = SDL_GameControllerOpen(device_index);
            if (!p->gc) continue; /* SDL may reject between enumerate and open */
            p->joy = SDL_GameControllerGetJoystick(p->gc);
            p->id  = SDL_JoystickInstanceID(p->joy);
            cat_log("Game controller opened: %s",
                    SDL_GameControllerName(p->gc) ? SDL_GameControllerName(p->gc) : "?");
        } else {
            p->joy = SDL_JoystickOpen(device_index);
            if (!p->joy) continue;
            p->gc  = NULL;
            p->id  = SDL_JoystickInstanceID(p->joy);
            cat_log("Joystick opened: %s",
                    SDL_JoystickName(p->joy) ? SDL_JoystickName(p->joy) : "?");
        }
        if (p->id < 0) { /* should not happen; undo partial slot */
            if (p->gc) SDL_GameControllerClose(p->gc);
            else if (p->joy) SDL_JoystickClose(p->joy);
            p->id = -1; p->gc = NULL; p->joy = NULL;
            continue;
        }
        cat__g.pad_count++;
        /* Legacy single-device fields: first controller / first raw joystick. */
        if (p->gc && !cat__g.controller) {
            cat__g.controller = p->gc;
            cat__g.joystick   = p->joy;
        } else if (!p->gc && !cat__g.joystick) {
            cat__g.joystick = p->joy;
        }
        cat__g.wake_dirty = true;
        return;
    }
}

static void cat__pad_remove(SDL_JoystickID id) CAT__MAYBE_UNUSED;
static void cat__pad_remove(SDL_JoystickID id) {
    int i = cat__pad_find(id);
    if (i < 0) return;
    cat__pad *p = &cat__g.pads[i];
    cat_log("Input device removed: %s", p->gc
        ? (SDL_GameControllerName(p->gc) ? SDL_GameControllerName(p->gc) : "?")
        : (p->joy && SDL_JoystickName(p->joy) ? SDL_JoystickName(p->joy) : "?"));
    if (p->gc) {
        SDL_GameControllerClose(p->gc);
        cat__g.controller = NULL;
        cat__g.joystick   = NULL;
        /* Don't leave a stick-hold lingering from the disconnected pad. */
        cat__g.axis_stick_lx = cat__g.axis_stick_ly = 0;
        cat__g.axis_stick_rx = cat__g.axis_stick_ry = 0;
    } else if (p->joy) {
        SDL_JoystickClose(p->joy);
        cat__g.joystick = NULL;
    }
    p->id = -1; p->gc = NULL; p->joy = NULL;
    cat__g.pad_count--;
    /* Repoint legacy fields at remaining devices. */
    for (int k = 0; k < CAT__MAX_PADS; k++) {
        if (cat__g.pads[k].id == -1) continue;
        if (cat__g.pads[k].gc && !cat__g.controller) {
            cat__g.controller = cat__g.pads[k].gc;
            cat__g.joystick   = cat__g.pads[k].joy;
        }
        if (!cat__g.pads[k].gc && !cat__g.joystick) {
            cat__g.joystick = cat__g.pads[k].joy;
        }
    }
    cat__g.wake_dirty = true;
}

static void cat__handle_sdl_event(SDL_Event *ev, uint32_t now) {
    switch (ev->type) {
        case SDL_QUIT:
            cat__input_push(CAT_BTN_B, true);
            break;

        case SDL_USEREVENT:
            break; /* wake-up only — no action needed */

        case SDL_KEYDOWN:
            if (!ev->key.repeat) {
                cat_button b = cat__map_key_event(&ev->key);
                cat__push_button_press(b, now);
            }
            break;

        case SDL_KEYUP: {
            cat_button b = cat__map_key_event(&ev->key);
            cat__push_button_release(b);
            break;
        }

        /* --- Raw Joystick button/hat events (TrimUI devices and any pad SDL
           does not recognise as a game controller) ---
           MY355 sends buttons and d-pad as keyboard scancodes; processing
           joystick button/hat events too would cause double-input.
           For devices opened as GameControllers, skip the raw joystick
           button/hat/axis events — the GameController API already maps them
           canonically, and the raw mappings differ, causing phantom inputs. */
        #if !defined(PLATFORM_MY355)
        case SDL_JOYBUTTONDOWN: {
            if (cat__pad_is_controller(ev->jbutton.which)) break; /* GameController handles this */
            cat_button b = cat__map_joy_button(ev->jbutton.button);
            cat__push_button_press(b, now);
            break;
        }

        case SDL_JOYBUTTONUP: {
            if (cat__pad_is_controller(ev->jbutton.which)) break; /* GameController handles this */
            cat_button b = cat__map_joy_button(ev->jbutton.button);
            cat__push_button_release(b);
            break;
        }

        /* --- SDL GameController events (macOS / recognised controllers) --- */
        case SDL_CONTROLLERDEVICEADDED:
            cat__pad_add_index(ev->cdevice.which); /* which = device index */
            break;

        case SDL_CONTROLLERDEVICEREMOVED:
            cat__pad_remove(ev->cdevice.which);    /* which = instance id */
            break;

        case SDL_CONTROLLERBUTTONDOWN: {
            cat_button b = cat__map_controller_button(ev->cbutton.button);
            cat__push_button_press(b, now);
            break;
        }

        case SDL_CONTROLLERBUTTONUP: {
            cat_button b = cat__map_controller_button(ev->cbutton.button);
            cat__push_button_release(b);
            break;
        }

        case SDL_CONTROLLERAXISMOTION: {
            /* Sticks drive directional input past CAT_AXIS_DEADZONE. Track the
               last canonical positions of both sticks and arbitrate on every
               event: the left stick wins, the right stick drives only while the
               left sits inside the deadzone — no phantoms, no stuck holds. */
            if (ev->caxis.axis == SDL_CONTROLLER_AXIS_LEFTX ||
                ev->caxis.axis == SDL_CONTROLLER_AXIS_RIGHTX) {
                if (ev->caxis.axis == SDL_CONTROLLER_AXIS_LEFTX) {
                    cat__g.axis_stick_lx = ev->caxis.value;
                } else {
                    cat__g.axis_stick_rx = ev->caxis.value;
                }
                int x = cat__g.axis_stick_rx;
                if (cat__g.axis_stick_lx < -CAT_AXIS_DEADZONE ||
                    cat__g.axis_stick_lx >  CAT_AXIS_DEADZONE) {
                    x = cat__g.axis_stick_lx; /* left stick wins */
                }
                if (x < -CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_x(-1, now);
                } else if (x > CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_x(1, now);
                } else {
                    cat__set_axis_direction_x(0, now);
                }
            } else if (ev->caxis.axis == SDL_CONTROLLER_AXIS_LEFTY ||
                       ev->caxis.axis == SDL_CONTROLLER_AXIS_RIGHTY) {
                if (ev->caxis.axis == SDL_CONTROLLER_AXIS_LEFTY) {
                    cat__g.axis_stick_ly = ev->caxis.value;
                } else {
                    cat__g.axis_stick_ry = ev->caxis.value;
                }
                int y = cat__g.axis_stick_ry;
                if (cat__g.axis_stick_ly < -CAT_AXIS_DEADZONE ||
                    cat__g.axis_stick_ly >  CAT_AXIS_DEADZONE) {
                    y = cat__g.axis_stick_ly; /* left stick wins */
                }
                if (y < -CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_y(-1, now);
                } else if (y > CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_y(1, now);
                } else {
                    cat__set_axis_direction_y(0, now);
                }
            } else if (ev->caxis.axis == SDL_CONTROLLER_AXIS_TRIGGERLEFT) {
                if (ev->caxis.value > CAT_AXIS_DEADZONE) {
                    if (!cat__g.buttons_held[CAT_BTN_L2]) {
                        cat__input_push(CAT_BTN_L2, true);
                    }
                } else if (cat__g.buttons_held[CAT_BTN_L2]) {
                    cat__input_push(CAT_BTN_L2, false);
                }
            } else if (ev->caxis.axis == SDL_CONTROLLER_AXIS_TRIGGERRIGHT) {
                if (ev->caxis.value > CAT_AXIS_DEADZONE) {
                    if (!cat__g.buttons_held[CAT_BTN_R2]) {
                        cat__input_push(CAT_BTN_R2, true);
                    }
                } else if (cat__g.buttons_held[CAT_BTN_R2]) {
                    cat__input_push(CAT_BTN_R2, false);
                }
            }
            break;
        }

        case SDL_JOYHATMOTION: {
            if (cat__pad_is_controller(ev->jhat.which)) break; /* GameController handles d-pad */
            cat__set_hat_state(ev->jhat.value, now);
            break;
        }

        case SDL_JOYDEVICEADDED:
            /* Controller-recognised pads are opened by CONTROLLERDEVICEADDED
             * (fired right after); open everything else as a raw joystick so
             * the existing platform map keeps working for it. The built-in pad
             * is claimed here even though SDL recognises it, because it stays
             * on the platform map either way and registering twice is a no-op. */
            if (!SDL_IsGameController(ev->jdevice.which) ||
                cat__pad_prefers_raw_map(ev->jdevice.which)) {
                cat__pad_add_index(ev->jdevice.which);
            }
            break;

        case SDL_JOYDEVICEREMOVED:
            /* Pads opened as controllers are closed by CONTROLLERDEVICEREMOVED. */
            {
                int pi = cat__pad_find(ev->jdevice.which);
                if (pi >= 0 && !cat__g.pads[pi].gc) {
                    cat__pad_remove(ev->jdevice.which);
                }
            }
            break;
        #endif /* !PLATFORM_MY355 */

        /* --- Analog stick axis events (all device platforms) ---
           Thumbstick generates SDL_JOYAXISMOTION on all devices including
           MY355, so this must remain outside the MY355 exclusion guard. */
        case SDL_JOYAXISMOTION: {
            if (cat__pad_is_controller(ev->jaxis.which)) break; /* GameController handles axes */
            if (ev->jaxis.axis == 1) { /* Y axis (up/down) */
                if (ev->jaxis.value < -CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_y(-1, now);
                } else if (ev->jaxis.value > CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_y(1, now);
                } else {
                    cat__set_axis_direction_y(0, now);
                }
            } else if (ev->jaxis.axis == 0) { /* X axis (left/right) */
                if (ev->jaxis.value < -CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_x(-1, now);
                } else if (ev->jaxis.value > CAT_AXIS_DEADZONE) {
                    cat__set_axis_direction_x(1, now);
                } else {
                    cat__set_axis_direction_x(0, now);
                }
            }
            #if CAT_PLATFORM_IS_DEVICE
            /* L2/R2 analog triggers (TrimUI TG5040/TG5050 send triggers as
             * axes 2/5 rather than joystick buttons). Use val > 0 threshold
             * for natural trigger handling. The buttons_held guard in
             * cat__input_push prevents duplicate events if the platform also
             * sends joystick button events for these triggers. */
            else if (ev->jaxis.axis == CAT__JOY_AXIS_L2) {
                if (ev->jaxis.value > 0) {
                    if (!cat__g.buttons_held[CAT_BTN_L2])
                        cat__input_push(CAT_BTN_L2, true);
                } else if (cat__g.buttons_held[CAT_BTN_L2]) {
                    cat__input_push(CAT_BTN_L2, false);
                }
            } else if (ev->jaxis.axis == CAT__JOY_AXIS_R2) {
                if (ev->jaxis.value > 0) {
                    if (!cat__g.buttons_held[CAT_BTN_R2])
                        cat__input_push(CAT_BTN_R2, true);
                } else if (cat__g.buttons_held[CAT_BTN_R2]) {
                    cat__input_push(CAT_BTN_R2, false);
                }
            }
            #endif
            break;
        }
    }
}

static void cat__process_sdl_events(void) {
    if (!cat__g.input_backend_ready) {
        cat__input_backend_init();
    }

    uint32_t now = SDL_GetTicks();

    SDL_Event ev;
    while (SDL_PollEvent(&ev)) {
        cat__handle_sdl_event(&ev, now);
    }

    bool repeated_up = false;
    bool repeated_down = false;
    bool repeated_left = false;
    bool repeated_right = false;

    /* Directional hold repeat — digital buttons (keyboard/D-pad/button maps) */
    if (cat__g.buttons_held[CAT_BTN_UP] && now >= cat__g.button_repeat_time[CAT_BTN_UP]) {
        repeated_up = cat__repeat_direction(CAT_BTN_UP, now);
    }
    if (cat__g.buttons_held[CAT_BTN_DOWN] && now >= cat__g.button_repeat_time[CAT_BTN_DOWN]) {
        repeated_down = cat__repeat_direction(CAT_BTN_DOWN, now);
    }
    if (cat__g.buttons_held[CAT_BTN_LEFT] && now >= cat__g.button_repeat_time[CAT_BTN_LEFT]) {
        repeated_left = cat__repeat_direction(CAT_BTN_LEFT, now);
    }
    if (cat__g.buttons_held[CAT_BTN_RIGHT] && now >= cat__g.button_repeat_time[CAT_BTN_RIGHT]) {
        repeated_right = cat__repeat_direction(CAT_BTN_RIGHT, now);
    }

    /* Directional hold repeat — hat */
    if (cat__g.hat_held && now >= cat__g.hat_repeat_time) {
        if ((cat__g.hat_held & SDL_HAT_UP) && !repeated_up) repeated_up = cat__repeat_direction(CAT_BTN_UP, now);
        if ((cat__g.hat_held & SDL_HAT_DOWN) && !repeated_down) repeated_down = cat__repeat_direction(CAT_BTN_DOWN, now);
        if ((cat__g.hat_held & SDL_HAT_LEFT) && !repeated_left) repeated_left = cat__repeat_direction(CAT_BTN_LEFT, now);
        if ((cat__g.hat_held & SDL_HAT_RIGHT) && !repeated_right) repeated_right = cat__repeat_direction(CAT_BTN_RIGHT, now);
        cat__advance_repeat_deadline(&cat__g.hat_repeat_time, now);
    }

    /* Directional hold repeat — analog Y */
    if (cat__g.axis_held_dir_y && now >= cat__g.axis_repeat_time_y) {
        if (cat__g.axis_held_dir_y < 0) {
            if (!repeated_up) repeated_up = cat__repeat_direction(CAT_BTN_UP, now);
        } else {
            if (!repeated_down) repeated_down = cat__repeat_direction(CAT_BTN_DOWN, now);
        }
        cat__advance_repeat_deadline(&cat__g.axis_repeat_time_y, now);
    }

    /* Directional hold repeat — analog X */
    if (cat__g.axis_held_dir_x && now >= cat__g.axis_repeat_time_x) {
        if (cat__g.axis_held_dir_x < 0) {
            if (!repeated_left) repeated_left = cat__repeat_direction(CAT_BTN_LEFT, now);
        } else {
            if (!repeated_right) repeated_right = cat__repeat_direction(CAT_BTN_RIGHT, now);
        }
        cat__advance_repeat_deadline(&cat__g.axis_repeat_time_x, now);
    }

    /* Shoulder hold repeat — opt-in (cat_set_shoulder_repeat). Off by default so
       L1/R1 stay single-shot everywhere else; the coverflow carousel turns it on
       so a held shoulder keeps flowing. Self-arms on the first held frame. */
    if (cat__g.shoulder_repeat) {
        const cat_button sh[2] = { CAT_BTN_L1, CAT_BTN_R1 };
        for (int k = 0; k < 2; k++) {
            cat_button b = sh[k];
            if (!cat__g.buttons_held[b]) { cat__g.button_repeat_time[b] = 0; continue; }
            if (cat__g.button_repeat_time[b] == 0) {
                cat__g.button_repeat_time[b] = now + cat__g.input_repeat_delay_ms;
            } else if (now >= cat__g.button_repeat_time[b]) {
                int next = (cat__input_head + 1) % 64;
                if (next != cat__input_tail) {
                    cat__input_queue[cat__input_head] = (cat_input_event){ b, true, true };
                    cat__input_queue_ms[cat__input_head] = now;
                    cat__input_head = next;
                    cat__g.needs_frame = true;
                }
                cat__advance_repeat_deadline(&cat__g.button_repeat_time[b], now);
            }
        }
    }
}

bool cat_poll_input(cat_input_event *event) {
    while (1) {
        /* Process SDL events into our queue */
        cat__process_sdl_events();

        if (cat__footer_overflow_consume_open_request()) continue;

        /* Pop from internal queue */
        if (cat__input_head == cat__input_tail) return false;

        uint32_t ev_ms = cat__input_queue_ms[cat__input_tail];
        *event = cat__input_queue[cat__input_tail];
        cat__input_tail = (cat__input_tail + 1) % 64;

        /* Debounce: skip presses too close together. Measured from when the
           events arrived, so a slow frame that queues several of them costs
           latency and not input — comparing against SDL_GetTicks() here would
           give every event in one drain the same timestamp and throw away all
           but the first. Releases neither gate nor arm the window: callers
           discard them, and letting one hold the budget means the next press,
           arriving a handful of ms later, is the one that disappears. */
        if (event->pressed) {
            /* Signed delta: an event stamped at or before the last accepted one
               (the footer-overflow chord parks last_input_time at pop time to
               swallow what the chord queued) is inside the window, not a
               wrapped-around eternity ago. */
            int32_t since = (int32_t)(ev_ms - cat__g.last_input_time);
            if (cat__g.input_delay_ms > 0 && since < (int32_t)cat__g.input_delay_ms) {
                continue;
            }
            cat__g.last_input_time = ev_ms;
        }

        return true;
    }
}

void cat_set_input_delay(uint32_t ms) {
    cat__g.input_delay_ms = ms;
}

void cat_set_input_repeat(uint32_t delay_ms, uint32_t rate_ms) {
    cat__g.input_repeat_delay_ms = delay_ms;
    cat__g.input_repeat_rate_ms = rate_ms;
}

void cat_set_shoulder_repeat(bool enabled) {
    cat__g.shoulder_repeat = enabled;
    if (!enabled) {
        /* Clear any armed L1/R1 repeat deadlines. The self-arming block above
           only runs while shoulder_repeat is on, so a deadline left set on a
           still-held shoulder would keep cat__next_wake_time() waking at (or
           past) it — a 0-timeout idle spin until release. */
        cat__g.button_repeat_time[CAT_BTN_L1] = 0;
        cat__g.button_repeat_time[CAT_BTN_R1] = 0;
    }
}

void cat_flip_face_buttons(bool flip) {
    cat__g.face_buttons_flipped = flip;
}

const char *cat_button_name(cat_button btn) {
    if (btn < 0 || btn >= CAT_BTN_COUNT) return "Unknown";
    return cat__button_names[btn];
}

static size_t cat__utf8_codepoint_count(const char *text) {
    size_t count = 0;
    if (!text) return 0;
    while (*text) {
        if ((((unsigned char)*text) & 0xC0u) != 0x80u) count++;
        text++;
    }
    return count;
}

/* ─── Combo System ───────────────────────────────────────────────────────── */

static int cat__register_combo(const char *id, cat_button *buttons, int count,
                              uint32_t timing_ms, bool is_sequence, bool strict,
                              cat_combo_callback on_trigger,
                              cat_combo_callback on_release,
                              void *userdata) {
    if (!id || !id[0] || !buttons) return CAT_ERROR;
    if (count < 1 || count > 8) return CAT_ERROR;
    if (cat__g.combo_count >= CAT_MAX_COMBOS) return CAT_ERROR;

    cat_combo *c = &cat__g.combos[cat__g.combo_count++];
    size_t button_bytes = (size_t)count * sizeof(cat_button);
    memset(c, 0, sizeof(*c));
    strncpy(c->id, id, sizeof(c->id) - 1);
    memcpy(c->buttons, buttons, button_bytes);
    c->button_count = count;
    c->window_ms = timing_ms > 0 ? timing_ms : (is_sequence ? 500 : 100);
    c->is_sequence = is_sequence;
    c->strict = strict;
    c->on_trigger = on_trigger;
    c->on_release = on_release;
    c->userdata = userdata;
    c->active = true;
    return CAT_OK;
}

int cat_register_chord(const char *id, cat_button *buttons, int count, uint32_t window_ms) {
    return cat__register_combo(id, buttons, count, window_ms, false, false, NULL, NULL, NULL);
}

int cat_register_sequence(const char *id, cat_button *buttons, int count, uint32_t timeout_ms, bool strict) {
    return cat__register_combo(id, buttons, count, timeout_ms, true, strict, NULL, NULL, NULL);
}

int cat_register_chord_ex(const char *id, cat_button *buttons, int count,
                         uint32_t window_ms,
                         cat_combo_callback on_trigger,
                         cat_combo_callback on_release,
                         void *userdata) {
    return cat__register_combo(id, buttons, count, window_ms, false, false,
                              on_trigger, on_release, userdata);
}

int cat_register_sequence_ex(const char *id, cat_button *buttons, int count,
                            uint32_t timeout_ms, bool strict,
                            cat_combo_callback on_trigger,
                            void *userdata) {
    return cat__register_combo(id, buttons, count, timeout_ms, true, strict,
                              on_trigger, NULL, userdata);
}

void cat_unregister_combo(const char *id) {
    for (int i = 0; i < cat__g.combo_count; i++) {
        if (strcmp(cat__g.combos[i].id, id) == 0) {
            cat__g.combos[i].active = false;
            break;
        }
    }
}

void cat_clear_combos(void) {
    cat__g.combo_count = 0;
    cat__g.seq_buffer_count = 0;
    memset(cat__g.combo_held, 0, sizeof(cat__g.combo_held));
    cat__g.combo_queue_head = 0;
    cat__g.combo_queue_tail = 0;
}

bool cat_poll_combo(cat_combo_event *event) {
    if (cat__g.combo_queue_head == cat__g.combo_queue_tail) return false;
    *event = cat__g.combo_queue[cat__g.combo_queue_tail];
    cat__g.combo_queue_tail = (cat__g.combo_queue_tail + 1) % 16;
    return true;
}


/* ─── Drawing Primitives ─────────────────────────────────────────────────── */

void cat_clear_screen(void) {
    cat__footer_overflow_begin_frame();
    cat_draw_color bg = cat__g.theme.background;
    SDL_SetRenderDrawColor(cat__g.renderer, bg.r, bg.g, bg.b, bg.a);
    SDL_RenderClear(cat__g.renderer);
}

void cat_request_frame(void) {
    cat__g.needs_frame = true;
}

void cat_set_idle_wake_fd(int fd) {
    cat__g.idle_wake_fd = fd;
}

void cat_request_frame_in(uint32_t ms) {
    uint32_t target = SDL_GetTicks() + ms;
    if (cat__g.next_redraw_ms == 0 || target < cat__g.next_redraw_ms) {
        cat__g.next_redraw_ms = target;
    }
}

static uint32_t cat__next_wake_time(void) {
    uint32_t now = SDL_GetTicks();
    /* Cap idle sleep at the next wall-clock minute boundary: the status-bar
       clock is minute-resolution (%H:%M), so there is no reason to wake every
       second. time() is whole-second, so this lands within ~1s of the true
       boundary — fine for a minute clock, and ~60x fewer idle redraws. */
    time_t t = time(NULL);
    uint32_t to_next_minute = 60000u - (uint32_t)(t % 60) * 1000u;
    uint32_t wake = now + to_next_minute;

    if (cat__g.next_redraw_ms != 0 && cat__g.next_redraw_ms < wake)
        wake = cat__g.next_redraw_ms;

    for (int i = 0; i < CAT_BTN_COUNT; i++) {
        if (cat__g.buttons_held[i] && cat__g.button_repeat_time[i] != 0
            && cat__g.button_repeat_time[i] < wake)
            wake = cat__g.button_repeat_time[i];
    }
    if (cat__g.hat_held && cat__g.hat_repeat_time != 0
        && cat__g.hat_repeat_time < wake)
        wake = cat__g.hat_repeat_time;
    if (cat__g.axis_held_dir_y && cat__g.axis_repeat_time_y != 0
        && cat__g.axis_repeat_time_y < wake)
        wake = cat__g.axis_repeat_time_y;
    if (cat__g.axis_held_dir_x && cat__g.axis_repeat_time_x != 0
        && cat__g.axis_repeat_time_x < wake)
        wake = cat__g.axis_repeat_time_x;

    return wake;
}

#if CAT_PLATFORM_IS_DEVICE
static void cat__wake_rescan(void);
static bool cat__wake_drain(int fd);
#endif

void cat_present(void) {
    /* When a view is being rendered into an offscreen target (e.g. a transition
       snapshot), the caller is capturing pixels, not drawing the live frame —
       don't present or pace. */
    if (SDL_GetRenderTarget(cat__g.renderer) != NULL) return;

    SDL_RenderPresent(cat__g.renderer);

    if (cat__g.needs_frame) {
        /* Active rendering: normal 60fps pacing */
        cat__g.needs_frame = false;
        uint32_t now = SDL_GetTicks();
        uint32_t elapsed = now - cat__g.last_present_ms;
        if (!cat__g.renderer_has_vsync && elapsed < 16) {
            SDL_Delay(16 - elapsed);
        }
    } else {
        /* Idle: sleep until input arrives or the next scheduled redraw.
         * On device we block on the gamepad evdev fd set (internal pad +
         * connected external controllers) so the kernel wakes us instantly on
         * input -> ~zero idle CPU. SDL joysticks expose no fd for SDL to wait
         * on, so SDL_WaitEventTimeout would busy-poll instead. */
        uint32_t wake = cat__next_wake_time();
        uint32_t now = SDL_GetTicks();
        int timeout = (wake > now) ? (int)(wake - now) : 0;
        if (timeout > 0) {
#if CAT_PLATFORM_IS_DEVICE
            /* Periodic wake-set rebind: cheap timestamp check; rescans run at
             * most every CAT__WAKE_RESCAN_MS or right after a hotplug event. */
            if (cat__g.wake_dirty ||
                (int32_t)(SDL_GetTicks() - cat__g.wake_rescan_ms) >= 0) {
                cat__wake_rescan();
            }
            if (cat__g.wake_fd_count > 0 || cat__g.idle_wake_fd >= 0) {
                uint32_t start = SDL_GetTicks();
                int remaining = timeout;
                while (remaining > 0) {
                    struct pollfd pfds[CAT__WAKE_MAX_FDS + 1];
                    for (int i = 0; i < cat__g.wake_fd_count; i++) {
                        pfds[i] = (struct pollfd){ cat__g.wake_fds[i], POLLIN, 0 };
                    }
                    int count = cat__g.wake_fd_count;
                    if (cat__g.idle_wake_fd >= 0)
                        pfds[count++] = (struct pollfd){ cat__g.idle_wake_fd, POLLIN, 0 };
                    /* Never sleep past a rebind opportunity. A controller that
                     * connects while we are blocked here is not in this fd set,
                     * so nothing it sends can wake us -- and idle sleep runs to
                     * the next minute boundary. Without this cap a pad that
                     * reconnects mid-session looks dead for up to a minute,
                     * while an emulator that never idles keeps responding to
                     * it. Waking every few seconds to re-scan is far cheaper
                     * than that. */
                    int slice = remaining < CAT__WAKE_RESCAN_MS
                                    ? remaining : CAT__WAKE_RESCAN_MS;
                    int r = poll(pfds, count, slice);
                    if (r < 0) break; /* error */
                    if (r == 0) {
                        /* Slice expired with no input: pick up any device that
                         * appeared, then wait out the rest of the budget. */
                        if ((int32_t)(SDL_GetTicks() - cat__g.wake_rescan_ms) >= 0)
                            cat__wake_rescan();
                        remaining = timeout - (int)(SDL_GetTicks() - start);
                        continue;
                    }
                    if (cat__g.idle_wake_fd >= 0 &&
                        pfds[count - 1].revents != 0) break;
                    bool woke = false;
                    for (int i = 0; i < cat__g.wake_fd_count; i++) {
                        if (pfds[i].revents & POLLIN) {
                            if (cat__wake_drain(pfds[i].fd)) woke = true;
                        }
                        if (pfds[i].revents & (POLLERR | POLLHUP | POLLNVAL)) {
                            cat__g.wake_dirty = true; /* dead node: rebind next pass */
                        }
                    }
                    /* Sub-deadzone analog noise is drained but not a wake: keep
                     * blocking with the remaining budget so a noisy wireless
                     * stick cannot prevent idle sleep. */
                    if (woke) break;
                    remaining = timeout - (int)(SDL_GetTicks() - start);
                }
            } else {
                SDL_Delay(timeout);
            }
#else
            /* Desktop/preview (Mac): SDL_Delay sleeps the main thread without
             * servicing the Cocoa event loop, so a multi-second idle timeout
             * beachballs the window and defers queued keyboard input until the
             * delay expires. Pump events while waiting and bail the moment one
             * is queued so the next cat_poll_input picks it up; short slices
             * keep input latency low and the window-server happy. */
            uint32_t deadline = now + (uint32_t)timeout;
            for (;;) {
#if defined(__linux__) || defined(__APPLE__)
                if (cat__g.idle_wake_fd >= 0) {
                    struct pollfd pfd = { cat__g.idle_wake_fd, POLLIN, 0 };
                    if (poll(&pfd, 1, 0) != 0) break;
                }
#endif
                SDL_PumpEvents();
                if (SDL_HasEvents(SDL_FIRSTEVENT, SDL_LASTEVENT)) break;
                uint32_t t = SDL_GetTicks();
                if (t >= deadline) break;
                uint32_t slice = deadline - t;
                SDL_Delay(slice > 5 ? 5 : slice);
            }
#endif
        }
        if (cat__g.next_redraw_ms != 0 && SDL_GetTicks() >= cat__g.next_redraw_ms) {
            cat__g.next_redraw_ms = 0;
        }
    }
    cat__g.last_present_ms = SDL_GetTicks();
}

void cat_draw_background(void) {
    cat__footer_overflow_begin_frame();
    if (cat__g.bg_texture) {
        SDL_RenderCopy(cat__g.renderer, cat__g.bg_texture, NULL, NULL);
    } else {
        cat_clear_screen();
    }
}

/* Fill a quarter-circle arc with anti-aliased edges */
static void cat__fill_circle_quadrant(int cx, int cy, int r, int quadrant) {
    SDL_Renderer *rend = cat__g.renderer;
    Uint8 base_r, base_g, base_b, base_a;
    SDL_GetRenderDrawColor(rend, &base_r, &base_g, &base_b, &base_a);

    for (int dy = 0; dy <= r; dy++) {
        float fx = sqrtf((float)(r * r - dy * dy));
        int ix = (int)fx;            /* integer part = fully filled pixels */
        float frac = fx - (float)ix; /* fractional part = edge pixel alpha */
        Uint8 edge_a = (Uint8)(frac * base_a);
        int y0;

        switch (quadrant) {
            case 0: /* top-left */
                y0 = cy - dy;
                SDL_RenderDrawLine(rend, cx - ix, y0, cx, y0);
                if (edge_a > 0) {
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, edge_a);
                    SDL_RenderDrawPoint(rend, cx - ix - 1, y0);
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, base_a);
                }
                break;
            case 1: /* top-right */
                y0 = cy - dy;
                SDL_RenderDrawLine(rend, cx, y0, cx + ix, y0);
                if (edge_a > 0) {
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, edge_a);
                    SDL_RenderDrawPoint(rend, cx + ix + 1, y0);
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, base_a);
                }
                break;
            case 2: /* bottom-left */
                y0 = cy + dy;
                SDL_RenderDrawLine(rend, cx - ix, y0, cx, y0);
                if (edge_a > 0) {
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, edge_a);
                    SDL_RenderDrawPoint(rend, cx - ix - 1, y0);
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, base_a);
                }
                break;
            case 3: /* bottom-right */
                y0 = cy + dy;
                SDL_RenderDrawLine(rend, cx, y0, cx + ix, y0);
                if (edge_a > 0) {
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, edge_a);
                    SDL_RenderDrawPoint(rend, cx + ix + 1, y0);
                    SDL_SetRenderDrawColor(rend, base_r, base_g, base_b, base_a);
                }
                break;
        }
    }
}

void cat_draw_rounded_rect_ex(int x, int y, int w, int h, int r, unsigned corners, cat_draw_color c) {
    SDL_Renderer *rend = cat__g.renderer;
    if (r > h / 2) r = h / 2;
    if (r > w / 2) r = w / 2;
    if (r < 0) r = 0;
    if (corners == 0 || r == 0) { cat_draw_rect(x, y, w, h, c); return; }

    bool round_tl = (corners & CAT_CORNER_TL) != 0;
    bool round_tr = (corners & CAT_CORNER_TR) != 0;
    bool round_bl = (corners & CAT_CORNER_BL) != 0;
    bool round_br = (corners & CAT_CORNER_BR) != 0;

    /* Use quarter-circle sprites from the pill asset for smooth AA whenever
       the assets spritesheet is available.
       The 30×30 white pill at (1,1) in assets@Nx.png is a perfect circle — each
       quadrant scaled to the corner radius via bilinear-filtered SDL_RenderCopy.
       Corners not in the mask are filled square instead. */
    if (cat__g.status_assets) {
        int s = cat__g.status_asset_scale;
        int sx = 1 * s, sy = 1 * s; /* white pill sprite origin */
        int half = 15 * s;           /* half of the 30×30 sprite */

        SDL_SetTextureColorMod(cat__g.status_assets, c.r, c.g, c.b);
        SDL_SetTextureAlphaMod(cat__g.status_assets, c.a);

        if (round_tl) { SDL_Rect s0 = { sx,        sy,        half, half }, d0 = { x,         y,         r, r }; SDL_RenderCopy(rend, cat__g.status_assets, &s0, &d0); }
        if (round_tr) { SDL_Rect s0 = { sx + half, sy,        half, half }, d0 = { x + w - r, y,         r, r }; SDL_RenderCopy(rend, cat__g.status_assets, &s0, &d0); }
        if (round_bl) { SDL_Rect s0 = { sx,        sy + half, half, half }, d0 = { x,         y + h - r, r, r }; SDL_RenderCopy(rend, cat__g.status_assets, &s0, &d0); }
        if (round_br) { SDL_Rect s0 = { sx + half, sy + half, half, half }, d0 = { x + w - r, y + h - r, r, r }; SDL_RenderCopy(rend, cat__g.status_assets, &s0, &d0); }

        SDL_SetRenderDrawColor(rend, c.r, c.g, c.b, c.a);
        /* LoongMate patch: các thanh chồng nhau 1px để không hở vạch khi renderer co giãn toạ độ */
        SDL_Rect top_bar = { x + r - 1, y,         w - 2 * r + 2, r + 1 };
        SDL_Rect mid_bar = { x,     y + r - 1,     w,         h - 2 * r + 2 };
        SDL_Rect bot_bar = { x + r - 1, y + h - r - 1, w - 2 * r + 2, r + 1 };
        SDL_RenderFillRect(rend, &top_bar);
        SDL_RenderFillRect(rend, &mid_bar);
        SDL_RenderFillRect(rend, &bot_bar);
        /* Square corners: fill the r×r corner box the sprite would have rounded. */
        if (!round_tl) { SDL_Rect q = { x,         y,         r, r }; SDL_RenderFillRect(rend, &q); }
        if (!round_tr) { SDL_Rect q = { x + w - r, y,         r, r }; SDL_RenderFillRect(rend, &q); }
        if (!round_bl) { SDL_Rect q = { x,         y + h - r, r, r }; SDL_RenderFillRect(rend, &q); }
        if (!round_br) { SDL_Rect q = { x + w - r, y + h - r, r, r }; SDL_RenderFillRect(rend, &q); }
        return;
    }

    /* Desktop / fallback: procedural anti-aliased corners (square where unmasked). */
    SDL_SetRenderDrawColor(rend, c.r, c.g, c.b, c.a);
    /* LoongMate patch: chồng 1px giữa các thanh (xem trên) */
    SDL_Rect center = {x, y + r - 1, w, h - 2 * r + 2};
    SDL_RenderFillRect(rend, &center);
    SDL_Rect top = {x + r - 1, y, w - 2 * r + 2, r + 1};
    SDL_RenderFillRect(rend, &top);
    SDL_Rect bot = {x + r - 1, y + h - r - 1, w - 2 * r + 2, r + 1};
    SDL_RenderFillRect(rend, &bot);

    if (round_tl) cat__fill_circle_quadrant(x + r - 1, y + r - 1, r, 0); else { SDL_Rect q = { x,         y,         r, r }; SDL_RenderFillRect(rend, &q); }
    if (round_tr) cat__fill_circle_quadrant(x + w - r, y + r - 1, r, 1); else { SDL_Rect q = { x + w - r, y,         r, r }; SDL_RenderFillRect(rend, &q); }
    if (round_bl) cat__fill_circle_quadrant(x + r - 1, y + h - r, r, 2); else { SDL_Rect q = { x,         y + h - r, r, r }; SDL_RenderFillRect(rend, &q); }
    if (round_br) cat__fill_circle_quadrant(x + w - r, y + h - r, r, 3); else { SDL_Rect q = { x + w - r, y + h - r, r, r }; SDL_RenderFillRect(rend, &q); }
}

bool cat_mask_rounded_rect(int x, int y, int w, int h, int r, unsigned corners) {
    SDL_Renderer *rend = cat__g.renderer;
    if (!rend || w <= 0 || h <= 0) return false;
    /* dst.a = src.a * dst.a, dst.rgb untouched. The corner sprites are drawn as
       textures rather than with the draw colour, so the assets texture needs the
       same blend mode or the corners would be copied in opaque. */
    SDL_BlendMode mask = SDL_ComposeCustomBlendMode(
        SDL_BLENDFACTOR_ZERO, SDL_BLENDFACTOR_ONE,       SDL_BLENDOPERATION_ADD,
        SDL_BLENDFACTOR_ZERO, SDL_BLENDFACTOR_SRC_ALPHA, SDL_BLENDOPERATION_ADD);
    SDL_BlendMode saved_draw;
    SDL_GetRenderDrawBlendMode(rend, &saved_draw);
    if (SDL_SetRenderDrawBlendMode(rend, mask) != 0) return false;
    SDL_BlendMode saved_assets = SDL_BLENDMODE_BLEND;
    if (cat__g.status_assets) {
        SDL_GetTextureBlendMode(cat__g.status_assets, &saved_assets);
        SDL_SetTextureBlendMode(cat__g.status_assets, mask);
    }
    cat_draw_color white = { 255, 255, 255, 255 };
    cat_draw_rounded_rect_ex(x, y, w, h, r, corners, white);
    if (cat__g.status_assets) SDL_SetTextureBlendMode(cat__g.status_assets, saved_assets);
    SDL_SetRenderDrawBlendMode(rend, saved_draw);
    return true;
}

void cat_draw_rounded_rect(int x, int y, int w, int h, int r, cat_draw_color c) {
    cat_draw_rounded_rect_ex(x, y, w, h, r, CAT_CORNER_ALL, c);
}

void cat_draw_pill(int x, int y, int w, int h, cat_draw_color c) {
    /* Pill shape is themed: the active stylesheet's ui.pill_radius_ratio scales the corner
       radius (0 = sharp rectangle, 1 = full half-cap circle). The pre-rendered sprite has
       fixed full-cap geometry, so we only take the sprite path at ratio == 1.0; any other
       ratio falls through to the procedural rounded-rect path. */
    float ratio = cat__g.theme.pill_radius_ratio;
    if (ratio <= 0.0f) {
        cat_draw_rect(x, y, w, h, c);
        return;
    }
    /* Asymmetric corners (e.g. the "Leaf" list style): round only the masked
       corners. 0 means unset → treat as all four (the default pill). */
    int corner_mask = cat__g.theme.pill_corner_mask;
    if (corner_mask != 0 && corner_mask != CAT_CORNER_ALL) {
        int r = (int)(ratio * (h * 0.5f) + 0.5f);
        if (r < 1) { cat_draw_rect(x, y, w, h, c); return; }
        int max_r = (w < h ? w : h) / 2;
        if (r > max_r) r = max_r;
        cat_draw_rounded_rect_ex(x, y, w, h, r, (unsigned)corner_mask, c);
        return;
    }
    if (ratio >= 1.0f && cat__g.status_assets) {
        /* Sprite path: white 30×30 AA pill at (1,1) in 1x coords; blit two caps + center. */
        int s = cat__g.status_asset_scale;
        int sprite_x = 1 * s;
        int sprite_y = 1 * s;
        int sprite_sz = 30 * s;
        int cap_src_w = sprite_sz / 2;
        int cap_dst_w = h / 2;

        SDL_SetTextureColorMod(cat__g.status_assets, c.r, c.g, c.b);
        SDL_SetTextureAlphaMod(cat__g.status_assets, c.a);

        SDL_Rect lsrc = { sprite_x, sprite_y, cap_src_w, sprite_sz };
        SDL_Rect ldst = { x, y, cap_dst_w, h };
        SDL_RenderCopy(cat__g.renderer, cat__g.status_assets, &lsrc, &ldst);

        int center_w = w - 2 * cap_dst_w;
        if (center_w > 0) {
            SDL_SetRenderDrawColor(cat__g.renderer, c.r, c.g, c.b, c.a);
            SDL_Rect center = { x + cap_dst_w - 1, y, center_w + 2, h };   /* LoongMate patch: chồng 1px lên hai đầu */
            SDL_RenderFillRect(cat__g.renderer, &center);
        }

        SDL_Rect rsrc = { sprite_x + cap_src_w, sprite_y, cap_src_w, sprite_sz };
        SDL_Rect rdst = { x + cap_dst_w + (center_w > 0 ? center_w : 0), y, cap_dst_w, h };
        SDL_RenderCopy(cat__g.renderer, cat__g.status_assets, &rsrc, &rdst);
        return;
    }

    /* Procedural path: radius = ratio × h/2. Caller-controlled shape. */
    int r = (int)(ratio * (h * 0.5f) + 0.5f);
    if (r < 1) { cat_draw_rect(x, y, w, h, c); return; }
    int max_r = (w < h ? w : h) / 2;
    if (r > max_r) r = max_r;
    cat_draw_rounded_rect(x, y, w, h, r, c);
}

void cat_draw_rect(int x, int y, int w, int h, cat_draw_color c) {
    SDL_SetRenderDrawColor(cat__g.renderer, c.r, c.g, c.b, c.a);
    SDL_Rect r = {x, y, w, h};
    SDL_RenderFillRect(cat__g.renderer, &r);
}

void cat_draw_textured_quad(SDL_Texture *tex, const SDL_FPoint corners[4],
                            const SDL_FPoint uv[4], const SDL_Color col[4]) {
    static const SDL_FPoint kFullUV[4] = { {0, 0}, {1, 0}, {1, 1}, {0, 1} };
    static const SDL_Color  kWhite     = { 255, 255, 255, 255 };
    const SDL_FPoint *u = uv ? uv : kFullUV;
    SDL_Vertex v[4];
    for (int i = 0; i < 4; i++) {
        v[i].position  = corners[i];
        v[i].tex_coord = u[i];
        v[i].color     = col ? col[i] : kWhite;
    }
    static const int idx[6] = { 0, 1, 2, 0, 2, 3 };
    SDL_RenderGeometry(cat__g.renderer, tex, v, 4, idx, 6);
}

void cat_draw_circle(int cx, int cy, int r, cat_draw_color c) {
    SDL_SetRenderDrawColor(cat__g.renderer, c.r, c.g, c.b, c.a);
    for (int dy = -r; dy <= r; dy++) {
        int dx = (int)(sqrtf((float)(r * r - dy * dy)) + 0.5f);
        SDL_RenderDrawLine(cat__g.renderer, cx - dx, cy + dy, cx + dx, cy + dy);
    }
}

void cat_draw_star(int cx, int cy, int outer_r, cat_draw_color c) {
    if (outer_r < 1) return;

    /* Filled 5-point star as a triangle fan: a center vertex plus 10 perimeter
       vertices alternating between the outer radius (points) and an inner
       radius (valleys), starting at the top. Drawn untextured so the per-vertex
       color fills it — no font glyph required. */
    enum { CAT__STAR_POINTS = 5, CAT__STAR_PERIM = CAT__STAR_POINTS * 2 };
    const float inner_r = (float)outer_r * 0.42f;
    const float start_angle = -1.57079633f;          /* -90deg: first point up */
    const float step_angle = 0.62831853f;             /* 36deg between vertices */
    SDL_Color col = (SDL_Color){ c.r, c.g, c.b, c.a };

    SDL_Vertex verts[1 + CAT__STAR_PERIM];
    verts[0].position  = (SDL_FPoint){ (float)cx, (float)cy };
    verts[0].tex_coord = (SDL_FPoint){ 0.0f, 0.0f };
    verts[0].color     = col;
    for (int i = 0; i < CAT__STAR_PERIM; i++) {
        float radius = (i % 2 == 0) ? (float)outer_r : inner_r;
        float angle  = start_angle + (float)i * step_angle;
        verts[1 + i].position  = (SDL_FPoint){ (float)cx + radius * cosf(angle),
                                               (float)cy + radius * sinf(angle) };
        verts[1 + i].tex_coord = (SDL_FPoint){ 0.0f, 0.0f };
        verts[1 + i].color     = col;
    }

    int indices[3 * CAT__STAR_PERIM];
    for (int i = 0; i < CAT__STAR_PERIM; i++) {
        indices[i * 3 + 0] = 0;
        indices[i * 3 + 1] = 1 + i;
        indices[i * 3 + 2] = 1 + ((i + 1) % CAT__STAR_PERIM);
    }
    SDL_RenderGeometry(cat__g.renderer, NULL, verts, 1 + CAT__STAR_PERIM,
                       indices, 3 * CAT__STAR_PERIM);
}

static uint32_t cat__text_hash(const char *s) {
    uint32_t h = 2166136261u;
    while (*s) {
        h ^= (uint8_t)*s++;
        h *= 16777619u;
    }
    return h;
}

static bool cat__text_cacheable(const char *text) {
    for (int i = 0; i < CAT_TEXT_CACHE_MAX_TEXT; i++) {
        if (text[i] == '\0') return true;
    }
    return false;
}

static SDL_Texture *cat__render_text_texture(TTF_Font *font, const char *text,
                                             cat_draw_color color, int *out_w,
                                             int *out_h) {
    SDL_Surface *surf = TTF_RenderUTF8_Blended(font, text, color);
    if (!surf) return NULL;

    SDL_Texture *tex = SDL_CreateTextureFromSurface(cat__g.renderer, surf);
    int w = surf->w;
    int h = surf->h;
    SDL_FreeSurface(surf);
    if (!tex) return NULL;

    SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_BLEND);
    if (out_w) *out_w = w;
    if (out_h) *out_h = h;
    return tex;
}

static int cat__draw_text_uncached(TTF_Font *font, const char *text, int x, int y,
                                  cat_draw_color color) {
    int w = 0, h = 0;
    SDL_Texture *tex = cat__render_text_texture(font, text, color, &w, &h);
    if (!tex) return 0;

    SDL_Rect dst = {x, y, w, h};
    SDL_RenderCopy(cat__g.renderer, tex, NULL, &dst);
    SDL_DestroyTexture(tex);
    return w;
}

static cat_text_cache_entry *cat__text_cache_find(TTF_Font *font, const char *text,
                                                  uint32_t hash) {
    for (int i = 0; i < cat__g.text_cache.count; i++) {
        cat_text_cache_entry *e = &cat__g.text_cache.entries[i];
        if (e->font == font &&
            e->hash == hash &&
            strcmp(e->text, text) == 0) {
            e->last_used = SDL_GetTicks();
            return e;
        }
    }
    return NULL;
}

static void cat__text_cache_evict(int idx) {
    if (idx < 0 || idx >= cat__g.text_cache.count) return;
    if (cat__g.text_cache.entries[idx].texture) {
        SDL_DestroyTexture(cat__g.text_cache.entries[idx].texture);
    }
    cat__g.text_cache.entries[idx] = (cat_text_cache_entry){0};
    if (idx < cat__g.text_cache.count - 1) {
        cat__g.text_cache.entries[idx] =
            cat__g.text_cache.entries[cat__g.text_cache.count - 1];
    }
    cat__g.text_cache.count--;
}

static cat_text_cache_entry *cat__text_cache_insert(TTF_Font *font, const char *text,
                                                    uint32_t hash) {
    /* Glyphs are rasterized white and tinted at draw time with SDL colour
       modulation. TTF_RenderUTF8_Blended writes (r,g,b,coverage), so white plus
       a (r,g,b) mod is pixel-identical to rasterizing in that colour -- and it
       keeps colour out of the cache key. Animated text (a row lerping between
       theme->text and theme->highlighted_text as the cursor moves) would
       otherwise miss on every frame, re-rasterizing and re-uploading the same
       string at a slightly different tint and evicting live entries to do it. */
    int w = 0, h = 0;
    cat_draw_color white = { 255, 255, 255, 255 };
    SDL_Texture *tex = cat__render_text_texture(font, text, white, &w, &h);
    if (!tex) return NULL;

    if (cat__g.text_cache.count >= CAT_TEXT_CACHE_SIZE) {
        int lru = 0;
        for (int i = 1; i < cat__g.text_cache.count; i++) {
            if (cat__g.text_cache.entries[i].last_used <
                cat__g.text_cache.entries[lru].last_used) {
                lru = i;
            }
        }
        cat__text_cache_evict(lru);
    }

    cat_text_cache_entry *e = &cat__g.text_cache.entries[cat__g.text_cache.count++];
    e->font = font;
    e->hash = hash;
    strncpy(e->text, text, sizeof(e->text) - 1);
    e->text[sizeof(e->text) - 1] = '\0';
    e->texture = tex;
    e->w = w;
    e->h = h;
    e->last_used = SDL_GetTicks();
    return e;
}

/* Tint a cached (white) glyph texture for this draw. */
static void cat__text_apply_tint(SDL_Texture *tex, cat_draw_color color) {
    SDL_SetTextureColorMod(tex, color.r, color.g, color.b);
    SDL_SetTextureAlphaMod(tex, color.a);
}

static cat_text_cache_entry *cat__text_cache_get(TTF_Font *font, const char *text) {
    if (!font || !text || !text[0] || !cat__g.renderer || !cat__text_cacheable(text))
        return NULL;

    uint32_t hash = cat__text_hash(text);
    cat_text_cache_entry *e = cat__text_cache_find(font, text, hash);
    if (e) return e;
    return cat__text_cache_insert(font, text, hash);
}

static void cat__text_cache_clear(void) {
    for (int i = 0; i < cat__g.text_cache.count; i++) {
        if (cat__g.text_cache.entries[i].texture) {
            SDL_DestroyTexture(cat__g.text_cache.entries[i].texture);
        }
    }
    memset(&cat__g.text_cache, 0, sizeof(cat__g.text_cache));
}

int cat_draw_text(TTF_Font *font, const char *text, int x, int y, cat_draw_color color) {
    font = cat__font_for_text(font, text);   /* draw and measure must agree */
    if (!font || !text || !text[0]) return 0;

    cat_text_cache_entry *e = cat__text_cache_get(font, text);
    if (e && e->texture) {
        cat__text_apply_tint(e->texture, color);
        SDL_Rect dst = {x, y, e->w, e->h};
        SDL_RenderCopy(cat__g.renderer, e->texture, NULL, &dst);
        return e->w;
    }

    return cat__draw_text_uncached(font, text, x, y, color);
}

int cat_draw_text_clipped(TTF_Font *font, const char *text, int x, int y, cat_draw_color color, int max_w) {
    font = cat__font_for_text(font, text);   /* draw and measure must agree */
    if (!font || !text || !text[0]) return 0;
    if (max_w <= 0) return cat_draw_text(font, text, x, y, color);

    cat_text_cache_entry *e = cat__text_cache_get(font, text);
    if (e && e->texture) {
        cat__text_apply_tint(e->texture, color);
        int draw_w = e->w;
        if (draw_w > max_w) draw_w = max_w;

        SDL_Rect src = {0, 0, draw_w, e->h};
        SDL_Rect dst = {x, y, draw_w, e->h};
        SDL_RenderCopy(cat__g.renderer, e->texture, &src, &dst);
        return e->w;
    }

    int tw = 0, th = 0;
    SDL_Texture *tex = cat__render_text_texture(font, text, color, &tw, &th);
    if (!tex) return 0;
    int draw_w = tw;
    if (draw_w > max_w) draw_w = max_w;

    SDL_Rect src = {0, 0, draw_w, th};
    SDL_Rect dst = {x, y, draw_w, th};
    SDL_RenderCopy(cat__g.renderer, tex, &src, &dst);

    SDL_DestroyTexture(tex);
    return tw;
}

int cat_draw_text_ellipsized(TTF_Font *font, const char *text, int x, int y, cat_draw_color color, int max_w) {
    font = cat__font_for_text(font, text);   /* draw and measure must agree */
    if (!font || !text || !text[0]) return 0;
    if (max_w <= 0) return cat_draw_text(font, text, x, y, color);

    int full_w = cat_measure_text(font, text);
    if (full_w <= max_w) return cat_draw_text(font, text, x, y, color);

    int ellipsis_w = cat_measure_text(font, "...");
    if (ellipsis_w >= max_w) return cat_draw_text_clipped(font, text, x, y, color, max_w);

    int target_w = max_w - ellipsis_w;
    int len = (int)strlen(text);
    int lo = 0, hi = len, best = 0;

    /* Use stack buffer for typical labels, heap for unusually long strings */
    char stack_buf[1024];
    int buf_size = len + 4;  /* +4 for "..." and NUL */
    char *buf = buf_size <= (int)sizeof(stack_buf) ? stack_buf : (char *)malloc(buf_size);
    if (!buf) return cat_draw_text_clipped(font, text, x, y, color, max_w);

    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        /* Walk back to a valid UTF-8 start byte */
        int safe = mid;
        while (safe > 0 && (text[safe] & 0xC0) == 0x80) safe--;

        memcpy(buf, text, safe);
        buf[safe] = '\0';

        int tw = 0;
        TTF_SizeUTF8(font, buf, &tw, NULL);

        if (tw <= target_w) {
            best = safe;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }

    /* Strip trailing spaces before ellipsis */
    while (best > 0 && text[best - 1] == ' ') best--;

    memcpy(buf, text, best);
    buf[best]     = '.';
    buf[best + 1] = '.';
    buf[best + 2] = '.';
    buf[best + 3] = '\0';

    int result = cat_draw_text(font, buf, x, y, color);
    if (buf != stack_buf) free(buf);
    return result;
}

int cat_measure_text_ellipsized(TTF_Font *font, const char *text, int max_w) {
    font = cat__font_for_text(font, text);   /* draw and measure must agree */
    if (!font || !text || !text[0]) return 0;
    if (max_w <= 0) return 0;

    int full_w = cat_measure_text(font, text);
    if (full_w <= max_w) return full_w;

    int ellipsis_w = cat_measure_text(font, "...");
    if (ellipsis_w >= max_w) return max_w;

    int target_w = max_w - ellipsis_w;
    int len = (int)strlen(text);
    int lo = 0, hi = len, best = 0;

    char stack_buf[1024];
    int buf_size = len + 4;
    char *buf = buf_size <= (int)sizeof(stack_buf) ? stack_buf : (char *)malloc(buf_size);
    int result_w;

    if (!buf) return max_w;

    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        int safe = mid;
        while (safe > 0 && (text[safe] & 0xC0) == 0x80) safe--;

        memcpy(buf, text, safe);
        buf[safe] = '\0';

        int tw = 0;
        TTF_SizeUTF8(font, buf, &tw, NULL);

        if (tw <= target_w) {
            best = safe;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }

    while (best > 0 && text[best - 1] == ' ') best--;

    memcpy(buf, text, best);
    buf[best]     = '.';
    buf[best + 1] = '.';
    buf[best + 2] = '.';
    buf[best + 3] = '\0';

    TTF_SizeUTF8(font, buf, &result_w, NULL);
    if (buf != stack_buf) free(buf);
    return result_w;
}

int cat_draw_text_wrapped(TTF_Font *font, const char *text, int x, int y, int max_w, cat_draw_color color, cat_text_align align) {
    font = cat__font_for_text(font, text);   /* draw and measure must agree */
    if (!font || !text || !text[0] || max_w <= 0) return 0;

    /* Word wrap: break text into lines that fit within max_w */
    char buf[4096];
    strncpy(buf, text, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    int line_h = TTF_FontLineSkip(font);
    int cur_y = y;

    char *line_start = buf;
    while (*line_start) {
        /* Preserve explicit blank lines ("\n\n") as vertical spacing. */
        if (*line_start == '\n') {
            cur_y += line_h;
            line_start++;
            continue;
        }

        /* Find how much text fits on this line */
        char *best_break = NULL;
        char *p = line_start;

        while (*p) {
            /* Find next word boundary */
            char *word_end = p;
            while (*word_end && *word_end != ' ' && *word_end != '\n') word_end++;

            /* Check if text up to word_end fits */
            char saved = *word_end;
            *word_end = '\0';

            int tw = 0;
            TTF_SizeUTF8(font, line_start, &tw, NULL);

            *word_end = saved;

            if (tw > max_w && best_break) {
                /* Doesn't fit — break at last good position */
                break;
            }

            best_break = word_end;

            if (*word_end == '\n') {
                best_break = word_end;
                break;
            }

            if (*word_end == '\0') break;
            p = word_end + 1;
        }

        if (!best_break || best_break == line_start) {
            /* Single word wider than max_w, or end of text */
            best_break = line_start + strlen(line_start);
        }

        /* Render this line */
        char saved = *best_break;
        *best_break = '\0';

        if (line_start[0]) {
            int tw = 0;
            TTF_SizeUTF8(font, line_start, &tw, NULL);

            int draw_x = x;
            if (align == CAT_ALIGN_CENTER) draw_x = x + (max_w - tw) / 2;
            else if (align == CAT_ALIGN_RIGHT) draw_x = x + max_w - tw;

            cat_draw_text(font, line_start, draw_x, cur_y, color);
        }

        cur_y += line_h;
        *best_break = saved;

        /* Advance past the break character */
        if (*best_break == ' ' || *best_break == '\n')
            line_start = best_break + 1;
        else
            line_start = best_break;
    }
    return cur_y - y;
}

static int cat__wrapped_line_count(TTF_Font *font, const char *text, int max_w) {
    if (!font || !text || !text[0] || max_w <= 0) return 0;

    char buf[4096];
    strncpy(buf, text, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    int lines = 0;
    char *line_start = buf;

    while (*line_start) {
        if (*line_start == '\n') {
            lines++;
            line_start++;
            continue;
        }

        char *best_break = NULL;
        char *p = line_start;

        while (*p) {
            char *word_end = p;
            while (*word_end && *word_end != ' ' && *word_end != '\n') word_end++;

            char saved = *word_end;
            *word_end = '\0';

            int tw = 0;
            TTF_SizeUTF8(font, line_start, &tw, NULL);

            *word_end = saved;

            if (tw > max_w && best_break) break;

            best_break = word_end;

            if (*word_end == '\n') {
                best_break = word_end;
                break;
            }

            if (*word_end == '\0') break;
            p = word_end + 1;
        }

        if (!best_break || best_break == line_start) {
            best_break = line_start + strlen(line_start);
        }

        lines++;

        if (*best_break == ' ' || *best_break == '\n')
            line_start = best_break + 1;
        else
            line_start = best_break;
    }

    return lines;
}

int cat_measure_text(TTF_Font *font, const char *text) {
    font = cat__font_for_text(font, text);   /* draw and measure must agree */
    if (!font || !text || !text[0]) return 0;
    if (cat__text_cacheable(text)) {
        cat_text_cache_entry *e =
            cat__text_cache_find(font, text, cat__text_hash(text));
        if (e) return e->w;
    }
    int w = 0;
    TTF_SizeUTF8(font, text, &w, NULL);
    return w;
}

int cat_measure_wrapped_text_height(TTF_Font *font, const char *text, int max_w) {
    font = cat__font_for_text(font, text);   /* draw and measure must agree */
    if (!font || !text || !text[0] || max_w <= 0) return 0;
    return cat__wrapped_line_count(font, text, max_w) * TTF_FontLineSkip(font);
}

SDL_Rect cat_get_content_rect(bool has_title, bool has_footer, bool has_status_bar) {
    SDL_Rect rect = { 0, 0, cat_get_screen_width(), cat_get_screen_height() };

    int top = 0;
    if (has_title || has_status_bar) {
        top = cat__max(CAT_DS(40), cat_get_status_bar_height());
    }

    int bottom = has_footer ? cat_get_footer_height() : 0;
    rect.y = top;
    rect.h = cat_get_screen_height() - top - bottom;
    if (rect.h < 0) rect.h = 0;

    return rect;
}

SDL_Rect cat_box_content(const cat_box *b) {
    SDL_Rect r = { 0, 0, 0, 0 };
    if (!b) return r;
    r.x = b->x + b->pad_l;
    r.y = b->y + b->pad_t;
    r.w = b->w - b->pad_l - b->pad_r;
    r.h = b->h - b->pad_t - b->pad_b;
    if (r.w < 0) r.w = 0;
    if (r.h < 0) r.h = 0;
    return r;
}

cat_box cat_box_carve_top(cat_box *b, int height) {
    cat_box strip = { 0, 0, 0, 0, 0, 0, 0, 0 };
    if (!b) return strip;
    SDL_Rect c = cat_box_content(b);
    if (height < 0) height = 0;
    if (height > c.h) height = c.h;
    strip.x = c.x; strip.y = c.y; strip.w = c.w; strip.h = height;
    b->pad_t += height;   /* consume the strip from the top of b's content */
    return strip;
}

cat_box cat_box_carve_bottom(cat_box *b, int height) {
    cat_box strip = { 0, 0, 0, 0, 0, 0, 0, 0 };
    if (!b) return strip;
    SDL_Rect c = cat_box_content(b);
    if (height < 0) height = 0;
    if (height > c.h) height = c.h;
    strip.x = c.x; strip.y = c.y + c.h - height; strip.w = c.w; strip.h = height;
    b->pad_b += height;
    return strip;
}

void cat_box_split_cols(const cat_box *b, int left_w, int gutter,
                        cat_box *left, cat_box *right) {
    if (!b) return;
    SDL_Rect c = cat_box_content(b);
    if (left_w < 0) left_w = 0;
    if (left_w > c.w) left_w = c.w;
    int half = gutter / 2;
    if (left) {
        cat_box l = { c.x, c.y, left_w, c.h, 0, half, 0, 0 };
        *left = l;
    }
    if (right) {
        cat_box r = { c.x + left_w, c.y, c.w - left_w, c.h, 0, 0, 0, half };
        *right = r;
    }
}

SDL_Rect cat_box_fit_rows(const cat_box *b, int base_item_h, int item_count,
                          int *visible_rows, int *out_item_h) {
    SDL_Rect r = cat_box_content(b);
    int base = base_item_h > 0 ? base_item_h : 1;
    int max_rows = r.h / base;   /* what fits at the natural height */
    if (max_rows < 1) max_rows = 1;
    int rows = (visible_rows && *visible_rows > 0) ? *visible_rows : max_rows;
    /* A cached count from before the box shrank (hint bar appeared, layout
       changed) could compress rows below base_item_h — or to zero height in a
       tiny box. Rows only ever stretch, so clamp to what actually fits. */
    if (rows > max_rows) rows = max_rows;
    (void)item_count;
    int item_h = base;
    if (r.h > 0) {
        /* Rows always render at the filled pitch, whether or not the list
           overflows: a short list draws fewer rows on the SAME grid a full one
           uses, so pills and any pane snapped to the box land pixel-identical
           across screens. List length changes how many rows appear, never the
           geometry. */
        item_h = r.h / rows;
        r.h = item_h * rows;
    }
    if (visible_rows) *visible_rows = rows;
    if (out_item_h)   *out_item_h = item_h;
    return r;
}

static void cat__draw_screen_title_impl(const char *title, cat_status_bar_opts *status_bar, bool center) {
    if (!title || !title[0]) return;

    int margin = CAT_DS(cat__g.device_padding + 5);
    int max_w = cat_get_screen_width() - margin * 2;
    if (status_bar) {
        int status_bar_w = cat_get_status_bar_width(status_bar);
        if (status_bar_w > 0) max_w -= status_bar_w + CAT_S(10);
    }
    if (max_w < 1) max_w = 1;

    static const cat_font_tier title_tiers[] = {
        CAT_FONT_EXTRA_LARGE,
        CAT_FONT_LARGE,
        CAT_FONT_MEDIUM,
    };

    TTF_Font *font = NULL;
    for (size_t i = 0; i < sizeof(title_tiers) / sizeof(title_tiers[0]); i++) {
        TTF_Font *candidate = cat_get_font(title_tiers[i]);
        if (!candidate) continue;
        font = candidate;
        if (cat_measure_text(candidate, title) <= max_w) break;
    }

    if (!font) return;

    int x = margin;
    if (center) {
        int text_w = cat_measure_text(font, title);
        if (text_w > max_w) text_w = max_w;
        x = margin + (max_w - text_w) / 2;
    }
    cat_draw_text_clipped(font, title, x, 0, cat_get_theme()->text, max_w);
}

void cat_draw_screen_title(const char *title, cat_status_bar_opts *status_bar) {
    cat__draw_screen_title_impl(title, status_bar, false);
}

void cat_draw_screen_title_centered(const char *title, cat_status_bar_opts *status_bar) {
    cat__draw_screen_title_impl(title, status_bar, true);
}

void cat_draw_image(SDL_Texture *tex, int x, int y, int w, int h) {
    if (!tex) return;
    SDL_Rect dst = {x, y, w, h};
    SDL_RenderCopy(cat__g.renderer, tex, NULL, &dst);
}

void cat_draw_image_rounded_ex(SDL_Texture *tex, int x, int y, int w, int h,
                               int r, unsigned corners) {
    SDL_Renderer *rend = cat__g.renderer;
    if (!tex || w <= 0 || h <= 0 || !rend) return;
    if (r <= 0 || corners == 0) { cat_draw_image(tex, x, y, w, h); return; }

    /* The mask multiplies the image's alpha by a white rounded-rect: dst.a =
       src.a*dst.a, leaving RGB untouched. This needs a custom blend mode. Some
       renderers reject it — and without the alpha multiply the "white" mask would
       paint a solid white rectangle over the art. So probe once, and if custom
       blend is unsupported fall back to a plain (square) draw — never white,
       transparency preserved. */
    SDL_BlendMode mask_blend = SDL_ComposeCustomBlendMode(
        SDL_BLENDFACTOR_ZERO, SDL_BLENDFACTOR_ONE,        SDL_BLENDOPERATION_ADD,
        SDL_BLENDFACTOR_ZERO, SDL_BLENDFACTOR_SRC_ALPHA,  SDL_BLENDOPERATION_ADD);
    static int custom_blend_ok = -1;
    if (custom_blend_ok < 0) {
        SDL_BlendMode probe_prev;
        SDL_GetRenderDrawBlendMode(rend, &probe_prev);
        custom_blend_ok = (SDL_SetRenderDrawBlendMode(rend, mask_blend) == 0) ? 1 : 0;
        SDL_SetRenderDrawBlendMode(rend, probe_prev);
    }
    if (!custom_blend_ok) { cat_draw_image(tex, x, y, w, h); return; }

    /* Scratch render target, grown to the largest box we've been asked to draw.
       Held in cat__g so cat_quit() can free it (a function-local static would be
       a permanent unfreed GPU allocation). */
    SDL_Texture *scratch = cat__g.rounded_scratch;
    if (!scratch || cat__g.rounded_scratch_w < w || cat__g.rounded_scratch_h < h) {
        if (scratch) SDL_DestroyTexture(scratch);
        if (w > cat__g.rounded_scratch_w) cat__g.rounded_scratch_w = w;
        if (h > cat__g.rounded_scratch_h) cat__g.rounded_scratch_h = h;
        scratch = SDL_CreateTexture(rend, SDL_PIXELFORMAT_RGBA8888,
                                    SDL_TEXTUREACCESS_TARGET,
                                    cat__g.rounded_scratch_w, cat__g.rounded_scratch_h);
        cat__g.rounded_scratch = scratch;
        if (!scratch) {
            cat__g.rounded_scratch_w = cat__g.rounded_scratch_h = 0;
            cat_draw_image(tex, x, y, w, h);
            return;
        }
        SDL_SetTextureBlendMode(scratch, SDL_BLENDMODE_BLEND);
    }

    SDL_Texture *prev_target = SDL_GetRenderTarget(rend);
    SDL_SetRenderTarget(rend, scratch);

    /* Clear the target fully transparent. RenderClear writes the alpha channel
       reliably (a plain NONE-blended fill can leave the target opaque on GLES). */
    SDL_SetRenderDrawColor(rend, 0, 0, 0, 0);
    SDL_RenderClear(rend);
    SDL_Rect region = { 0, 0, w, h };

    /* Copy the image in over the transparent target with normal alpha blending,
       so the image's own transparent pixels stay transparent (copying with
       BLENDMODE_NONE forces the alpha opaque on this renderer → white). */
    SDL_BlendMode tex_bm;
    SDL_GetTextureBlendMode(tex, &tex_bm);
    SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_BLEND);
    SDL_RenderCopy(rend, tex, NULL, &region);
    SDL_SetTextureBlendMode(tex, tex_bm);

    /* Multiply the region's alpha by the white rounded-rect mask (see above):
       inside the shape alpha is kept, outside the rounded corners it is zeroed,
       with AA at the arc edges. The mask's white RGB is ignored (color = dst). */
    SDL_BlendMode saved_draw_bm;
    SDL_GetRenderDrawBlendMode(rend, &saved_draw_bm);
    SDL_BlendMode saved_assets_bm = SDL_BLENDMODE_BLEND;
    if (cat__g.status_assets) SDL_GetTextureBlendMode(cat__g.status_assets, &saved_assets_bm);
    SDL_SetRenderDrawBlendMode(rend, mask_blend);
    if (cat__g.status_assets) SDL_SetTextureBlendMode(cat__g.status_assets, mask_blend);

    cat_draw_color white = { 255, 255, 255, 255 };
    cat_draw_rounded_rect_ex(0, 0, w, h, r, corners, white);

    SDL_SetRenderDrawBlendMode(rend, saved_draw_bm);
    if (cat__g.status_assets) SDL_SetTextureBlendMode(cat__g.status_assets, saved_assets_bm);

    /* Back to the real target; blit the masked image where it belongs. */
    SDL_SetRenderTarget(rend, prev_target);
    SDL_Rect out = { x, y, w, h };
    SDL_RenderCopy(rend, scratch, &region, &out);
}

SDL_Texture *cat_load_image(const char *path) {
    if (!path) return NULL;
    return IMG_LoadTexture(cat__g.renderer, path);
}

/* Downscale `src` so its largest dimension is <= max_dim, preserving aspect.
   Returns a new RGBA surface (caller frees), or NULL if no scaling is needed. */
static SDL_Surface *cat__downscale_surface(SDL_Surface *src, int max_dim) {
    if (!src || max_dim <= 0) return NULL;
    int sw = src->w, sh = src->h;
    int longest = sw > sh ? sw : sh;
    if (longest <= max_dim) return NULL;            /* already small enough */
    double scale = (double)max_dim / (double)longest;
    int dw = (int)(sw * scale + 0.5);
    int dh = (int)(sh * scale + 0.5);
    if (dw < 1) dw = 1;
    if (dh < 1) dh = 1;
    SDL_Surface *dst = SDL_CreateRGBSurfaceWithFormat(0, dw, dh, 32,
                                                      SDL_PIXELFORMAT_RGBA32);
    if (!dst) return NULL;
    /* SDL_BlitScaled needs a same-format source for predictable results. */
    SDL_Surface *src32 = src;
    bool free_src32 = false;
    if (src->format->format != SDL_PIXELFORMAT_RGBA32) {
        src32 = SDL_ConvertSurfaceFormat(src, SDL_PIXELFORMAT_RGBA32, 0);
        free_src32 = true;
        if (!src32) { SDL_FreeSurface(dst); return NULL; }
    }
    if (SDL_BlitScaled(src32, NULL, dst, NULL) != 0) {
        if (free_src32) SDL_FreeSurface(src32);
        SDL_FreeSurface(dst);
        return NULL;
    }
    if (free_src32) SDL_FreeSurface(src32);
    return dst;
}

bool cat_thumbnail_is_cached(const char *path, const char *thumb_path) {
    if (!thumb_path || !thumb_path[0]) return false;
    struct stat ss, ts;
    if (stat(thumb_path, &ts) != 0) return false;
    /* Fresh if the source is gone or no newer than the thumbnail. */
    return (stat(path, &ss) != 0 || ts.st_mtime >= ss.st_mtime);
}

SDL_Surface *cat_decode_thumbnail_surface(const char *path, const char *thumb_path,
                                          int max_dim) {
    if (!path || !path[0]) return NULL;

    /* Fast path: a fresh thumbnail already exists — decode the small file. */
    if (cat_thumbnail_is_cached(path, thumb_path)) {
        SDL_Surface *t = IMG_Load(thumb_path);
        if (t) return t;
        /* fall through to regenerate a corrupt/unreadable thumbnail */
    }

    SDL_Surface *full = IMG_Load(path);
    if (!full) return NULL;

    SDL_Surface *small = cat__downscale_surface(full, max_dim);
    SDL_Surface *result = small ? small : full;

    /* Persist every successful cold decode, including sources that are already
       within max_dim. Callers use the thumbnail's presence to mark pre-warm work
       complete; omitting it for a small source makes them enqueue and decode the
       same image again on every frame. */
    if (thumb_path && thumb_path[0]) {
        IMG_SavePNG(result, thumb_path);              /* best-effort; ignore failure */
    }

    if (small) SDL_FreeSurface(full);
    return result;
}

SDL_Texture *cat_texture_from_surface(SDL_Surface *surf) {
    if (!surf) return NULL;
    return SDL_CreateTextureFromSurface(cat__g.renderer, surf);
}

SDL_Texture *cat_load_image_thumbnail(const char *path, const char *thumb_path,
                                      int max_dim, int *out_w, int *out_h) {
    SDL_Surface *surf = cat_decode_thumbnail_surface(path, thumb_path, max_dim);
    if (!surf) return NULL;
    SDL_Texture *tex = SDL_CreateTextureFromSurface(cat__g.renderer, surf);
    int tw = surf->w, th = surf->h;
    SDL_FreeSurface(surf);
    if (!tex) return NULL;
    if (out_w) *out_w = tw;
    if (out_h) *out_h = th;
    return tex;
}

void cat_draw_scrollbar(int x, int y, int h, int visible, int total, int offset) {
    if (total <= visible || total <= 0) return;

    SDL_Renderer *rend = cat__g.renderer;
    SDL_BlendMode prev_blend = SDL_BLENDMODE_NONE;
    SDL_GetRenderDrawBlendMode(rend, &prev_blend);
    SDL_SetRenderDrawBlendMode(rend, SDL_BLENDMODE_BLEND);

    int bar_w = CAT_S(4);
    int track_h = h;
    int thumb_h = cat__max((track_h * visible) / total, CAT_S(20));
    int thumb_y = y + (offset * (track_h - thumb_h)) / (total - visible);

    /* Track */
    cat_draw_color track_color = cat__g.theme.hint;
    track_color.a = 40;
    cat_draw_rounded_rect(x, y, bar_w, track_h, bar_w / 2, track_color);

    /* Thumb */
    cat_draw_color thumb_color = cat__g.theme.hint;
    thumb_color.a = 120;
    cat_draw_rounded_rect(x, thumb_y, bar_w, thumb_h, bar_w / 2, thumb_color);

    SDL_SetRenderDrawBlendMode(rend, prev_blend);
}

void cat_draw_progress_bar(int x, int y, int w, int h, float progress, cat_draw_color fg, cat_draw_color bg) {
    /* Background track */
    cat_draw_rounded_rect(x, y, w, h, h / 2, bg);

    /* Fill */
    int fill_w = (int)(w * cat__clamp((int)(progress * 100), 0, 100) / 100.0f);
    if (fill_w > 0) {
        cat_draw_rounded_rect(x, y, fill_w, h, h / 2, fg);
    }
}

/* ─── Text Scrolling ─────────────────────────────────────────────────────── */

void cat_text_scroll_init(cat_text_scroll *s) {
    if (!s) return;
    s->offset = 0;
    s->direction = 1;
    s->pause_timer = CAT_TEXT_SCROLL_PAUSE_MS;
    s->active = false;
}

void cat_text_scroll_update(cat_text_scroll *s, int text_w, int visible_w, uint32_t dt_ms) {
    if (!s) return;

    if (text_w <= visible_w) {
        s->offset = 0;
        s->active = false;
        return;
    }

    s->active = true;
    int max_offset = text_w - visible_w;

    if (s->pause_timer > 0) {
        s->pause_timer -= (int)dt_ms;
        return;
    }

    s->offset += s->direction * CAT_TEXT_SCROLL_SPEED;

    if (s->offset >= max_offset) {
        s->offset = max_offset;
        s->direction = -1;
        s->pause_timer = CAT_TEXT_SCROLL_PAUSE_MS;
    } else if (s->offset <= 0) {
        s->offset = 0;
        s->direction = 1;
        s->pause_timer = CAT_TEXT_SCROLL_PAUSE_MS;
    }
}

void cat_text_scroll_reset(cat_text_scroll *s) {
    cat_text_scroll_init(s);
}

bool cat_draw_text_marquee(TTF_Font *font, const char *text, int x, int y,
                           cat_draw_color color, int visible_w,
                           cat_marquee *m, uint32_t dt_ms) {
    if (!font || !text) return false;
    font = cat__font_for_text(font, text);   /* draw and measure must agree */

    int text_w = cat_measure_text(font, text);
    if (visible_w <= 0 || text_w <= visible_w) {
        if (m) m->elapsed_ms = 0;
        cat_draw_text(font, text, x, y, color);
        return false;
    }

    if (m) m->elapsed_ms += dt_ms;
    uint32_t elapsed = m ? m->elapsed_ms : 0;
    cat_marquee_mode mode = m ? m->mode : CAT_MARQUEE_LOOP;

    const uint32_t pause_ms   = 900;           /* hold before/at the turns */
    const int      speed_px_s = CAT_S(70);

    int off = 0;
    int wrap_period = 0;                        /* >0 → draw a wrap copy (loop only) */

    if (mode == CAT_MARQUEE_PINGPONG) {
        /* Scroll out to the far edge, pause, scroll back, pause, repeat. */
        int max_off = text_w - visible_w;
        if (max_off < 1) max_off = 1;
        int speed = speed_px_s > 0 ? speed_px_s : 1;
        uint32_t travel_ms = (uint32_t)(((uint64_t)max_off * 1000u) / (uint64_t)speed);
        if (travel_ms < 1) travel_ms = 1;
        uint32_t cycle = pause_ms + travel_ms + pause_ms + travel_ms;
        uint32_t t = cycle ? (elapsed % cycle) : 0;
        if (t < pause_ms) {
            off = 0;
        } else if (t < pause_ms + travel_ms) {
            off = (int)(((uint64_t)(t - pause_ms) * (uint64_t)speed) / 1000u);
        } else if (t < pause_ms + travel_ms + pause_ms) {
            off = max_off;
        } else {
            uint32_t back = t - (pause_ms + travel_ms + pause_ms);
            off = max_off - (int)(((uint64_t)back * (uint64_t)speed) / 1000u);
        }
        if (off < 0) off = 0;
        if (off > max_off) off = max_off;
    } else {
        int gap = visible_w / 9;
        if (gap < CAT_S(10)) gap = CAT_S(10);
        wrap_period = text_w + gap;             /* one full loop in pixels */
        uint32_t scroll_ms = (elapsed > pause_ms) ? (elapsed - pause_ms) : 0;
        off = (int)(((uint64_t)scroll_ms * (uint64_t)speed_px_s) / 1000u);
        if (wrap_period > 0) off %= wrap_period;
    }

    /* Clip to this cell, intersected with any clip already in effect, so the
       marquee composes correctly inside a clipped region (e.g. a scroll view),
       and restore the previous clip on exit rather than clearing it. */
    SDL_bool had_clip = SDL_RenderIsClipEnabled(cat__g.renderer);
    SDL_Rect prev_clip;
    if (had_clip) SDL_RenderGetClipRect(cat__g.renderer, &prev_clip);
    SDL_Rect cell = { x, y, visible_w, TTF_FontHeight(font) };
    SDL_Rect use_clip = cell;
    if (had_clip && !SDL_IntersectRect(&prev_clip, &cell, &use_clip)) {
        /* This cell is entirely outside the outer clip — draw nothing. */
        SDL_RenderSetClipRect(cat__g.renderer, &prev_clip);
        return true;
    }
    SDL_RenderSetClipRect(cat__g.renderer, &use_clip);
    cat_draw_text(font, text, x - off, y, color);
    if (wrap_period > 0)
        cat_draw_text(font, text, x - off + wrap_period, y, color);  /* wrap-around copy */
    SDL_RenderSetClipRect(cat__g.renderer, had_clip ? &prev_clip : NULL);
    return true;
}

/* ─── Texture Cache ──────────────────────────────────────────────────────── */

SDL_Texture *cat_cache_get(const char *key, int *w, int *h) {
    uint32_t now = SDL_GetTicks();
    for (int i = 0; i < cat__g.tex_cache.count; i++) {
        if (strcmp(cat__g.tex_cache.entries[i].key, key) == 0) {
            cat__g.tex_cache.entries[i].last_used = now;
            if (w) *w = cat__g.tex_cache.entries[i].w;
            if (h) *h = cat__g.tex_cache.entries[i].h;
            return cat__g.tex_cache.entries[i].texture;
        }
    }
    return NULL;
}

void cat_cache_put(const char *key, SDL_Texture *tex, int w, int h) {
    uint32_t now = SDL_GetTicks();

    /* Check if already cached */
    for (int i = 0; i < cat__g.tex_cache.count; i++) {
        if (strcmp(cat__g.tex_cache.entries[i].key, key) == 0) {
            if (cat__g.tex_cache.entries[i].texture != tex) {
                SDL_DestroyTexture(cat__g.tex_cache.entries[i].texture);
            }
            cat__g.tex_cache.entries[i].texture = tex;
            cat__g.tex_cache.entries[i].w = w;
            cat__g.tex_cache.entries[i].h = h;
            cat__g.tex_cache.entries[i].last_used = now;
            return;
        }
    }

    /* If cache is full, evict least recently used */
    if (cat__g.tex_cache.count >= CAT_TEXTURE_CACHE_SIZE) {
        int lru = 0;
        for (int i = 1; i < cat__g.tex_cache.count; i++) {
            if (cat__g.tex_cache.entries[i].last_used < cat__g.tex_cache.entries[lru].last_used)
                lru = i;
        }
        SDL_DestroyTexture(cat__g.tex_cache.entries[lru].texture);
        cat__g.tex_cache.entries[lru] = (cat_cache_entry){0};
        /* Move last entry to fill gap */
        if (lru < cat__g.tex_cache.count - 1) {
            cat__g.tex_cache.entries[lru] = cat__g.tex_cache.entries[cat__g.tex_cache.count - 1];
        }
        cat__g.tex_cache.count--;
    }

    /* Add new entry */
    cat_cache_entry *e = &cat__g.tex_cache.entries[cat__g.tex_cache.count++];
    strncpy(e->key, key, sizeof(e->key) - 1);
    e->key[sizeof(e->key) - 1] = '\0';
    e->texture = tex;
    e->w = w;
    e->h = h;
    e->last_used = now;
}

void cat_cache_clear(void) {
    for (int i = 0; i < cat__g.tex_cache.count; i++) {
        if (cat__g.tex_cache.entries[i].texture) {
            SDL_DestroyTexture(cat__g.tex_cache.entries[i].texture);
        }
    }
    memset(&cat__g.tex_cache, 0, sizeof(cat__g.tex_cache));
}

/* ─── Footer & Status Bar ────────────────────────────────────────────────── */

int cat_get_footer_height(void) {
    /* padding + pill_size, scaled by device_scale */
    return CAT_DS(cat__g.device_padding + CAT__PILL_SIZE);
}

void cat_set_footer_overflow_opts(const cat_footer_overflow_opts *opts) {
    if (opts) {
        cat__g.footer_overflow_opts = *opts;
    } else {
        cat__g.footer_overflow_opts.enabled = true;
        cat__g.footer_overflow_opts.chord_a = CAT_BTN_NONE;
        cat__g.footer_overflow_opts.chord_b = CAT_BTN_NONE;
    }

    memset(cat__g.footer_overflow_swallow, 0, sizeof(cat__g.footer_overflow_swallow));
    cat__g.footer_overflow_chord_held = false;
    cat__g.footer_overflow_open_requested = false;

    if (!cat__g.footer_overflow_opts.enabled ||
        cat__g.footer_overflow_opts.chord_a == CAT_BTN_NONE ||
        cat__g.footer_overflow_opts.chord_b == CAT_BTN_NONE ||
        cat__g.footer_overflow_opts.chord_a == cat__g.footer_overflow_opts.chord_b) {
        cat__g.footer_overflow_active = false;
        cat__g.footer_hidden_count = 0;
    }
}

void cat_show_footer_overflow(void) {
    if (cat__g.footer_hidden_count > 0)
        cat__footer_overflow_show_hidden_actions();
}

void cat_get_footer_overflow_opts(cat_footer_overflow_opts *out) {
    if (!out) return;
    *out = cat__g.footer_overflow_opts;
}

static const char *cat__footer_button_text(cat_footer_item *item) {
    if (!item) return "";
    if (item->button_text && item->button_text[0]) return item->button_text;
    return cat_button_name(item->button);
}

static bool cat__footer_button_text_is_single_codepoint(const char *text) {
    return cat__utf8_codepoint_count(text) == 1;
}

static bool cat__text_has_non_ascii(const char *s) {
    for (; s && *s; s++) if ((unsigned char)*s & 0x80u) return true;
    return false;
}

/* Scale a footer pill-background colour by the one-shot footer opacity (0 = opaque). */
static cat_draw_color cat__footer_bg(cat_draw_color c) {
    int op = cat__g.footer_bg_opacity;
    if (op > 0 && op < 255) c.a = (Uint8)((int)c.a * op / 255);
    return c;
}

static TTF_Font *cat__footer_button_font(const char *btn_name) {
    if (!btn_name || !btn_name[0]) return cat_get_font(CAT_FONT_SMALL);
    if (cat__text_has_non_ascii(btn_name)) {
        return cat__get_symbol_font_small(); /* arrows/symbols the themed family lacks */
    }
    if (cat__footer_button_text_is_single_codepoint(btn_name)) {
        return cat_get_font(CAT_FONT_MEDIUM); /* single-codepoint button label */
    }
    return cat_get_font(CAT_FONT_TINY); /* multi-codepoint button label */
}

static int cat__footer_item_width(cat_footer_item *item, TTF_Font *hint_font, int btn_margin) {
    const char *btn_name = cat__footer_button_text(item);
    const char *label = item->label ? item->label : "";
    TTF_Font *btn_font = cat__footer_button_font(btn_name);
    if (!btn_font) btn_font = hint_font;

    int btn_tw = cat_measure_text(btn_font, btn_name);
    int btn_w = cat__footer_button_text_is_single_codepoint(btn_name)
        ? CAT_DS(CAT__BUTTON_SIZE)
        : (CAT_DS(CAT__BUTTON_SIZE) / 2 + btn_tw);
    int label_w = cat_measure_text(hint_font, label);
    return btn_w + btn_margin + label_w + btn_margin;
}

static int cat__footer_marker_width(int hidden_count, TTF_Font *hint_font, int btn_margin) {
    if (hidden_count <= 0) return 0;
    char marker[16];
    snprintf(marker, sizeof(marker), "+%d", hidden_count);
    return btn_margin + cat_measure_text(hint_font, marker) + btn_margin;
}

static int cat__footer_group_outer_width(const int *widths, int visible_count,
                                        int item_gap, int outer_pad, int marker_w) {
    if (visible_count <= 0 && marker_w <= 0) return 0;

    int inner = 0;
    for (int i = 0; i < visible_count; i++) {
        if (i > 0) inner += item_gap;
        inner += widths[i];
    }
    if (marker_w > 0) {
        if (visible_count > 0) inner += item_gap;
        inner += marker_w;
    }
    return outer_pad + inner + outer_pad;
}

static void cat__footer_draw_item(int *cx, int btn_y, int inner_h, int btn_margin,
                                 int hint_font_h, TTF_Font *hint_font, cat_footer_item *item) {
    const char *btn_name = cat__footer_button_text(item);
    const char *label = item->label ? item->label : "";
    TTF_Font *btn_font = cat__footer_button_font(btn_name);
    if (!btn_font) btn_font = hint_font;

    int btn_font_h = TTF_FontHeight(btn_font);
    int btn_tw = cat_measure_text(btn_font, btn_name);
    int btn_pill_w = cat__footer_button_text_is_single_codepoint(btn_name)
        ? CAT_DS(CAT__BUTTON_SIZE)
        : (CAT_DS(CAT__BUTTON_SIZE) / 2 + btn_tw);

    cat_draw_pill(*cx, btn_y, btn_pill_w, inner_h, cat__footer_bg(cat__g.theme.button_glyph_bg));
    cat_draw_text(btn_font, btn_name,
                 *cx + (btn_pill_w - btn_tw) / 2,
                 btn_y + (inner_h - btn_font_h) / 2,
                 cat__g.theme.button_label);

    *cx += btn_pill_w + btn_margin;
    cat_draw_text(hint_font, label,
                 *cx,
                 btn_y + (inner_h - hint_font_h) / 2,
                 cat__g.theme.hint);
    *cx += cat_measure_text(hint_font, label) + btn_margin;
}

void cat_set_footer_bg_opacity(int alpha) {
    cat__g.footer_bg_opacity = alpha < 0 ? 0 : (alpha > 255 ? 255 : alpha);
}

static void cat__footer_store_hidden_item(cat_footer_item *item) {
    if (!item) return;
    cat__g.footer_overflow_active = true;
    if (cat__g.footer_hidden_count >= CAT__MAX_FOOTER_ITEMS) return;
    cat__g.footer_hidden_items[cat__g.footer_hidden_count++] = *item;
}

void cat_draw_footer(cat_footer_item *items, int count) {
    cat__footer_overflow_clear_visible_state();
    if (!items || count <= 0) return;
    if (count > CAT__MAX_FOOTER_ITEMS) count = CAT__MAX_FOOTER_ITEMS;

    /* Button-group layout:
     * outer pill = PILL_SIZE high, inner buttons = BUTTON_SIZE high,
     * inset by BUTTON_MARGIN, positioned PADDING from screen edges. */
    TTF_Font *hint_font = cat_get_font(CAT_FONT_SMALL); /* hint text uses the small tier */
    if (!hint_font) return;

    int padding    = CAT_DS(cat__g.device_padding);      /* screen-edge padding */
    int outer_h    = CAT_DS(CAT__PILL_SIZE);              /* outer pill height */
    int btn_margin = CAT_DS(CAT__BUTTON_MARGIN);          /* inset from outer → inner + inter-element */
    int inner_h    = CAT_DS(CAT__BUTTON_SIZE);             /* inner button pill height */
    int pill_y     = cat__g.screen_h - CAT_DS(cat__g.device_padding + CAT__PILL_SIZE); /* bottom-aligned: screen_h - scaled(PADDING + PILL_SIZE) */
    int item_gap   = btn_margin;                        /* gap between items */
    int outer_pad  = btn_margin;                        /* padding at start/end of outer pill */
    int hint_font_h = TTF_FontHeight(hint_font);
    int max_total_w = cat__g.screen_w - padding * 2;
    if (max_total_w < 0) max_total_w = 0;

    int left_indices[CAT__MAX_FOOTER_ITEMS];
    int left_widths[CAT__MAX_FOOTER_ITEMS];
    int right_indices[CAT__MAX_FOOTER_ITEMS];
    int right_widths[CAT__MAX_FOOTER_ITEMS];

    int left_count = 0;
    int right_count = 0;
    for (int i = 0; i < count; i++) {
        int width = cat__footer_item_width(&items[i], hint_font, btn_margin);
        if (items[i].is_confirm) {
            right_indices[right_count] = i;
            right_widths[right_count] = width;
            right_count++;
        } else {
            left_indices[left_count] = i;
            left_widths[left_count] = width;
            left_count++;
        }
    }

    int right_visible_start = 0;
    int right_visible_count = right_count;
    int right_outer_w = cat__footer_group_outer_width(right_widths, right_count, item_gap, outer_pad, 0);

    if (cat__g.footer_overflow_opts.enabled && right_count > 0 && right_outer_w > max_total_w) {
        int inner = 0;
        right_visible_start = right_count;
        right_visible_count = 0;

        for (int i = right_count - 1; i >= 0; i--) {
            int candidate = right_widths[i];
            if (right_visible_count > 0) candidate += item_gap;
            if (outer_pad + inner + candidate + outer_pad <= max_total_w || right_visible_count == 0) {
                inner += candidate;
                right_visible_start = i;
                right_visible_count++;
            }
        }

        right_outer_w = (right_visible_count > 0) ? (outer_pad + inner + outer_pad) : 0;
        for (int i = 0; i < right_visible_start; i++) {
            cat__footer_store_hidden_item(&items[right_indices[i]]);
        }
    }

    int available_left_w = max_total_w - right_outer_w;
    if (available_left_w < 0) available_left_w = 0;

    int left_visible_count = left_count;

    if (cat__g.footer_overflow_opts.enabled) {
        int best_visible = -1;

        for (int visible = 0; visible <= left_count; visible++) {
            int hidden_total = cat__g.footer_hidden_count + (left_count - visible);
            int candidate_marker_w = cat__footer_marker_width(hidden_total, hint_font, btn_margin);
            int outer_w = cat__footer_group_outer_width(left_widths, visible, item_gap, outer_pad,
                                                       hidden_total > 0 ? candidate_marker_w : 0);
            if (outer_w <= available_left_w) {
                best_visible = visible;
            }
        }

        if (best_visible < 0) {
            left_visible_count = 0;
        } else {
            left_visible_count = best_visible;
        }

        for (int i = left_visible_count; i < left_count; i++) {
            cat__footer_store_hidden_item(&items[left_indices[i]]);
        }
        if (cat__g.footer_hidden_count <= 0) {
            cat__g.footer_overflow_active = false;
        }
    }

    int left_outer_w = cat__footer_group_outer_width(left_widths, left_visible_count, item_gap, outer_pad,
                                                    (cat__g.footer_hidden_count > 0 && cat__g.footer_overflow_opts.enabled)
                                                        ? cat__footer_marker_width(cat__g.footer_hidden_count, hint_font, btn_margin)
                                                        : 0);

    if (!cat__g.footer_overflow_opts.enabled) {
        left_outer_w = cat__footer_group_outer_width(left_widths, left_count, item_gap, outer_pad, 0);
        right_outer_w = cat__footer_group_outer_width(right_widths, right_count, item_gap, outer_pad, 0);
        left_visible_count = left_count;
        right_visible_start = 0;
        right_visible_count = right_count;
    }

    if (left_outer_w > 0) {
        cat_draw_pill(padding, pill_y, left_outer_w, outer_h, cat__footer_bg(cat__g.theme.accent));
        int cx = padding + outer_pad;
        int btn_y = pill_y + btn_margin; /* buttons inset by BUTTON_MARGIN from pill top */
        for (int i = 0; i < left_visible_count; i++) {
            if (i > 0) cx += item_gap;
            cat__footer_draw_item(&cx, btn_y, inner_h, btn_margin, hint_font_h, hint_font,
                                 &items[left_indices[i]]);
        }

        if (cat__g.footer_hidden_count > 0 && cat__g.footer_overflow_opts.enabled) {
            char marker[16];
            snprintf(marker, sizeof(marker), "+%d", cat__g.footer_hidden_count);
            if (left_visible_count > 0) cx += item_gap;
            cat_draw_text(hint_font, marker,
                         cx + btn_margin,
                         btn_y + (inner_h - hint_font_h) / 2,
                         cat__g.theme.hint);
        }
    }

    if (right_visible_count > 0 && right_outer_w > 0) {
        int rx = cat__g.screen_w - padding - right_outer_w;
        cat_draw_pill(rx, pill_y, right_outer_w, outer_h, cat__footer_bg(cat__g.theme.accent));
        int cx = rx + outer_pad;
        int btn_y = pill_y + btn_margin;
        for (int i = right_visible_start; i < right_count; i++) {
            if (i > right_visible_start) cx += item_gap;
            cat__footer_draw_item(&cx, btn_y, inner_h, btn_margin, hint_font_h, hint_font,
                                 &items[right_indices[i]]);
        }
    }

    cat__g.footer_bg_opacity = 0;   /* one-shot: never leak to the next footer */
}

static void cat__footer_overflow_show_hidden_actions(void) {
    if (cat__g.footer_hidden_count <= 0) return;

    TTF_Font *title_font = cat_get_font(CAT_FONT_SMALL);
    TTF_Font *body_font = cat_get_font(CAT_FONT_TINY);
    TTF_Font *hint_font = cat_get_font(CAT_FONT_MICRO);
    if (!body_font) return;

    char text[2048];
    int off = 0;
    for (int i = 0; i < cat__g.footer_hidden_count && off < (int)sizeof(text) - 1; i++) {
        const char *btn_name = cat__footer_button_text(&cat__g.footer_hidden_items[i]);
        const char *label = cat__g.footer_hidden_items[i].label ? cat__g.footer_hidden_items[i].label : "";
        off += snprintf(text + off, sizeof(text) - (size_t)off,
                        "%s  %.120s\n", btn_name, label);
        if (off < 0 || off >= (int)sizeof(text)) {
            text[sizeof(text) - 1] = '\0';
            break;
        }
    }

    cat__g.footer_overflow_overlay_open = true;

    cat_theme *theme = cat_get_theme();
    int screen_w = cat_get_screen_width();
    int screen_h = cat_get_screen_height();
    int margin = CAT_S(40);
    int title_h = title_font ? TTF_FontLineSkip(title_font) : 0;
    int line_h = TTF_FontLineSkip(body_font);
    int body_y = margin + title_h + CAT_S(12);
    int max_w = screen_w - margin * 2;
    int body_h = screen_h - body_y - margin;
    if (body_h < line_h) body_h = line_h;

    int chars_per_line = max_w / CAT_S(10);
    if (chars_per_line < 1) chars_per_line = 1;
    int est_lines = ((int)strlen(text) / chars_per_line) + 2;
    int content_h = est_lines * line_h;
    int scroll = 0;
    int max_scroll = content_h - body_h;
    if (max_scroll < 0) max_scroll = 0;

    bool running = true;
    while (running) {
        cat_input_event ev;
        while (cat_poll_input(&ev)) {
            if (!ev.pressed) continue;
            switch (ev.button) {
                case CAT_BTN_UP:
                    scroll -= CAT_S(40);
                    if (scroll < 0) scroll = 0;
                    break;
                case CAT_BTN_DOWN:
                    scroll += CAT_S(40);
                    if (scroll > max_scroll) scroll = max_scroll;
                    break;
                default:
                    running = false;
                    break;
            }
        }

        cat_draw_color overlay_bg = {0, 0, 0, 220};
        cat_draw_rect(0, 0, screen_w, screen_h, overlay_bg);

        if (title_font) {
            cat_draw_text(title_font, "Hidden Actions", margin, margin, theme->text);
        }

        SDL_Rect clip = { margin, body_y, max_w, body_h };
        SDL_RenderSetClipRect(cat_get_renderer(), &clip);
        cat_draw_text_wrapped(body_font, text, margin, body_y - scroll, max_w, theme->text, CAT_ALIGN_LEFT);
        SDL_RenderSetClipRect(cat_get_renderer(), NULL);

        if (max_scroll > 0) {
            cat_draw_scrollbar(screen_w - margin + CAT_S(8), body_y, body_h, body_h, content_h, scroll);
        }

        if (hint_font) {
            const char *hint = "Press any button to close";
            int hint_w = cat_measure_text(hint_font, hint);
            cat_draw_text(hint_font, hint, (screen_w - hint_w) / 2,
                         screen_h - margin + CAT_S(8), theme->hint);
        }

        cat_present();
    }

    cat__g.footer_overflow_overlay_open = false;
}

static bool cat__footer_overflow_consume_open_request(void) {
    if (!cat__g.footer_overflow_open_requested) return false;

    cat__g.footer_overflow_open_requested = false;
    if (!cat__footer_overflow_enabled()) return true;

    cat__footer_overflow_show_hidden_actions();
    cat__g.last_input_time = SDL_GetTicks();
    return true;
}

int cat_get_status_bar_height(void) {
    /* padding + pill_size, scaled */
    return CAT_DS(cat__g.device_padding + CAT__PILL_SIZE);
}

void cat_set_platform_services(const cat_platform_services *services) {
    if (services) {
        cat__g.platform_services = *services;
    } else {
        memset(&cat__g.platform_services, 0, sizeof(cat__g.platform_services));
    }
#if CAT_PLATFORM_IS_DEVICE
    cat__g.cached_wifi_strength = 0;
    cat__g.wifi_cache_time_ms = 0;
#endif
}

/* ─── Device / Preview status helpers ────────────────────────────────────── */

#if CAT_PLATFORM_IS_DEVICE
static int cat__read_sysfs_int(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    int val = -1;
    if (fscanf(f, "%d", &val) != 1) val = -1;
    fclose(f);
    return val;
}
#endif

static int cat__get_battery_percent(void) {
    if (cat__g.platform_services.battery_percent) {
        int cap = cat__g.platform_services.battery_percent(cat__g.platform_services.userdata);
        if (cap >= 0 && cap <= 100) {
            return cap;
        }
    }
#if CAT_PLATFORM_IS_DEVICE
    int cap;
    #if defined(PLATFORM_MY355) || defined(PLATFORM_MLP1)
    cap = cat__read_sysfs_int("/sys/class/power_supply/battery/capacity");
    #else /* tg5040, tg5050 */
    cap = cat__read_sysfs_int("/sys/class/power_supply/axp2202-battery/capacity");
    #endif
    return (cap >= 0 && cap <= 100) ? cap : -1;
#else
    int cap = -1;
    if (!cat__env_parse_int("CAT_PREVIEW_BATTERY_PERCENT", &cap)) return -1;
    return cat__clamp(cap, 0, 100);
#endif
}

static bool cat__is_charging(void) {
    if (cat__g.platform_services.charging_state) {
        int charging = cat__g.platform_services.charging_state(cat__g.platform_services.userdata);
        if (charging >= 0) {
            return charging != 0;
        }
    }
#if CAT_PLATFORM_IS_DEVICE
    int charger, ttf;
    #if defined(PLATFORM_MLP1)
    charger = cat__read_sysfs_int("/sys/class/power_supply/ac/online");
    if (charger != 1)
        charger = cat__read_sysfs_int("/sys/class/power_supply/usb/online");
    return charger == 1;
    #elif defined(PLATFORM_MY355)
    charger = cat__read_sysfs_int("/sys/class/power_supply/ac/online");
    ttf     = cat__read_sysfs_int("/sys/class/power_supply/battery/time_to_full_now");
    #else
    charger = cat__read_sysfs_int("/sys/class/power_supply/axp2202-usb/online");
    ttf     = cat__read_sysfs_int("/sys/class/power_supply/axp2202-battery/time_to_full_now");
    #endif
    return (charger == 1) && (ttf > 0);
#else
    int charging = 0;
    return cat__env_parse_int("CAT_PREVIEW_CHARGING", &charging) && charging != 0;
#endif
}

/* ─── CPU & Fan sysfs paths and frequency presets ───────────────────────── */

#if CAT_PLATFORM_IS_DEVICE
#if defined(PLATFORM_MY355)
#  define CAT__CPU_GOVERNOR_PATH  "/sys/devices/system/cpu/cpufreq/policy0/scaling_governor"
#  define CAT__CPU_SPEED_PATH     "/sys/devices/system/cpu/cpufreq/policy0/scaling_setspeed"
#  define CAT__CPU_CUR_FREQ_PATH  "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
#  define CAT__CPU_FREQ_MENU         600000
#  define CAT__CPU_FREQ_POWERSAVE   1200000
#  define CAT__CPU_FREQ_NORMAL      1608000
#  define CAT__CPU_FREQ_PERFORMANCE 2000000
#elif defined(PLATFORM_TG5040)
#  define CAT__CPU_GOVERNOR_PATH  "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
#  define CAT__CPU_SPEED_PATH     "/sys/devices/system/cpu/cpu0/cpufreq/scaling_setspeed"
#  define CAT__CPU_CUR_FREQ_PATH  "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
#  define CAT__CPU_FREQ_MENU         600000
#  define CAT__CPU_FREQ_POWERSAVE   1200000
#  define CAT__CPU_FREQ_NORMAL      1608000
#  define CAT__CPU_FREQ_PERFORMANCE 2000000
#elif defined(PLATFORM_TG5050)
#  define CAT__CPU_GOVERNOR_PATH  "/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor"
#  define CAT__CPU_SPEED_PATH     "/sys/devices/system/cpu/cpu4/cpufreq/scaling_setspeed"
#  define CAT__CPU_CUR_FREQ_PATH  "/sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq"
#  define CAT__CPU_FREQ_MENU         672000
#  define CAT__CPU_FREQ_POWERSAVE   1200000
#  define CAT__CPU_FREQ_NORMAL      1680000
#  define CAT__CPU_FREQ_PERFORMANCE 2160000
#  define CAT__FAN_STATE_PATH     "/sys/class/thermal/cooling_device0/cur_state"
#  define CAT__FAN_HELPER_NAME    "fancontrol"
#  define CAT__FAN_LOCK_PATH      "/var/run/fan-control.lock"
#endif

#define CAT__CPU_TEMP_PATH "/sys/devices/virtual/thermal/thermal_zone0/temp"

#if defined(CAT__CPU_SPEED_PATH) || defined(CAT__FAN_STATE_PATH)
static int cat__write_sysfs_int(const char *path, int value) {
    FILE *f = fopen(path, "w");
    if (!f) return CAT_ERROR;
    fprintf(f, "%d\n", value);
    fclose(f);
    return CAT_OK;
}
#endif

#if defined(CAT__CPU_SPEED_PATH)
static int cat__write_sysfs_str(const char *path, const char *value) {
    FILE *f = fopen(path, "w");
    if (!f) return CAT_ERROR;
    fputs(value, f);
    fclose(f);
    return CAT_OK;
}
#endif

#if defined(PLATFORM_TG5050) && defined(CAT__FAN_STATE_PATH)
static bool cat__is_all_digits(const char *s) {
    if (!s || !s[0]) return false;
    for (const char *p = s; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
    }
    return true;
}

static const char *cat__fan_mode_arg(cat_fan_mode mode) {
    switch (mode) {
        case CAT_FAN_MODE_AUTO_QUIET: return "quiet";
        case CAT_FAN_MODE_AUTO_NORMAL: return "normal";
        case CAT_FAN_MODE_AUTO_PERFORMANCE: return "performance";
        default: return NULL;
    }
}

static int cat__fan_stop_helper(void) {
    (void)system("killall fancontrol 2>/dev/null");
    usleep(100000);
    unlink(CAT__FAN_LOCK_PATH);
    return CAT_OK;
}

static const char *cat__fan_helper_path(char *buf, size_t buf_size) {
    const char *explicit_path = cat__env_nonempty("CAT_FAN_HELPER_PATH");
    if (explicit_path) return explicit_path;

    char system_buf[PATH_MAX];
    const char *system_path = cat__system_path(system_buf, sizeof(system_buf));
    if (!system_path || !buf || buf_size == 0) return NULL;
    snprintf(buf, buf_size, "%s/bin/%s", system_path, CAT__FAN_HELPER_NAME);
    return buf;
}

static bool cat__fan_helper_available(void) {
    char helper_buf[PATH_MAX];
    const char *helper = cat__fan_helper_path(helper_buf, sizeof(helper_buf));
    return helper && access(helper, X_OK) == 0;
}

static int cat__fan_launch_helper(const char *arg) {
    char command[256];
    char helper_buf[PATH_MAX];
    const char *helper = cat__fan_helper_path(helper_buf, sizeof(helper_buf));
    if (!arg || !arg[0]) return CAT_ERROR;
    if (!helper) return CAT_ERROR;
    snprintf(command, sizeof(command), "%s %s >/dev/null 2>&1 &", helper, arg);
    return system(command) == 0 ? CAT_OK : CAT_ERROR;
}

static cat_fan_mode cat__fan_detect_helper_mode(void) {
    DIR *dir = opendir("/proc");
    if (!dir) return CAT_FAN_MODE_UNSUPPORTED;

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (!cat__is_all_digits(ent->d_name)) continue;

        char path[sizeof("/proc//cmdline") + sizeof(ent->d_name)];
        snprintf(path, sizeof(path), "/proc/%s/cmdline", ent->d_name);

        FILE *f = fopen(path, "r");
        if (!f) continue;

        char cmdline[256];
        size_t n = fread(cmdline, 1, sizeof(cmdline) - 1, f);
        fclose(f);
        if (n == 0) continue;
        cmdline[n] = '\0';

        const char *arg = cmdline;
        const char *found = NULL;
        const char *next = NULL;
        while (arg < cmdline + n && *arg) {
            const char *base = strrchr(arg, '/');
            base = base ? base + 1 : arg;
            if (strcmp(base, "fancontrol") == 0) {
                found = arg;
                next = arg + strlen(arg) + 1;
                break;
            }
            arg += strlen(arg) + 1;
        }
        if (!found) continue;

        if (next >= cmdline + n || !*next) {
            closedir(dir);
            return CAT_FAN_MODE_MANUAL;
        }

        if (strcmp(next, "quiet") == 0) {
            closedir(dir);
            return CAT_FAN_MODE_AUTO_QUIET;
        }
        if (strcmp(next, "normal") == 0) {
            closedir(dir);
            return CAT_FAN_MODE_AUTO_NORMAL;
        }
        if (strcmp(next, "performance") == 0) {
            closedir(dir);
            return CAT_FAN_MODE_AUTO_PERFORMANCE;
        }

        closedir(dir);
        return CAT_FAN_MODE_MANUAL;
    }

    closedir(dir);
    return CAT_FAN_MODE_UNSUPPORTED;
}
#endif

#endif /* CAT_PLATFORM_IS_DEVICE */

/* ─── CPU & Fan implementations ──────────────────────────────────────────── */

int cat_set_cpu_speed(cat_cpu_speed speed) {
#if CAT_PLATFORM_IS_DEVICE && defined(CAT__CPU_SPEED_PATH)
    int freq = 0;
    switch (speed) {
        case CAT_CPU_SPEED_MENU:        freq = CAT__CPU_FREQ_MENU;        break;
        case CAT_CPU_SPEED_POWERSAVE:   freq = CAT__CPU_FREQ_POWERSAVE;   break;
        case CAT_CPU_SPEED_NORMAL:      freq = CAT__CPU_FREQ_NORMAL;      break;
        case CAT_CPU_SPEED_PERFORMANCE: freq = CAT__CPU_FREQ_PERFORMANCE; break;
        default: return CAT_ERROR;
    }
    cat__write_sysfs_str(CAT__CPU_GOVERNOR_PATH, "userspace\n");
    return cat__write_sysfs_int(CAT__CPU_SPEED_PATH, freq);
#else
    (void)speed;
    return CAT_OK;
#endif
}

int cat_get_cpu_speed_mhz(void) {
#if CAT_PLATFORM_IS_DEVICE && defined(CAT__CPU_CUR_FREQ_PATH)
    int khz = cat__read_sysfs_int(CAT__CPU_CUR_FREQ_PATH);
    return (khz > 0) ? khz / 1000 : -1;
#else
    return -1;
#endif
}

int cat_get_cpu_temp_celsius(void) {
#if CAT_PLATFORM_IS_DEVICE
    int mk = cat__read_sysfs_int(CAT__CPU_TEMP_PATH);
    return (mk > 0) ? mk / 1000 : -1;
#else
    return -1;
#endif
}

int cat_set_fan_mode(cat_fan_mode mode) {
    if (cat__g.platform_services.set_fan_mode) {
        int rc = cat__g.platform_services.set_fan_mode(cat__g.platform_services.userdata, mode);
        if (rc == CAT_OK) {
            return CAT_OK;
        }
    }
#if defined(PLATFORM_TG5050) && defined(CAT__FAN_STATE_PATH)
    const char *arg;
    if (mode == CAT_FAN_MODE_MANUAL) return cat__fan_stop_helper();

    arg = cat__fan_mode_arg(mode);
    if (!arg) return CAT_ERROR;

    cat__fan_stop_helper();
    if (!cat__fan_helper_available()) return CAT_ERROR;
    return cat__fan_launch_helper(arg);
#else
    (void)mode;
    return CAT_OK;
#endif
}

cat_fan_mode cat_get_fan_mode(void) {
    if (cat__g.platform_services.get_fan_mode) {
        cat_fan_mode mode =
            cat__g.platform_services.get_fan_mode(cat__g.platform_services.userdata);
        if (mode != CAT_FAN_MODE_UNSUPPORTED) {
            return mode;
        }
    }
#if defined(PLATFORM_TG5050) && defined(CAT__FAN_STATE_PATH)
    cat_fan_mode mode = cat__fan_detect_helper_mode();
    if (mode != CAT_FAN_MODE_UNSUPPORTED) return mode;
    return cat__read_sysfs_int(CAT__FAN_STATE_PATH) >= 0 ? CAT_FAN_MODE_MANUAL : CAT_FAN_MODE_UNSUPPORTED;
#else
    return CAT_FAN_MODE_UNSUPPORTED;
#endif
}

int cat_set_fan_speed(int percent) {
    if (cat__g.platform_services.set_fan_speed) {
        int rc = cat__g.platform_services.set_fan_speed(cat__g.platform_services.userdata, percent);
        if (rc == CAT_OK) {
            return CAT_OK;
        }
    }
#if defined(PLATFORM_TG5050) && defined(CAT__FAN_STATE_PATH)
    if (percent < 0) return CAT_OK; /* -1 = keep current */
    if (percent > 100) percent = 100;
    cat__fan_stop_helper();
    if (cat__fan_helper_available()) {
        char arg[16];
        snprintf(arg, sizeof(arg), "%d", percent);
        if (cat__fan_launch_helper(arg) == CAT_OK) return CAT_OK;
    }
    return cat__write_sysfs_int(CAT__FAN_STATE_PATH, (31 * percent + 50) / 100);
#else
    (void)percent;
    return CAT_OK;
#endif
}

int cat_get_fan_speed(void) {
    if (cat__g.platform_services.get_fan_speed) {
        int percent = cat__g.platform_services.get_fan_speed(cat__g.platform_services.userdata);
        if (percent >= 0) {
            return cat__clamp(percent, 0, 100);
        }
    }
#if defined(PLATFORM_TG5050) && defined(CAT__FAN_STATE_PATH)
    int raw = cat__read_sysfs_int(CAT__FAN_STATE_PATH);
    return (raw >= 0) ? raw * 100 / 31 : -1;
#else
    return 0;
#endif
}

#if CAT_PLATFORM_IS_DEVICE
    #define CAT__WIFI_CACHE_TTL_MS 5000  /* 5-second polling interval */

    static int cat__map_rssi_to_wifi_strength(int rssi) {
        /* Signal-strength thresholds:
           0   = disconnected
           -60 = high
           -70 = med
           else low */
        if (rssi == 0)    return 0;
        if (rssi >= -60)  return 3;
        if (rssi >= -70)  return 2;
        return 1;
    }

    static int cat__read_wifi_rssi_iw(void) {
        const int unavailable = -10000;
        FILE *f = popen("iw dev wlan0 link 2>/dev/null", "r");
        if (!f) return unavailable;

        char line[256];
        int rssi = unavailable;
        bool disconnected = false;

        while (fgets(line, sizeof(line), f)) {
            int val;
            if (sscanf(line, " signal: %d dBm", &val) == 1 ||
                sscanf(line, "signal: %d dBm", &val) == 1) {
                rssi = val;
                break;
            }
            if (strstr(line, "Not connected") != NULL) {
                disconnected = true;
            }
        }

        int rc = pclose(f);
        if (rssi != unavailable) return rssi;
        if (disconnected) return 0;
        if (rc == -1) return unavailable;
        if (WIFEXITED(rc) && WEXITSTATUS(rc) != 0) return unavailable;
        return unavailable;
    }

    static int cat__read_wifi_rssi_wpa_cli(void) {
        const int unavailable = -10000;
        static const char *cmds[] = {
            "wpa_cli -p /var/run/wpa_supplicant -i wlan0 signal_poll 2>/dev/null",
            "/usr/sbin/wpa_cli -p /var/run/wpa_supplicant -i wlan0 signal_poll 2>/dev/null",
            "wpa_cli -p /etc/wifi/sockets -i wlan0 signal_poll 2>/dev/null",
            "/usr/sbin/wpa_cli -p /etc/wifi/sockets -i wlan0 signal_poll 2>/dev/null",
            "wpa_cli -i wlan0 signal_poll 2>/dev/null",
            "/usr/sbin/wpa_cli -i wlan0 signal_poll 2>/dev/null",
            NULL
        };

        for (int i = 0; cmds[i]; i++) {
            FILE *f = popen(cmds[i], "r");
            if (!f) continue;

            int rssi = unavailable;
            char line[256];
            while (fgets(line, sizeof(line), f)) {
                int val;
                if (sscanf(line, "RSSI=%d", &val) == 1) {
                    rssi = val;
                    break;
                }
            }

            int rc = pclose(f);
            if (rssi != unavailable) return rssi;
            if (rc == -1) continue;
            if (WIFEXITED(rc) && WEXITSTATUS(rc) == 0) {
                /* Command worked but did not report RSSI.
                   Treat as disconnected rather than unavailable. */
                return 0;
            }
        }

        return unavailable;
    }
#endif

/* Returns wifi signal strength: 0=off, 1=low, 2=med, 3=high
   Uses iw first; falls back to wpa_cli signal_poll on my355 where iw may be unavailable.
   Results are cached for CAT__WIFI_CACHE_TTL_MS. */
static int cat__get_wifi_strength(void) {
#if CAT_PLATFORM_IS_DEVICE
    uint32_t now = SDL_GetTicks();

    /* Return cached value if within TTL window */
    if (cat__g.wifi_cache_time_ms != 0 &&
        (now - cat__g.wifi_cache_time_ms) < CAT__WIFI_CACHE_TTL_MS) {
        return cat__g.cached_wifi_strength;
    }

    const int unavailable = -10000;
    int result;

    if (cat__g.platform_services.wifi_strength) {
        int supplied = cat__g.platform_services.wifi_strength(cat__g.platform_services.userdata);
        if (supplied >= 0) {
            result = cat__clamp(supplied, 0, 3);
            goto cache;
        }
    }

    /* Check if interface is up */
    FILE *f = fopen("/sys/class/net/wlan0/operstate", "r");
    if (!f) { result = 0; goto cache; }
    char state[16] = {0};
    if (fgets(state, sizeof(state), f)) {
        char *nl = strchr(state, '\n');
        if (nl) *nl = '\0';
    }
    fclose(f);
    if (strcmp(state, "up") != 0) { result = 0; goto cache; }

    /* Read signal via wpa_cli signal_poll first so the status-bar icon matches
       the Network settings page (which reads the same source); fall back to iw
       only on devices where wpa_cli is unavailable. (iw and signal_poll can
       report RSSI that differs by enough to cross a strength bucket — e.g. iw
       ~-65 → 2 bars vs signal_poll -80 → 1 bar — so they must share one source.) */
    int rssi = cat__read_wifi_rssi_wpa_cli();
    if (rssi == unavailable) {
        rssi = cat__read_wifi_rssi_iw();
    }

    /* Keep icon visible when interface is up but RSSI source is unavailable */
    result = (rssi == unavailable) ? 1 : cat__map_rssi_to_wifi_strength(rssi);

cache:
    cat__g.cached_wifi_strength = result;
    cat__g.wifi_cache_time_ms = now;
    return result;
#else
    if (cat__g.platform_services.wifi_strength) {
        int supplied = cat__g.platform_services.wifi_strength(cat__g.platform_services.userdata);
        if (supplied >= 0) {
            return cat__clamp(supplied, 0, 3);
        }
    }
    int strength = 0;
    if (!cat__env_parse_int("CAT_PREVIEW_WIFI_STRENGTH", &strength)) return 0;
    return cat__clamp(strength, 0, 3);
#endif
}

/* ─── Status bar icon blitting ───────────────────────────────────────────── */

/* Blit a colored icon from the status asset spritesheet.
   src_x/y/w/h are at 1x scale; they are multiplied by status_asset_scale. */
static void cat__blit_status_icon(int src_x, int src_y, int src_w, int src_h,
                                  int dst_x, int dst_y, int dst_w, int dst_h,
                                  cat_draw_color tint) {
    if (!cat__g.status_assets) return;
    int s = cat__g.status_asset_scale;
    SDL_Rect src = { src_x * s, src_y * s, src_w * s, src_h * s };
    SDL_Rect dst = { dst_x, dst_y, dst_w, dst_h };
    SDL_SetTextureColorMod(cat__g.status_assets, tint.r, tint.g, tint.b);
    SDL_SetTextureAlphaMod(cat__g.status_assets, tint.a);
    SDL_RenderCopy(cat__g.renderer, cat__g.status_assets, &src, &dst);
}

int cat_device_icon_px(void) {
    return cat__g.status_assets ? (12 * cat__g.status_asset_scale) : 0;
}

int cat_draw_device_icon(cat_device_icon icon, int x, int y, cat_draw_color color) {
    if (!cat__g.status_assets) {
        return 0;
    }
    /* 1x atlas slots (see scripts/generate_assets_atlas.py). */
    int src_x = 53, src_y = 104;             /* ASSET_BLUETOOTH (generic) */
    if (icon == CAT_DEVICE_ICON_HEADSET) {
        src_x = 79;                          /* ASSET_AUDIO (headphones) */
    } else if (icon == CAT_DEVICE_ICON_CONTROLLER) {
        src_x = 92;                          /* ASSET_CONTROLLER (gamepad) */
    }
    int px = 12 * cat__g.status_asset_scale;
    cat__blit_status_icon(src_x, src_y, 12, 12, x, y, px, px, color);
    return px;
}

/* ─── Status bar ─────────────────────────────────────────────────────────── */

/* Icon sizes at 1x logical (from the status spritesheet).
   Actual pixel size = these × status_asset_scale (matched to loaded assets@Nx.png). */
#define CAT__BATTERY_W  17
#define CAT__BATTERY_H  10
#define CAT__WIFI_SIZE  12
#define CAT__VOLUME_SIZE 12
#define CAT__BT_SIZE    12

/* Helper: icon pixel size using the loaded spritesheet scale (1:1, no GPU upscaling) */
#define CAT__ICON_PX(logical) ((logical) * cat__g.status_asset_scale)

typedef struct {
    bool use_sprite_layout;
    bool wifi_visible;
    bool bt_visible;
    bool volume_visible;
    bool battery_visible;
    bool battery_level_visible;   /* numeric "85%" next to the battery icon */
    bool clock_visible;
    bool clock_24h;
    bool clock_no_ampm;
    int  wifi_strength;
    int  bt_state;                /* 0=off, 1=on, 2=connected */
    int  visible_icon_count;
    bool single_icon_sprite_mode;
    bool alert_visible;
} cat__status_bar_layout;

/* Resolve status-bar visibility once so width and draw logic stay in sync. */
static inline cat__status_bar_layout cat__resolve_status_bar_layout(const cat_status_bar_opts *opts) {
    cat__status_bar_layout layout = {0};
    if (!opts) return layout;

    layout.use_sprite_layout    = (cat__g.status_assets != NULL);
    layout.battery_visible      = opts->show_battery;        /* the sprite icon */
    layout.battery_level_visible = opts->show_battery_level;  /* the "85%" number — independent of the icon */
    layout.clock_24h            = opts->use_24h;
    layout.clock_no_ampm        = opts->no_ampm;

    if (opts->show_wifi) {
        /* Prefer the caller-supplied strength (an app that owns the radio read
           keeps the icon and its own Wi-Fi UI on one source). Fall back to the
           built-in reader only when no value is supplied (preview/legacy apps). */
        layout.wifi_strength = opts->wifi_supplied ? opts->wifi_strength
                                                   : cat__get_wifi_strength();
        layout.wifi_visible  = (layout.wifi_strength > 0);
    }

    /* Bluetooth is always shown when enabled; the icon dims when the radio is
       off (bt_state 0) and is solid when on/connected (bt_state >= 1). The
       caller owns the radio read and supplies bt_state. */
    layout.bt_visible = opts->show_bluetooth;
    layout.bt_state   = opts->bt_state;

    /* Volume can't be read here (it lives in the platform daemon); the caller
       supplies volume_percent and we render the matching speaker sprite. */
    layout.volume_visible = opts->show_volume && opts->volume_percent >= 0;

    if (opts->show_clock == CAT_CLOCK_SHOW) {
        layout.clock_visible = true;
    } else if (opts->show_clock == CAT_CLOCK_AUTO) {
        /* AUTO: follow the active stylesheet's status_bar.show_clock */
        layout.clock_visible = cat__g.stylesheet.status_bar.show_clock;
        if (layout.clock_visible)
            layout.clock_24h = opts->use_24h;
    }

    if (layout.wifi_visible)    layout.visible_icon_count++;
    if (layout.bt_visible)      layout.visible_icon_count++;
    if (layout.volume_visible)  layout.visible_icon_count++;
    if (layout.battery_visible) layout.visible_icon_count++;

    /* The numeric battery text counts as extra content, so a battery-only bar
       with the percentage on no longer qualifies for the centered single-icon
       pill — it falls through to the left-to-right multi-element layout. */
    layout.alert_visible = opts->alert_text && opts->alert_text[0];
    layout.single_icon_sprite_mode =
        layout.use_sprite_layout && !layout.clock_visible &&
        layout.visible_icon_count == 1 && !layout.battery_level_visible &&
        !layout.alert_visible;

    return layout;
}

/* Format the battery percentage for the status bar (e.g. "85%"). Returns the
   percent (>=0), or -1 when unknown — in which case buf is emptied and the
   caller should draw the icon alone. */
static int cat__battery_level_text(char *buf, size_t n) {
    int bat = cat__get_battery_percent();
    if (bat < 0) { if (n) buf[0] = '\0'; return -1; }
    snprintf(buf, n, "%d%%", bat);
    return bat;
}

/* Format the status-bar clock. 24-hour keeps its leading zero (e.g. 09:05);
   12-hour modes drop the leading hour zero (e.g. 9:05, or 9:05 PM). */
static void cat__format_clock(char *buf, size_t n, bool clock_24h, bool no_ampm) {
    time_t now = time(NULL);
    struct tm *t = localtime(&now);
    if (clock_24h) {
        strftime(buf, n, "%H:%M", t);
    } else {
        int hour = t->tm_hour % 12;
        if (hour == 0) hour = 12;
        if (no_ampm)
            snprintf(buf, n, "%d:%02d", hour, t->tm_min);
        else
            snprintf(buf, n, "%d:%02d %s", hour, t->tm_min, t->tm_hour < 12 ? "AM" : "PM");
    }
}

static int cat__measure_status_bar_width(const cat_status_bar_opts *opts, TTF_Font *font,
                                        const cat__status_bar_layout *layout) {
    if (!opts || !layout) return 0;

    int s = cat__g.device_scale ? cat__g.device_scale : 2;
    int margin = CAT_DS(CAT__BUTTON_MARGIN);
    int total_w = margin;
    bool has_any = false;

    /* Alert text sits in its own inner pill: padding on both sides plus the gap. */
    if (layout->alert_visible && font) {
        total_w += cat_measure_text(font, opts->alert_text) + margin * 3;
        has_any = true;
    }

    if (layout->wifi_visible) {
        int wifi_w = layout->use_sprite_layout ? (CAT__WIFI_SIZE * s)
                                               : (font ? cat_measure_text(font, "WiFi") : CAT__WIFI_SIZE * s);
        total_w += wifi_w + margin;
        has_any = true;
    }

    if (layout->bt_visible) {
        int bt_w = layout->use_sprite_layout ? (CAT__BT_SIZE * s)
                                             : (font ? cat_measure_text(font, "BT") : CAT__BT_SIZE * s);
        total_w += bt_w + margin;
        has_any = true;
    }

    if (layout->volume_visible) {
        int volume_w = layout->use_sprite_layout ? (CAT__VOLUME_SIZE * s)
                                                 : (font ? cat_measure_text(font, "VOL") : CAT__VOLUME_SIZE * s);
        total_w += volume_w + margin;
        has_any = true;
    }

    if (layout->battery_visible || layout->battery_level_visible) {
        bool drew = false;
        if (layout->battery_visible) {
            int battery_w = layout->use_sprite_layout ? (CAT__BATTERY_W * s)
                                                      : (font ? cat_measure_text(font, "BAT") : CAT__BATTERY_W * s);
            total_w += battery_w;
            drew = true;
        }
        /* Size to the actual number so it sits tight against the clock. The pill
           tracks the digit count, just like the clock's changing minutes. A tight
           gap groups the number with its icon when both are shown. */
        if (layout->battery_level_visible && font) {
            char level_text[8];
            if (cat__battery_level_text(level_text, sizeof(level_text)) >= 0) {
                if (drew) total_w += (margin > 1) ? margin / 2 : 1;
                total_w += cat_measure_text(font, level_text);
            }
        }
        total_w += margin;
        has_any = true;
    }

    if (layout->clock_visible && font) {
        char clock_text[32];
        cat__format_clock(clock_text, sizeof(clock_text), layout->clock_24h, layout->clock_no_ampm);
        total_w += cat_measure_text(font, clock_text) + margin;
        has_any = true;
    }

    if (!has_any) return 0;
    if (layout->single_icon_sprite_mode) return CAT_DS(CAT__PILL_SIZE);
    return total_w;
}

static void cat__draw_status_bar_wifi_sprite(int x, int y, int wifi_strength) {
    int s = cat__g.device_scale ? cat__g.device_scale : 2;
    int iw = CAT__WIFI_SIZE * s;
    int ih = CAT__WIFI_SIZE * s;
    int sx;

    switch (wifi_strength) {
        case 3:  sx = 1;  break;  /* high */
        case 2:  sx = 14; break;  /* med */
        case 1:  sx = 27; break;  /* low */
        default: sx = 40; break;  /* off/disconnected */
    }

    cat__blit_status_icon(sx, 104, CAT__WIFI_SIZE, CAT__WIFI_SIZE,
                         x, y, iw, ih, cat__g.theme.hint);
}

/* Bluetooth rune. Always drawn when enabled; dimmed when the radio is off
   (bt_state 0), solid when on/connected (bt_state >= 1). The glyph lives at
   atlas base (53,104) - generate_assets_atlas.py's "bluetooth" slot; the
   sheets are generator output, so coordinates must match the script. */
static void cat__draw_status_bar_bluetooth_sprite(int x, int y, int bt_state) {
    int s = cat__g.device_scale ? cat__g.device_scale : 2;
    int iw = CAT__BT_SIZE * s;
    int ih = CAT__BT_SIZE * s;
    cat_draw_color tint = cat__g.theme.hint;
    if (bt_state <= 0)
        tint.a = (uint8_t)((int)tint.a * 2 / 5);  /* off: dim to ~40% */
    cat__blit_status_icon(53, 104, CAT__BT_SIZE, CAT__BT_SIZE,
                         x, y, iw, ih, tint);
}

void cat_draw_bluetooth_icon(int x, int y, int w, int h, cat_draw_color tint) {
    cat__blit_status_icon(53, 104, CAT__BT_SIZE, CAT__BT_SIZE, x, y, w, h, tint);
}

/* Speaker sprite, chosen by volume level: mute (<=0), low (1-50), high (51-100).
   Like wifi, the dimmer arcs are baked into the atlas as gray and the theme.hint
   colormod preserves the brightness ratio. */
static void cat__draw_status_bar_volume_sprite(int x, int y, int volume_percent) {
    int s = cat__g.device_scale ? cat__g.device_scale : 2;
    int iw = CAT__VOLUME_SIZE * s;
    int ih = CAT__VOLUME_SIZE * s;
    int sx;
    if (volume_percent <= 0)        sx = 90;  /* mute slash */
    else if (volume_percent <= 33)  sx = 77;  /* low: 1 arc bright */
    else if (volume_percent <= 66)  sx = 64;  /* med: 2 arcs bright */
    else                            sx = 51;  /* high: 3 arcs bright */

    cat__blit_status_icon(sx, 88, CAT__VOLUME_SIZE, CAT__VOLUME_SIZE,
                         x, y, iw, ih, cat__g.theme.hint);
}

static void cat__draw_status_bar_battery_sprite(int x, int y, TTF_Font *font) {
    int s = cat__g.device_scale ? cat__g.device_scale : 2;
    int iw = CAT__BATTERY_W * s;
    int ih = CAT__BATTERY_H * s;
    int bat = cat__get_battery_percent();
    bool charging = cat__is_charging();
    (void)font;

    /* Interior cavity for the fill bar. The terminal nub shifts the interior left
       by ~1px, so the bar sits at +2 (not +3) to stay centered. */
    int cav_x = x + 2 * s, cav_y = y + 2 * s, cav_w = 12 * s, cav_h = 6 * s;
    cat_draw_color charge_green = { 0x4C, 0xD9, 0x64, 0xFF };
    cat_draw_color low_red      = { 0xFF, 0x3B, 0x30, 0xFF };

    if (charging) {
        cat__blit_status_icon(47, 51, CAT__BATTERY_W, CAT__BATTERY_H,
                             x, y, iw, ih, cat__g.theme.hint);
        int lvl = bat < 0 ? 0 : (bat > 100 ? 100 : bat);
        if (lvl >= 100) {
            /* Fully charged: solid green, static — the sweep would imply it's
               still filling, so stop animating once it tops off. */
            cat_draw_rect(cav_x, cav_y, cav_w, cav_h, charge_green);
            return;
        }
        /* Solid green = the CURRENT charge (a real gauge, not a 0..100 sweep). */
        int base_w = cav_w * lvl / 100;
        if (base_w > 0) cat_draw_rect(cav_x, cav_y, base_w, cav_h, charge_green);
        /* A translucent green wave fills only the REMAINING (uncharged) portion,
           rising from the current level toward full and looping — reads as
           "charging from here to 100". Self-drives the next frame so it animates
           without the app polling. */
        uint32_t period = 2000u;                        /* ~2s per wave */
        uint32_t phase  = SDL_GetTicks() % period;
        int span = 100 - lvl;                           /* uncharged headroom */
        int wave_pct = lvl + (int)(phase * (uint32_t)span / period);  /* lvl..~100 */
        int wave_w = cav_w * wave_pct / 100;
        if (wave_w > base_w) {
            cat_draw_color charge_soft = { 0x4C, 0xD9, 0x64, 0x70 };
            cat_draw_rect(cav_x + base_w, cav_y, wave_w - base_w, cav_h, charge_soft);
        }
        /* Each requested frame forces a full launcher re-render (~5% of one core),
           so keep the cadence gentle — 500ms (~2fps) still reads as "filling"
           while keeping idle-charging load to ~10% (a 60ms/~16fps loop cost ~45%). */
        cat_request_frame_in(500);
        return;
    }

    bool low = (bat >= 0 && bat <= 10);
    if (low) {
        /* Flash the battery red when critically low (~0.6s on, 0.6s normal). */
        bool on = (SDL_GetTicks() % 1200u) < 700u;
        cat_draw_color frame_c = on ? low_red : cat__g.theme.hint;
        cat__blit_status_icon(66, 51, CAT__BATTERY_W, CAT__BATTERY_H,
                             x, y, iw, ih, frame_c);
        if (on && bat > 0) {
            int fill_w = cav_w * bat / 100;
            if (fill_w < s) fill_w = s;
            cat_draw_rect(cav_x, cav_y, fill_w, cav_h, low_red);
        }
        /* Red low-battery blink toggles at ~700ms/1200ms boundaries; 250ms catches
           the toggle promptly without the every-120ms full re-render. */
        cat_request_frame_in(250);
        return;
    }

    /* Normal: themed sprite fill scaled to the charge level, anchored LEFT so it
       drains/fills left-to-right (empty space on the right, like a real battery). */
    cat__blit_status_icon(47, 51, CAT__BATTERY_W, CAT__BATTERY_H,
                         x, y, iw, ih, cat__g.theme.hint);
    if (bat >= 0) {
        int fill_src_x = (bat <= 20) ? 1 : 81;
        int fill_src_y = (bat <= 20) ? 55 : 33;
        int fill_full_w = 12 * s;
        int fill_h_px = 6 * s;
        int fill_w = fill_full_w * bat / 100;
        if (fill_w > 0) {
            SDL_Rect fsrc = { fill_src_x * s, fill_src_y * s, fill_w, fill_h_px };
            SDL_Rect fdst = { cav_x, cav_y, fill_w, fill_h_px };
            SDL_SetTextureColorMod(cat__g.status_assets, cat__g.theme.hint.r, cat__g.theme.hint.g, cat__g.theme.hint.b);
            SDL_SetTextureAlphaMod(cat__g.status_assets, cat__g.theme.hint.a);
            SDL_RenderCopy(cat__g.renderer, cat__g.status_assets, &fsrc, &fdst);
        }
    }
}

/* Calculate the rendered pixel width of the status bar pill.
   Layout: BUTTON_MARGIN between each element. */
int cat_get_status_bar_width(cat_status_bar_opts *opts) {
    if (!opts) return 0;

    TTF_Font *font = cat_get_font(CAT_FONT_SMALL);
    cat__status_bar_layout layout = cat__resolve_status_bar_layout(opts);
    return cat__measure_status_bar_width(opts, font, &layout);
}

bool cat_status_bar_from_env(cat_status_bar_opts *out) {
    if (!out) return false;
    memset(out, 0, sizeof(*out));

    /* Visibility toggles (default to launcher defaults when unset). */
    out->show_wifi          = cat__env_flag("CAT_STATUS_SHOW_WIFI", true);
    out->show_battery       = cat__env_flag("CAT_STATUS_SHOW_BATTERY", true);
    out->show_battery_level = cat__env_flag("CAT_STATUS_SHOW_BATTERY_LEVEL", false);
    out->show_bluetooth     = cat__env_flag("CAT_STATUS_SHOW_BLUETOOTH", true);
    /* No live BT read in an app; the launcher exports a state snapshot. */
    const char *bt_state_env = cat__env_nonempty("CAT_STATUS_BT_STATE");
    out->bt_state           = bt_state_env ? atoi(bt_state_env) : 0;

    /* Clock token: hide / 12 / 24 / no-ampm (default 24h). */
    const char *clock = cat__env_nonempty("CAT_STATUS_CLOCK");
    if (!clock) clock = "24";
    if (strcmp(clock, "hide") == 0) {
        out->show_clock = CAT_CLOCK_HIDE;
    } else if (strcmp(clock, "12") == 0) {
        out->show_clock = CAT_CLOCK_SHOW;
        out->use_24h = false;
    } else if (strcmp(clock, "no-ampm") == 0) {
        out->show_clock = CAT_CLOCK_SHOW;
        out->use_24h = false;
        out->no_ampm = true;
    } else { /* "24" and any unrecognized value */
        out->show_clock = CAT_CLOCK_SHOW;
        out->use_24h = true;
    }

    /* Apps self-read wifi/battery/clock live (occasional cached read, no hot
       loop); volume has no live source in an app, so it is never shown. */
    out->wifi_supplied  = false;
    out->show_volume    = false;
    out->volume_percent = -1;

    /* Draw the bar if anything is enabled — battery LEVEL counts on its own
       (Battery="Percent" stores show_battery=0, show_battery_level=1). */
    return out->show_wifi || out->show_bluetooth || out->show_battery ||
           out->show_battery_level || out->show_clock != CAT_CLOCK_HIDE;
}

bool cat_hints_enabled_from_env(void) {
    return cat__env_flag("CAT_SHOW_HINTS", true);
}

void cat_draw_status_bar(cat_status_bar_opts *opts) {
    if (!opts) return;

    TTF_Font *font = cat_get_font(CAT_FONT_SMALL);
    if (!font) return;

    int padding = CAT_DS(cat__g.device_padding); /* outer UI padding */
    int margin = CAT_DS(CAT__BUTTON_MARGIN);     /* inter-element gap inside pill */
    int s = cat__g.device_scale ? cat__g.device_scale : 2;
    cat__status_bar_layout layout = cat__resolve_status_bar_layout(opts);

    int pill_w = cat__measure_status_bar_width(opts, font, &layout);
    if (pill_w <= 0) return;

    int pill_h = CAT_DS(CAT__PILL_SIZE);
    int pill_y = opts->use_y ? opts->y_position : padding;
    /* Right inset. For the inline (no-pill) status bar drawn over the tab bar,
       land the last element's right edge at screen_w - CAT_S(16) so the status
       cluster is symmetric with the leftmost tab / page-title left inset (also
       CAT_S(16)). pill_w already carries a trailing inter-element margin, so
       subtract it here. Pill contexts keep the original outer padding. */
    int right_inset = padding;
    if (opts->no_pill) {
        right_inset = CAT_S(16) - margin;
        if (right_inset < 0) right_inset = 0;
    }
    int pill_x = cat__g.screen_w - right_inset - pill_w;

    if (!opts->no_pill)
        cat_draw_pill(pill_x, pill_y, pill_w, pill_h, cat__g.theme.accent);

    if (layout.single_icon_sprite_mode) {
        if (layout.battery_visible) {
            int iw = CAT__BATTERY_W * s;
            int ih = CAT__BATTERY_H * s;
            int bx = pill_x + (pill_h - (iw + s)) / 2; /* center with +device_scale fudge for the icon's anti-alias halo */
            int by = pill_y + (pill_h - ih) / 2;
            cat__draw_status_bar_battery_sprite(bx, by, font);
        } else if (layout.wifi_visible) {
            int iw = CAT__WIFI_SIZE * s;
            int ih = CAT__WIFI_SIZE * s;
            int wx = pill_x + (pill_h - iw) / 2;
            int wy = pill_y + (pill_h - ih) / 2;
            cat__draw_status_bar_wifi_sprite(wx, wy, layout.wifi_strength);
        } else if (layout.bt_visible) {
            int iw = CAT__BT_SIZE * s;
            int ih = CAT__BT_SIZE * s;
            int bx = pill_x + (pill_h - iw) / 2;
            int by = pill_y + (pill_h - ih) / 2;
            cat__draw_status_bar_bluetooth_sprite(bx, by, layout.bt_state);
        } else if (layout.volume_visible) {
            int iw = CAT__VOLUME_SIZE * s;
            int ih = CAT__VOLUME_SIZE * s;
            int vx = pill_x + (pill_h - iw) / 2;
            int vy = pill_y + (pill_h - ih) / 2;
            cat__draw_status_bar_volume_sprite(vx, vy, opts->volume_percent);
        }
        return;
    }

    /* Multi-element mode: render left-to-right (alert → wifi → volume → battery → clock) */
    int cx = pill_x + margin;
    int cy = pill_y;

    if (layout.alert_visible) {
        /* A warning must read on every theme and wallpaper, so it does not use
           theme colors: dark text on its own amber pill, in both bar styles. */
        static const cat_draw_color alert_bg = { 245, 166, 35, 255 };
        static const cat_draw_color alert_fg = { 28, 22, 12, 255 };
        int text_w = cat_measure_text(font, opts->alert_text);
        int text_h = TTF_FontHeight(font);
        int inner_h = text_h + CAT_DS(4);
        if (inner_h > pill_h) inner_h = pill_h;
        cat_draw_pill(cx, cy + (pill_h - inner_h) / 2, text_w + margin * 2, inner_h, alert_bg);
        cat_draw_text(font, opts->alert_text, cx + margin, cy + (pill_h - text_h) / 2, alert_fg);
        cx += text_w + margin * 3;
    }

    /* Wifi icon */
    if (layout.wifi_visible) {
        int iw = CAT__WIFI_SIZE * s;
        int ih = CAT__WIFI_SIZE * s;
        int iy = cy + (pill_h - ih) / 2;

        if (cat__g.status_assets) {
            cat__draw_status_bar_wifi_sprite(cx, iy, layout.wifi_strength);
            cx += iw + margin;
        } else {
            int text_w = cat_measure_text(font, "WiFi");
            cat_draw_text(font, "WiFi", cx, cy + (pill_h - TTF_FontHeight(font)) / 2, cat__g.theme.hint);
            cx += text_w + margin;
        }
    }

    /* Bluetooth icon */
    if (layout.bt_visible) {
        int iw = CAT__BT_SIZE * s;
        int ih = CAT__BT_SIZE * s;
        int iy = cy + (pill_h - ih) / 2;

        if (cat__g.status_assets) {
            cat__draw_status_bar_bluetooth_sprite(cx, iy, layout.bt_state);
            cx += iw + margin;
        } else {
            int text_w = cat_measure_text(font, "BT");
            cat_draw_text(font, "BT", cx, cy + (pill_h - TTF_FontHeight(font)) / 2, cat__g.theme.hint);
            cx += text_w + margin;
        }
    }

    /* Volume icon */
    if (layout.volume_visible) {
        int iw = CAT__VOLUME_SIZE * s;
        int ih = CAT__VOLUME_SIZE * s;
        int iy = cy + (pill_h - ih) / 2;

        if (cat__g.status_assets) {
            cat__draw_status_bar_volume_sprite(cx, iy, opts->volume_percent);
            cx += iw + margin;
        } else {
            int text_w = cat_measure_text(font, "VOL");
            cat_draw_text(font, "VOL", cx, cy + (pill_h - TTF_FontHeight(font)) / 2, cat__g.theme.hint);
            cx += text_w + margin;
        }
    }

    /* Battery: icon and/or "85%", in any combination (Off / Icon / % / Both) */
    if (layout.battery_visible || layout.battery_level_visible) {
        int iw = CAT__BATTERY_W * s;
        int ih = CAT__BATTERY_H * s;
        int iy = cy + (pill_h - ih) / 2;
        int ty = cy + (pill_h - TTF_FontHeight(font)) / 2;
        bool drew_icon = false;

        if (layout.battery_visible) {
            if (cat__g.status_assets) {
                cat__draw_status_bar_battery_sprite(cx, iy, font);
                cx += iw;
            } else {
                cat_draw_text(font, "BAT", cx, ty, cat__g.theme.hint);
                cx += cat_measure_text(font, "BAT");
            }
            drew_icon = true;
        }

        if (layout.battery_level_visible) {
            char level_text[8];
            if (cat__battery_level_text(level_text, sizeof(level_text)) >= 0) {
                if (drew_icon) cx += (margin > 1) ? margin / 2 : 1;
                cat_draw_text(font, level_text, cx, ty, cat__g.theme.hint);
                cx += cat_measure_text(font, level_text);   /* actual width — sits tight against the clock */
            }
        }
        cx += margin;
    }

    /* Clock (rightmost) */
    {
        if (layout.clock_visible) {
            char clock_text[32];
            cat__format_clock(clock_text, sizeof(clock_text), layout.clock_24h, layout.clock_no_ampm);
            int th = TTF_FontHeight(font);
            int ty = cy + (pill_h - th) / 2;
            cat_draw_text(font, clock_text, cx, ty, cat__g.theme.hint);
        }
    }
}

/* ─── Power Button Handler ───────────────────────────────────────────────── */

#if CAT_PLATFORM_IS_DEVICE
#if !defined(PLATFORM_MY355)
static const char **cat__power_input_paths(void) {
    #if defined(PLATFORM_TG5040)
    static const char *paths[] = { "/dev/input/event1", NULL };
    #elif defined(PLATFORM_TG5050)
    static const char *paths[] = { "/dev/input/event2", NULL };
    #elif defined(PLATFORM_MLP1)
    static const char *paths[] = { NULL };
    #else
    static const char *paths[] = { "/dev/input/event1", NULL };
    #endif
    return paths;
}
#endif

/* MY355: scan all /dev/input/event* devices for the one that has KEY_POWER.
   Returns an open fd on success, -1 if no matching device found.
   Uses EVIOCGBIT to query key capabilities, matching the Linux input API. */
#if defined(PLATFORM_MY355)
static int cat__open_power_device_by_capability(void) {
    unsigned char key_bits[(KEY_MAX + 1) / 8];
    for (int i = 0; i < 16; i++) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/input/event%d", i);
        int fd = open(path, O_RDONLY | O_NONBLOCK);
        if (fd < 0) continue;
        memset(key_bits, 0, sizeof(key_bits));
        if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) >= 0) {
            /* KEY_POWER capability bit */
            if (key_bits[KEY_POWER / 8] & (1 << (KEY_POWER % 8))) {
                cat_log("Power: selected input device %s (KEY_POWER capability)", path);
                return fd;
            }
        }
        close(fd);
    }
    cat_log("Power: no /dev/input/event* device has KEY_POWER capability");
    return -1;
}
#endif

#if defined(PLATFORM_MY355)
static bool cat__is_power_key_code(int code) {
    /* Compatibility: some stacks may still expose power as 102. */
    return code == KEY_POWER || code == 102;
}
#else
static bool cat__is_power_key_code(int code) {
    return code == KEY_POWER;
}
#endif

static void cat__drain_power_events(int fd) {
    if (fd < 0) return;
    struct input_event ev;
    while (read(fd, &ev, sizeof(ev)) == sizeof(ev)) {
        if (ev.type == EV_KEY && cat__is_power_key_code((int)ev.code)) {
            cat_log("Power: draining queued key event code=%d value=%d", (int)ev.code, (int)ev.value);
        }
    }
}

#if defined(PLATFORM_MY355) || defined(PLATFORM_TG5040) || defined(PLATFORM_TG5050)
static int cat__run_power_command(const char *action, const char *command) {
    errno = 0;
    int rc = system(command);
    int saved_errno = errno;

    if (rc == -1) {
        cat_log("Power: %s command launch failed: cmd='%s' errno=%d (%s)",
               action, command, saved_errno, strerror(saved_errno));
        return rc;
    }

    if (WIFEXITED(rc)) {
        int exit_code = WEXITSTATUS(rc);
        cat_log("Power: %s command exited: cmd='%s' exit=%d", action, command, exit_code);
        return exit_code;
    }

    if (WIFSIGNALED(rc)) {
        cat_log("Power: %s command signaled: cmd='%s' signal=%d", action, command, WTERMSIG(rc));
        return rc;
    }

    cat_log("Power: %s command returned status=%d: cmd='%s'", action, rc, command);
    return rc;
}
#endif

static int cat__platform_service_power_suspend(void) {
    if (!cat__g.platform_services.power_suspend) {
        return CAT_ERROR;
    }
    return cat__g.platform_services.power_suspend(cat__g.platform_services.userdata);
}

static int cat__platform_service_power_shutdown(void) {
    if (!cat__g.platform_services.power_shutdown) {
        return CAT_ERROR;
    }
    return cat__g.platform_services.power_shutdown(cat__g.platform_services.userdata);
}

static void *cat__power_thread_func(void *arg) {
    (void)arg;

    int fd = -1;

#if defined(PLATFORM_MY355)
    /* Use EVIOCGBIT capability scan to find the device that actually has KEY_POWER */
    fd = cat__open_power_device_by_capability();
#else
    const char **input_paths = cat__power_input_paths();
    const char *opened_path = NULL;
    for (int i = 0; input_paths[i]; i++) {
        fd = open(input_paths[i], O_RDONLY | O_NONBLOCK);
        if (fd >= 0) {
            opened_path = input_paths[i];
            break;
        }
    }
    if (fd >= 0)
        cat_log("Power handler: listening on %s", opened_path ? opened_path : "unknown");
#endif

    if (fd < 0) {
        cat_log("Power handler: could not open input device");
        return NULL;
    }
    cat_log("Power handler: ready (fd=%d)", fd);
    cat__g.power_fd = fd;
    uint32_t ignore_power_until = 0;

    while (cat__g.power_thread_running) {
        struct input_event ev;
        ssize_t n = read(fd, &ev, sizeof(ev));
        if (n != sizeof(ev)) {
            SDL_Delay(10);
            continue;
        }

        if (ev.type == EV_KEY && cat__is_power_key_code((int)ev.code)) {
            uint32_t now = SDL_GetTicks();
            if (ignore_power_until && now < ignore_power_until) {
                cat_log("Power: ignoring key event during post-resume guard code=%d value=%d ms_left=%u",
                       (int)ev.code, (int)ev.value, (unsigned)(ignore_power_until - now));
                continue;
            }
            cat_log("Power: key event code=%d value=%d", (int)ev.code, (int)ev.value);
            if (ev.value == 1) { /* Press */
                /* Track press time for short/long detection */
                uint32_t press_start = SDL_GetTicks();
                bool released = false;

                while (cat__g.power_thread_running) {
                    n = read(fd, &ev, sizeof(ev));
                    if (n == sizeof(ev) && ev.type == EV_KEY &&
                        cat__is_power_key_code((int)ev.code) && ev.value == 0) {
                        cat_log("Power: key release code=%d", (int)ev.code);
                        released = true;
                        break;
                    }
                    if (SDL_GetTicks() - press_start > 1000) break;
                    SDL_Delay(10);
                }

                uint32_t held_ms = SDL_GetTicks() - press_start;
                if (held_ms >= 1000) {
                    /* Long press: shutdown */
                    cat_log("Power: long press → shutdown");
                    if (cat__platform_service_power_shutdown() != CAT_OK) {
                        system("touch /tmp/poweroff");
                    }
                    sync();
                    exit(0);
                } else if (released) {
                    /* Short press: suspend */
                    cat_log("Power: short press → suspend");
                    if (cat__platform_service_power_suspend() == CAT_OK) {
                        cat_log("Power: suspend handled by platform service");
                    } else {
                    #if defined(PLATFORM_MY355)
                    int rc = cat__run_power_command("suspend", "echo mem > /sys/power/state");
                    if (rc != 0) {
                        cat_log("Power: suspend mem failed, trying freeze fallback");
                        cat__run_power_command("suspend-fallback", "echo freeze > /sys/power/state");
                    }
                    #elif defined(PLATFORM_TG5040) || defined(PLATFORM_TG5050)
                    {
                        char system_buf[PATH_MAX];
                        const char *sp = cat__system_path(system_buf, sizeof(system_buf));
                        char suspend_cmd[256];
                        if (sp && sp[0] &&
                            snprintf(suspend_cmd, sizeof(suspend_cmd), "%s/bin/suspend", sp) <
                                (int)sizeof(suspend_cmd)) {
                            cat__run_power_command("suspend", suspend_cmd);
                        }
                    }
                    #endif
                    }

                    /* Ignore power key for a short time after resume so the wake-button
                       press itself doesn't trigger another suspend. */
                    cat__drain_power_events(fd);
                    ignore_power_until = SDL_GetTicks() + 1000;
                    cat_log("Power: resume guard active for 1000ms");
                }
            }
        }
    }

    int fd_to_close = cat__g.power_fd;
    cat__g.power_fd = -1;
    if (fd_to_close >= 0) close(fd_to_close);
    return NULL;
}
#endif

void cat_set_power_handler(bool enabled) {
    cat__g.power_handler_enabled = enabled;

#if CAT_PLATFORM_IS_DEVICE
    if (enabled && !cat__g.power_thread_running) {
        cat_log("Power handler: starting thread");
        cat__g.power_fd = -1;
        cat__g.power_thread_running = true;
        if (pthread_create(&cat__g.power_thread, NULL, cat__power_thread_func, NULL) != 0) {
            cat_log("Power handler: failed to create thread");
            cat__g.power_thread_running = false;
        }
    } else if (!enabled && cat__g.power_thread_running) {
        cat_log("Power handler: stopping thread");
        cat__g.power_thread_running = false;
        /* Thread uses non-blocking reads with a 10ms poll loop, so it will
           notice the flag change quickly.  Let the thread close its own fd
           to avoid racing with a concurrent read(). */
        pthread_join(cat__g.power_thread, NULL);
        cat_log("Power handler: thread stopped");
    }
#endif
}

/* ─── Accessors ──────────────────────────────────────────────────────────── */

SDL_Renderer *cat_get_renderer(void)   { return cat__g.renderer; }
SDL_Window   *cat_get_window(void)     { return cat__g.window; }
void cat_show_window(void) {
    if (!cat__g.window) return;
    SDL_ShowWindow(cat__g.window);
    /* Raise above any other surface (e.g. a paused RetroArch) so the freshly
       shown window takes the foreground. Cheap remap, not a re-init. */
    SDL_RaiseWindow(cat__g.window);
}
void cat_hide_window(void) { if (cat__g.window) SDL_HideWindow(cat__g.window); }
int           cat_get_screen_width(void)  { return cat__g.screen_w; }
int           cat_get_screen_height(void) { return cat__g.screen_h; }

/* ─── Initialization ─────────────────────────────────────────────────────── */

#if CAT_PLATFORM_IS_DEVICE
/* Idle wake sources in cat_present(): a bounded collection of non-grabbing,
   read-only evdev fds. The kernel marks them readable on input, so we can
   block in poll() with ~zero CPU instead of busy-polling; SDL reads the real
   events from its own independent fds. The set covers the (virtual) built-in
   pad plus any external controllers and is rebound on hotplug. Paths come
   from CAT_INPUT_WAKE_EVENTS (colon-separated list) and/or the legacy
   single-path CAT_INPUT_WAKE_EVENT; with neither set, /dev/input/event* is
   scanned for devices advertising BTN_GAMEPAD. */

static bool cat__wake_have(const char *path) {
    for (int i = 0; i < cat__g.wake_fd_count; i++) {
        if (strcmp(cat__g.wake_paths[i], path) == 0) return true;
    }
    return false;
}

static void cat__wake_add(const char *path) {
    if (!path || !path[0]) return;
    if (cat__g.wake_fd_count >= CAT__WAKE_MAX_FDS) return;
    if (cat__wake_have(path)) return;
    int fd = open(path, O_RDONLY | O_NONBLOCK);
    if (fd < 0) return;
    int i = cat__g.wake_fd_count++;
    cat__g.wake_fds[i] = fd;
    snprintf(cat__g.wake_paths[i], sizeof(cat__g.wake_paths[i]), "%s", path);
    cat_log("Input: idle poll() wake device %s", path);
}

/* Add every path in a colon-separated list (CAT_INPUT_WAKE_EVENTS format). */
static void cat__wake_add_list(const char *list) {
    if (!list || !list[0]) return;
    char buf[CAT__WAKE_PATH_MAX * CAT__WAKE_MAX_FDS + 8];
    snprintf(buf, sizeof(buf), "%s", list);
    char *tok = strtok(buf, ":");
    while (tok && cat__g.wake_fd_count < CAT__WAKE_MAX_FDS) {
        cat__wake_add(tok);
        tok = strtok(NULL, ":");
    }
}

/* Auto-scan fallback: devices advertising BTN_GAMEPAD. */
static void cat__wake_scan_gamepads(void) {
    unsigned char key_bits[(KEY_MAX + 1) / 8];
    for (int i = 0; i < 16 && cat__g.wake_fd_count < CAT__WAKE_MAX_FDS; i++) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/input/event%d", i);
        if (cat__wake_have(path)) continue;
        int fd = open(path, O_RDONLY | O_NONBLOCK);
        if (fd < 0) continue;
        memset(key_bits, 0, sizeof(key_bits));
        if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) >= 0
            && (key_bits[BTN_GAMEPAD / 8] & (1 << (BTN_GAMEPAD % 8)))) {
            int slot = cat__g.wake_fd_count++;
            cat__g.wake_fds[slot] = fd;
            snprintf(cat__g.wake_paths[slot], sizeof(cat__g.wake_paths[slot]), "%s", path);
            cat_log("Input: idle poll() wake device %s (BTN_GAMEPAD)", path);
        } else {
            close(fd);
        }
    }
}

static void cat__wake_close_all(void) {
    for (int i = 0; i < cat__g.wake_fd_count; i++) {
        if (cat__g.wake_fds[i] >= 0) close(cat__g.wake_fds[i]);
        cat__g.wake_fds[i] = -1;
        cat__g.wake_paths[i][0] = '\0';
    }
    cat__g.wake_fd_count = 0;
}

static bool cat__wake_has_overrides(void) {
    const char *multi  = getenv("CAT_INPUT_WAKE_EVENTS");
    const char *single = getenv("CAT_INPUT_WAKE_EVENT");
    return (multi && multi[0]) || (single && single[0]);
}

static void cat__wake_init(void) {
    cat__wake_close_all();
    cat__wake_add_list(getenv("CAT_INPUT_WAKE_EVENTS"));
    cat__wake_add(getenv("CAT_INPUT_WAKE_EVENT"));
    /* Same reasoning as the rescan: a pad connected before this process
       started can already be missing from an inherited list. */
    cat__wake_scan_gamepads();
    if (cat__g.wake_fd_count == 0) {
        cat_log("Input: no gamepad evdev device for poll() wake; using timed sleep");
    }
    cat__g.wake_rescan_ms = SDL_GetTicks() + CAT__WAKE_RESCAN_MS;
    cat__g.wake_dirty = false;
}

/* Light periodic rebind: close descriptors whose device node vanished
   (disconnect), reopen paths that reappeared (reconnect), and — only in
   auto-scan mode — pick up newly connected gamepads. */
static void cat__wake_rescan(void) {
    cat__g.wake_rescan_ms = SDL_GetTicks() + CAT__WAKE_RESCAN_MS;
    cat__g.wake_dirty = false;

    for (int i = 0; i < cat__g.wake_fd_count; i++) {
        if (access(cat__g.wake_paths[i], F_OK) != 0) {
            cat_log("Input: wake device %s disconnected", cat__g.wake_paths[i]);
            close(cat__g.wake_fds[i]);
            cat__g.wake_fds[i] = -1;
        }
    }
    /* Compact */
    int out = 0;
    for (int i = 0; i < cat__g.wake_fd_count; i++) {
        if (cat__g.wake_fds[i] < 0) continue;
        if (out != i) {
            cat__g.wake_fds[out] = cat__g.wake_fds[i];
            memcpy(cat__g.wake_paths[out], cat__g.wake_paths[i], sizeof(cat__g.wake_paths[out]));
        }
        out++;
    }
    cat__g.wake_fd_count = out;

    if (cat__wake_has_overrides()) {
        cat__wake_add_list(getenv("CAT_INPUT_WAKE_EVENTS"));
        cat__wake_add(getenv("CAT_INPUT_WAKE_EVENT"));
    }
    /* Always sweep for gamepads too, even when the environment named a list.
     * That list is fixed when the process starts, so a controller paired
     * afterwards is missing from it forever -- the host republishes the
     * variable, but only new children ever read it. Left to the environment
     * alone, such a pad drives the UI while being absent from the fd set we
     * sleep on, so every press waits for an unrelated wake and the UI feels a
     * beat behind. The named paths are added first and keep their priority;
     * this only fills the slots they left. */
    cat__wake_scan_gamepads();
}

/* Does this evdev event warrant a wake? Buttons and hats always; analog axes
   only past CAT_AXIS_DEADZONE so idle stick noise (wireless pads) never
   defeats sleep. Willing to wake on BTN_MODE (Guide) comes through EV_KEY. */
static bool cat__wake_event_is_activity(const struct input_event *e) {
    if (e->type == EV_KEY) return true;
    if (e->type == EV_ABS) {
        if (e->code >= ABS_HAT0X && e->code <= ABS_HAT3Y) return true;
        if (e->value > CAT_AXIS_DEADZONE || e->value < -CAT_AXIS_DEADZONE) return true;
    }
    return false;
}

/* Drain a wake-only fd; returns true if drained events include a real wake
   reason (leaving them queued would keep poll() returning immediately). */
static bool cat__wake_drain(int fd) {
    bool activity = false;
    struct input_event evbuf[16];
    for (;;) {
        ssize_t n = read(fd, evbuf, sizeof(evbuf));
        if (n < (ssize_t)sizeof(struct input_event)) break;
        int count = (int)(n / sizeof(struct input_event));
        for (int i = 0; i < count; i++) {
            if (cat__wake_event_is_activity(&evbuf[i])) activity = true;
        }
        if ((size_t)n < sizeof(evbuf)) break;
    }
    return activity;
}
#endif

/* Joystick subsystem + device scan (~200ms on device, dominated by the udev
   walk over /dev/input). Split out of cat_init so apps on the boot path can
   defer it past their first frame (cat_config.defer_input_init +
   cat_init_input); the event pump also calls it lazily as a fallback. */
static void cat__input_backend_init(void) {
    if (cat__g.input_backend_ready) {
        return;
    }
    cat__g.input_backend_ready = true;

    if (SDL_InitSubSystem(SDL_INIT_JOYSTICK | SDL_INIT_GAMECONTROLLER) < 0) {
        cat_log("ERROR: SDL_InitSubSystem(joystick) failed: %s", SDL_GetError());
        return;
    }

    /* Open all input devices. SDL-recognised controllers are opened via
     * SDL_GameControllerOpen and use the canonical mapping; everything else
     * (notably the virtual Loong pad on MLP1) is opened raw and keeps the
     * existing platform mapping. cat__pad_add_index() handles both. Hotplug is
     * handled at runtime via SDL_(CONTROLLER|JOY)DEVICEADDED/REMOVED. */
    int num_joy = SDL_NumJoysticks();
    if (num_joy > CAT__MAX_PADS) num_joy = CAT__MAX_PADS;
    for (int i = 0; i < num_joy; i++) {
        cat__pad_add_index(i);
    }
    cat_log("Input backend: %d device(s) tracked (%s)",
           cat__g.pad_count,
           cat__g.controller ? "gamecontroller+joystick" :
           (cat__g.joystick ? "joystick" : "none"));

    #if CAT_PLATFORM_IS_DEVICE
    /* Open the evdev fd set used only to wake from idle poll() in cat_present(). */
    cat__wake_init();
    #endif
}

void cat_init_input(void) {
    if (!cat__g.initialized) {
        return;
    }
    cat__input_backend_init();
}

int cat_init(cat_config *cfg) {
    if (cat__g.initialized) {
        cat__set_error("Already initialized");
        return CAT_ERROR;
    }

    memset(&cat__g, 0, sizeof(cat__g));
    cat__g.idle_wake_fd = -1;
    for (int i = 0; i < CAT__WAKE_MAX_FDS; i++) cat__g.wake_fds[i] = -1;
    for (int i = 0; i < CAT__MAX_PADS; i++) cat__g.pads[i].id = -1;
    #if CAT_PLATFORM_IS_DEVICE
    cat__g.power_fd = -1;
    #endif

    /* Logging */
    if (cfg && cfg->log_path) {
        cat_set_log_path(cfg->log_path);
    }

    cat_log("Catastrophe initializing (platform: %s)", CAT_PLATFORM_NAME);

    /* Per-phase init timings, reported in one line before "initialized
       successfully" — keeps boot-time regressions attributable. */
    uint32_t init_t0 = SDL_GetTicks();
    uint32_t init_t = init_t0;
    uint32_t init_ms_sdl = 0, init_ms_sdl_joy = 0, init_ms_ttf_img = 0,
             init_ms_window = 0, init_ms_renderer = 0,
             init_ms_sync = 0, init_ms_theme = 0, init_ms_fonts = 0,
             init_ms_bg = 0, init_ms_assets = 0;
    #define CAT__INIT_MARK(var) \
        do { uint32_t now__ = SDL_GetTicks(); (var) = now__ - init_t; init_t = now__; } while (0)

    /* Initialize stylesheet with defaults */
    cat_stylesheet_init_default(&cat__g.stylesheet);

    /* Set themes directory from env var or platform default */
    const char *td = cat__env_nonempty("CAT_THEMES_DIR");
    if (td) {
        strncpy(cat__g.themes_dir, td, sizeof(cat__g.themes_dir) - 1);
        cat__g.themes_dir[sizeof(cat__g.themes_dir) - 1] = '\0';
    } else {
        cat__g.themes_dir[0] = '\0';
    }

    /* Input defaults */
    cat__g.input_delay_ms = CAT_INPUT_DEBOUNCE;
    cat__g.input_repeat_delay_ms = CAT_INPUT_REPEAT_DELAY;
    cat__g.input_repeat_rate_ms = CAT_INPUT_REPEAT_RATE;
    cat__g.footer_overflow_opts.enabled = true;
    cat__g.footer_overflow_opts.chord_a = CAT_BTN_NONE;
    cat__g.footer_overflow_opts.chord_b = CAT_BTN_NONE;

    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) < 0) {
        cat__set_error("SDL_Init failed: %s", SDL_GetError());
        return CAT_ERROR;
    }
    CAT__INIT_MARK(init_ms_sdl);

    if (cfg && cfg->defer_input_init) {
        cat_log("Input init deferred (cat_init_input)");
    } else {
        cat__input_backend_init();
    }
    CAT__INIT_MARK(init_ms_sdl_joy);

    if (TTF_Init() < 0) {
        cat__set_error("TTF_Init failed: %s", TTF_GetError());
        SDL_Quit();
        return CAT_ERROR;
    }

    int img_flags = IMG_INIT_PNG | IMG_INIT_JPG;
    if (!(IMG_Init(img_flags) & img_flags)) {
        cat_log("Warning: SDL_image init incomplete: %s", IMG_GetError());
        /* Non-fatal — some platforms may not support all formats */
    }
    CAT__INIT_MARK(init_ms_ttf_img);

    /* Default face-button flip on TrimUI devices (firmware swaps A/B at hardware level) */
#if defined(PLATFORM_TG5040) || defined(PLATFORM_TG5050)
    cat__g.face_buttons_flipped = false;  /* Raw joystick map already accounts for TrimUI swap */
#endif

    /* Determine screen size */
    bool dev_mode = false;
    const char *env_val = getenv("CAT_ENV");
    if (!env_val) env_val = getenv("ENVIRONMENT");
    if (env_val && strcmp(env_val, "DEV") == 0) dev_mode = true;

    #if !CAT_PLATFORM_IS_DEVICE
    dev_mode = true;
    #endif

    if (dev_mode) {
        /* Windowed mode */
        const char *ww = getenv("CAT_WINDOW_WIDTH");
        const char *wh = getenv("CAT_WINDOW_HEIGHT");
        cat__g.screen_w = ww ? atoi(ww) : 1024;
        cat__g.screen_h = wh ? atoi(wh) :  768;
    } else {
        /* Fullscreen — get native display resolution */
        SDL_DisplayMode dm;
        #if defined(PLATFORM_MLP1)
        if (SDL_GetDesktopDisplayMode(0, &dm) == 0) {
            cat_log("MLP1 native display mode: %dx%d", dm.w, dm.h);
        } else {
            cat_log("MLP1 native display mode unavailable: %s", SDL_GetError());
        }
        const char *ww = getenv("CAT_WINDOW_WIDTH");
        const char *wh = getenv("CAT_WINDOW_HEIGHT");
        cat__g.screen_w = ww ? atoi(ww) : 960;
        cat__g.screen_h = wh ? atoi(wh) : 720;
        #else
        if (SDL_GetDesktopDisplayMode(0, &dm) == 0) {
            cat__g.screen_w = dm.w;
            cat__g.screen_h = dm.h;
        } else {
            /* Fallback defaults per platform */
            #if defined(PLATFORM_MY355)
            cat__g.screen_w = 640;
            cat__g.screen_h = 480;
            #else
            cat__g.screen_w = 1280;
            cat__g.screen_h = 720;
            #endif
        }
        #endif
    }

    cat_log("Screen size: %dx%d (dev_mode=%d)", cat__g.screen_w, cat__g.screen_h, dev_mode);

    /* Compute scale factor */
    cat__compute_scale_factor();
    cat_log("Scale factor: %.3f", cat__g.scale_factor);

    /* Compute device scale & padding before loading fonts.
       Desktop/dev previews now use the same resolution-based profile
       selection as device builds, based on the effective screen size
       (from CAT_WINDOW_WIDTH/CAT_WINDOW_HEIGHT in dev mode or native
       resolution). */
    cat__resolve_device_metrics();
    cat_log("Device scale: %d, padding: %d", cat__g.device_scale, cat__g.device_padding);

    /* Compute font bump (must be after device_scale, before font loading) */
    if (cfg && cfg->disable_font_bump)
        cat__g.font_bump = 0;
    else
        cat__g.font_bump = cat__compute_font_bump();
    {
        int env_bump = 0;
        if (cat__env_parse_int_range("CAT_FONT_BUMP", 0, CAT_FONT_BUMP_MAX, &env_bump))
            cat__g.font_bump = env_bump;
    }
    cat_log("Font bump: %d", cat__g.font_bump);

    /* Create window. Start hidden when requested so a daemon can warm up the
       renderer/fonts behind another fullscreen app (e.g. RetroArch) without
       mapping a surface; the caller maps it later via cat_show_window(). */
    uint32_t win_flags = (cfg && cfg->start_hidden) ? SDL_WINDOW_HIDDEN
                                                     : SDL_WINDOW_SHOWN;
    if (!dev_mode) {
        win_flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
    }

    const char *title = (cfg && cfg->window_title) ? cfg->window_title : "Catastrophe";

    cat__g.window = SDL_CreateWindow(
        title,
        SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
        cat__g.screen_w, cat__g.screen_h,
        win_flags
    );

    if (!cat__g.window) {
        cat__set_error("SDL_CreateWindow failed: %s", SDL_GetError());
        TTF_Quit();
        IMG_Quit();
        SDL_Quit();
        return CAT_ERROR;
    }
    CAT__INIT_MARK(init_ms_window);

    /* Set render quality hint before creating renderer/textures */
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "1"); /* bilinear filtering */

    /* Create renderer — try HW accelerated first, fall back to software */
    cat__g.renderer = SDL_CreateRenderer(cat__g.window, -1,
        SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC | SDL_RENDERER_TARGETTEXTURE);

    if (!cat__g.renderer) {
        cat_log("HW renderer failed, trying software: %s", SDL_GetError());
        cat__g.renderer = SDL_CreateRenderer(cat__g.window, -1, SDL_RENDERER_SOFTWARE);
    }

    if (!cat__g.renderer) {
        cat__set_error("SDL_CreateRenderer failed: %s", SDL_GetError());
        SDL_DestroyWindow(cat__g.window);
        TTF_Quit();
        IMG_Quit();
        SDL_Quit();
        return CAT_ERROR;
    }

    SDL_SetRenderDrawBlendMode(cat__g.renderer, SDL_BLENDMODE_BLEND);
    SDL_RenderSetLogicalSize(cat__g.renderer, cat__g.screen_w, cat__g.screen_h);
    SDL_ShowCursor(SDL_DISABLE);

    SDL_RendererInfo renderer_info;
    if (SDL_GetRendererInfo(cat__g.renderer, &renderer_info) == 0) {
        cat__g.renderer_has_vsync =
            (renderer_info.flags & SDL_RENDERER_PRESENTVSYNC) != 0;
    } else {
        cat__g.renderer_has_vsync = false;
    }
    cat_log("Renderer vsync: %s", cat__g.renderer_has_vsync ? "yes" : "no");
    CAT__INIT_MARK(init_ms_renderer);

    /* Framebuffer sync workaround — render 3 black frames to flush stale
       fbdev buffers on the TrimUI-class ports. MLP1 composites through
       Weston/Wayland where buffers start clean and nothing is shown before
       the first real commit — and each of these vsync'd presents blocks
       ~90ms while the surface is occluded by the boot transition, so the
       flush is skipped there. */
    #if !defined(PLATFORM_MLP1)
    for (int i = 0; i < 3; i++) {
        SDL_SetRenderDrawColor(cat__g.renderer, 0, 0, 0, 255);
        SDL_RenderClear(cat__g.renderer);
        SDL_RenderPresent(cat__g.renderer);
    }
    #endif
    cat__g.last_present_ms = SDL_GetTicks();
    CAT__INIT_MARK(init_ms_sync);

    /* Load requested/env theme first, then last-used theme from state file,
       defaulting to "Catastrophe". */
    {
        char theme_name[256];
        const char *env_theme = cat__env_nonempty("CAT_THEME_NAME");
        cat_theme_state_load(theme_name, sizeof(theme_name));
        if (env_theme)
            snprintf(theme_name, sizeof(theme_name), "%s", env_theme);
        cat_stylesheet ss;
        if (cat_stylesheet_load_theme(&ss, theme_name) != CAT_OK) {
            if (env_theme) {
                cat_log("Warning: CAT_THEME_NAME not found: %s", env_theme);
                cat_theme_state_load(theme_name, sizeof(theme_name));
            }
            if (cat_stylesheet_load_theme(&ss, theme_name) != CAT_OK)
                cat_stylesheet_init_default(&ss);
        }
        cat_stylesheet_apply(&ss);
    }

    /* Override accent color if specified */
    if (cfg && cfg->primary_color_hex) {
        cat_set_theme_color(cfg->primary_color_hex);
    }
    cat__apply_env_appearance_overrides();
    CAT__INIT_MARK(init_ms_theme);

    /* Load fonts. Priority: env override > cfg override > theme's ui_font.path > fallback. */
    const char *font_path = cat__env_nonempty("CAT_FONT_PATH");
    if (!font_path && cfg && cfg->font_path) font_path = cfg->font_path;
    if (!font_path && cat__g.theme.font_path[0]) font_path = cat__g.theme.font_path;
    if (cat__load_fonts(font_path) != CAT_OK) {
        cat__set_error("Failed to load fonts");
        SDL_DestroyRenderer(cat__g.renderer);
        SDL_DestroyWindow(cat__g.window);
        TTF_Quit();
        IMG_Quit();
        SDL_Quit();
        return CAT_ERROR;
    }
    CAT__INIT_MARK(init_ms_fonts);

    /* Load background image (on by default unless disabled) */
    if (!cfg || !cfg->disable_background) {
        const char *bg_path = (cfg && cfg->bg_image_path) ? cfg->bg_image_path : NULL;
        if (!bg_path || !bg_path[0]) {
            bg_path = getenv("CAT_BACKGROUND_PATH");
            #if CAT_PLATFORM_IS_DEVICE
            if (!bg_path || !bg_path[0]) {
                static char bg_buf[PATH_MAX];
                const char *sdcard = cat__sdcard_path();
                if (sdcard) {
                    snprintf(bg_buf, sizeof(bg_buf), "%s/bg.png", sdcard);
                    bg_path = bg_buf;
                }
            }
            #endif
        }

        if (bg_path && bg_path[0]) {
            cat__g.bg_texture = cat_load_image(bg_path);
            if (cat__g.bg_texture) {
                cat__str_copy(cat__g.theme.bg_image_path, sizeof(cat__g.theme.bg_image_path), bg_path);
                cat_log("Loaded background: %s", bg_path);
            } else {
                cat_log("Warning: could not load background: %s", bg_path);
            }
        }
    }
    CAT__INIT_MARK(init_ms_bg);

    /* Load status-bar / pill cap sprite sheet (icons + AA pill caps) */
    {
        char assets_dir_buf[256];
        int scale = cat__g.device_scale;
        char asset_path[512];
        const char *assets_dir = cat__status_assets_dir(assets_dir_buf, sizeof(assets_dir_buf));
        if (assets_dir && assets_dir[0]) {
            snprintf(asset_path, sizeof(asset_path), "%s/assets@%dx.png", assets_dir, scale);
            SDL_Surface *surf = IMG_Load(asset_path);
            if (surf) {
                cat__g.status_assets = SDL_CreateTextureFromSurface(cat__g.renderer, surf);
                cat__g.status_asset_scale = scale;
                SDL_SetTextureBlendMode(cat__g.status_assets, SDL_BLENDMODE_BLEND);
                SDL_FreeSurface(surf);
                cat_log("Loaded status assets: %s", asset_path);
            } else {
                cat_log("Warning: could not load status assets: %s", asset_path);
            }
        }
    }
    CAT__INIT_MARK(init_ms_assets);

    /* Always start the power-button handler on supported device ports. */
    #if defined(PLATFORM_MLP1)
    cat_log("Power handler: disabled on MLP1 pending stock-service audit");
    #else
    cat_set_power_handler(true);
    #endif

    /* Apply CPU speed preset if specified */
    if (cfg && cfg->cpu_speed != CAT_CPU_SPEED_DEFAULT) {
        if (cat_set_cpu_speed(cfg->cpu_speed) != CAT_OK) {
            cat_log("Warning: cat_set_cpu_speed failed for preset %d", (int)cfg->cpu_speed);
        } else {
            cat_log("CPU speed set to preset %d", (int)cfg->cpu_speed);
        }
    }

    cat__g.initialized = true;
    cat_log("cat_init timings: sdl=%u sdl_joy=%u ttf_img=%u window=%u "
            "renderer=%u sync=%u theme=%u fonts=%u bg=%u assets=%u total=%u",
            init_ms_sdl, init_ms_sdl_joy, init_ms_ttf_img,
            init_ms_window, init_ms_renderer, init_ms_sync, init_ms_theme,
            init_ms_fonts, init_ms_bg, init_ms_assets, SDL_GetTicks() - init_t0);
    #undef CAT__INIT_MARK
    cat_log("Catastrophe initialized successfully");

    return CAT_OK;
}

void cat_activate_window(void) {
#if defined(__APPLE__)
    if (!cat__g.window) return;
    SDL_RaiseWindow(cat__g.window);
    {
        typedef id   (*id_fn)(id, SEL);
        typedef void (*void_long_fn)(id, SEL, long);
        typedef void (*void_int_fn)(id, SEL, int);
        id app = ((id_fn)objc_msgSend)(
            (id)objc_getClass("NSApplication"),
            sel_registerName("sharedApplication"));
        ((void_long_fn)objc_msgSend)(app,
            sel_registerName("setActivationPolicy:"), 0L);
        ((void_int_fn)objc_msgSend)(app,
            sel_registerName("activateIgnoringOtherApps:"), 1);
    }
#endif
}

int cat_get_tab_bar_height(void) {
    TTF_Font *font = cat_get_font(CAT_FONT_SMALL);
    int font_h = font ? TTF_FontHeight(font) : CAT_DS(14);
    int bar_h  = font_h + CAT_S(6);
    int min_h  = CAT_DS(16);
    return bar_h < min_h ? min_h : bar_h;
}

void cat_set_tab_bar_reserved_right(int px) {
    cat__g.tab_bar_reserved_right = px > 0 ? px : 0;
}

/* Filled triangle via SDL_RenderGeometry (no glyph needed), like cat_draw_star. */
static void cat__fill_triangle(float ax, float ay, float bx, float by,
                               float cx, float cy, cat_draw_color c) {
    SDL_Color col = { c.r, c.g, c.b, c.a };
    SDL_Vertex v[3];
    v[0].position = (SDL_FPoint){ ax, ay }; v[0].color = col; v[0].tex_coord = (SDL_FPoint){ 0, 0 };
    v[1].position = (SDL_FPoint){ bx, by }; v[1].color = col; v[1].tex_coord = (SDL_FPoint){ 0, 0 };
    v[2].position = (SDL_FPoint){ cx, cy }; v[2].color = col; v[2].tex_coord = (SDL_FPoint){ 0, 0 };
    int idx[3] = { 0, 1, 2 };
    SDL_RenderGeometry(cat__g.renderer, NULL, v, 3, idx, 3);
}

void cat_draw_triangle(int x, int y, int w, int h, cat_dir dir, cat_draw_color c) {
    if (w <= 0 || h <= 0) return;
    float l = (float)x, t = (float)y, r = (float)(x + w), b = (float)(y + h);
    float mid_x = (float)x + (float)w * 0.5f;
    float mid_y = (float)y + (float)h * 0.5f;
    switch (dir) {
        case CAT_DIR_LEFT:  cat__fill_triangle(l, mid_y, r, t, r, b, c); break;
        case CAT_DIR_RIGHT: cat__fill_triangle(l, t, l, b, r, mid_y, c); break;
        case CAT_DIR_UP:    cat__fill_triangle(l, b, r, b, mid_x, t, c); break;
        case CAT_DIR_DOWN:  cat__fill_triangle(l, t, r, t, mid_x, b, c); break;
    }
}

void cat_draw_tab_bar(const char *const *labels, int count, int active_index) {
    if (!labels || count <= 0) return;
    TTF_Font *font    = cat_get_font(CAT_FONT_SMALL);
    cat_draw_color active_c = cat_color_to_sdl(cat__g.stylesheet.ui.tab_selected_color);
    cat_draw_color inact_c  = cat_color_to_sdl(cat__g.stylesheet.ui.tab_color);

    int sw     = cat_get_screen_width();
    int bar_h  = cat_get_tab_bar_height();
    cat_draw_rect(0, 0, sw, bar_h, cat_get_theme()->accent);

    int font_h = TTF_FontHeight(font);
    int text_y = (bar_h - font_h) / 2;
    int underline_h = CAT_S(2);
    int gap    = CAT_S(20);
    int left_x = CAT_S(16);

    if (active_index < 0) active_index = 0;
    if (active_index >= count) active_index = count - 1;

    /* Usable width for tabs: the bar minus any reserved right-side space (e.g.
       an inline status bar drawn over the bar). */
    int usable = sw - cat__g.tab_bar_reserved_right - left_x;
    if (usable < CAT_S(40)) usable = CAT_S(40);

    int label_w[count];
    int total = 0;
    for (int i = 0; i < count; i++) {
        label_w[i] = labels[i] ? cat_measure_text(font, labels[i]) : 0;
        total += label_w[i] + gap;
    }

    /* Triangle affordance dimensions (drawn instead of text < / >). */
    int tri_h  = font_h * 6 / 10;
    int tri_w  = font_h * 4 / 10;
    if (tri_w < CAT_S(6)) tri_w = CAT_S(6);
    int chev_w = tri_w + gap;        /* horizontal space a chevron consumes */

    /* Pick the visible window. If everything fits, show all. Otherwise grow a
       window around the active tab; each step recomputes the FULL window width
       counting only the chevrons actually needed, so reaching an end frees that
       side's chevron space and we keep filling instead of stopping short. */
    int first, last;
    if (total <= usable) {
        first = 0;
        last  = count - 1;
    } else {
        first = last = active_index;
        for (;;) {
            int grew = 0;
            if (last < count - 1) {
                int f = first, l = last + 1, w = 0;
                if (f > 0)         w += chev_w;
                if (l < count - 1) w += chev_w;
                for (int i = f; i <= l; i++) w += label_w[i] + gap;
                if (w <= usable) { last = l; grew = 1; }
            }
            if (first > 0) {
                int f = first - 1, l = last, w = 0;
                if (f > 0)         w += chev_w;
                if (l < count - 1) w += chev_w;
                for (int i = f; i <= l; i++) w += label_w[i] + gap;
                if (w <= usable) { first = f; grew = 1; }
            }
            if (!grew) break;
        }
    }

    int tri_top = (bar_h - tri_h) / 2;
    int x = left_x;
    if (first > 0) {                 /* hidden tabs to the left → left triangle */
        cat_draw_triangle(x, tri_top, tri_w, tri_h, CAT_DIR_LEFT, inact_c);
        x += tri_w + gap;
    }
    for (int i = first; i <= last; i++) {
        if (!labels[i]) continue;
        bool active = (i == active_index);
        int tw = cat_draw_text(font, labels[i], x, text_y,
                               active ? active_c : inact_c);
        if (active)
            cat_draw_rect(x, bar_h - underline_h, tw, underline_h, active_c);
        x += tw + gap;
    }
    if (last < count - 1) {          /* hidden tabs to the right → right triangle */
        cat_draw_triangle(x, tri_top, tri_w, tri_h, CAT_DIR_RIGHT, inact_c);
    }
}

void cat_draw_textured_parallelogram(SDL_Texture *tex,
                                      const SDL_FPoint quad[4],
                                      uint8_t alpha) {
    if (!tex || !quad) return;
    SDL_Renderer *r = cat_get_renderer();
    SDL_SetTextureAlphaMod(tex, alpha);

    /* UV corners matching clockwise quad: TL, TR, BR, BL */
    static const SDL_FPoint uvs[4] = {
        {0.0f, 0.0f}, {1.0f, 0.0f}, {1.0f, 1.0f}, {0.0f, 1.0f}
    };
    SDL_Vertex verts[4];
    for (int i = 0; i < 4; i++) {
        verts[i].position  = quad[i];
        verts[i].tex_coord = uvs[i];
        verts[i].color     = (SDL_Color){255, 255, 255, alpha};
    }
    int indices[6] = {0, 1, 2,  0, 2, 3};
    SDL_RenderGeometry(r, tex, verts, 4, indices, 6);
    SDL_SetTextureAlphaMod(tex, 255);
}

void cat_quit(void) {
    if (!cat__g.initialized) return;

    cat_log("Catastrophe shutting down");

    /* Stop power handler */
    cat_set_power_handler(false);

    /* Clear texture cache */
    cat_cache_clear();
    cat__text_cache_clear();

    /* Destroy background texture */
    if (cat__g.bg_texture) {
        SDL_DestroyTexture(cat__g.bg_texture);
        cat__g.bg_texture = NULL;
    }

    /* Destroy the rounded-image scratch render target */
    if (cat__g.rounded_scratch) {
        SDL_DestroyTexture(cat__g.rounded_scratch);
        cat__g.rounded_scratch = NULL;
        cat__g.rounded_scratch_w = 0;
        cat__g.rounded_scratch_h = 0;
    }

    /* Destroy status assets */
    if (cat__g.status_assets) {
        SDL_DestroyTexture(cat__g.status_assets);
        cat__g.status_assets = NULL;
    }

    /* Close fonts */
    for (int i = 0; i < CAT_FONT_TIER_COUNT; i++) {
        if (cat__g.fonts[i]) {
            TTF_CloseFont(cat__g.fonts[i]);
            cat__g.fonts[i] = NULL;
        }
    }
    if (cat__g.symbol_font) {
        TTF_CloseFont(cat__g.symbol_font);
        cat__g.symbol_font = NULL;
    }
    if (cat__g.symbol_font_small) {
        TTF_CloseFont(cat__g.symbol_font_small);
        cat__g.symbol_font_small = NULL;
    }

    #if CAT_PLATFORM_IS_DEVICE
    /* Close idle-wake evdev fds */
    cat__wake_close_all();
    #endif

    /* Close all tracked controllers / joysticks */
    for (int i = 0; i < CAT__MAX_PADS; i++) {
        if (cat__g.pads[i].id == -1) continue;
        if (cat__g.pads[i].gc) SDL_GameControllerClose(cat__g.pads[i].gc);
        else if (cat__g.pads[i].joy) SDL_JoystickClose(cat__g.pads[i].joy);
        cat__g.pads[i].id = -1;
        cat__g.pads[i].gc = NULL;
        cat__g.pads[i].joy = NULL;
    }
    cat__g.pad_count = 0;
    cat__g.controller = NULL;
    cat__g.joystick = NULL;

    /* Destroy renderer and window */
    if (cat__g.renderer) {
        SDL_DestroyRenderer(cat__g.renderer);
        cat__g.renderer = NULL;
    }
    if (cat__g.window) {
        SDL_DestroyWindow(cat__g.window);
        cat__g.window = NULL;
    }

    /* Close log file */
    if (cat__g.log_file && cat__g.log_file != stderr) {
        fclose(cat__g.log_file);
        cat__g.log_file = NULL;
    }

    TTF_Quit();
    IMG_Quit();
    SDL_Quit();

    cat__g.initialized = false;
}

#endif /* CAT_IMPLEMENTATION */
#endif /* CATASTROPHE_H */
