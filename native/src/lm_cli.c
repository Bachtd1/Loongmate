#define _GNU_SOURCE
#include "lm_cli.h"
#include <errno.h>
#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

lm_paths P;

static int file_ok(const char *p, int mode) { return p && *p && access(p, mode) == 0; }

int cli_resolve(const char *arg_pak, const char *arg_py, const char *arg_ud,
                const char *arg_primary, const char *arg_secondary, const char *arg_apps, char *err, size_t errsz) {
    if (arg_pak) snprintf(P.pak, sizeof P.pak, "%s", arg_pak);
    else {
        char exe[512];
        ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);   /* <pak>/bin/<binary> -> <pak> */
        if (n <= 0) { snprintf(err, errsz, "không xác định được thư mục pak (dùng --pak)"); return -1; }
        exe[n] = 0;
        char tmp[512];
        snprintf(tmp, sizeof tmp, "%s", dirname(exe));
        snprintf(P.pak, sizeof P.pak, "%s/..", tmp);
    }
    if (arg_ud) snprintf(P.userdata, sizeof P.userdata, "%s", arg_ud);
    else {
        const char *u = getenv("USERDATA_PATH"), *sd = getenv("SDCARD_PATH");
        if (u && *u) snprintf(P.userdata, sizeof P.userdata, "%s/LoongMate", u);
        else snprintf(P.userdata, sizeof P.userdata, "%s/.userdata/mlp1/LoongMate", (sd && *sd) ? sd : "/mnt/sdcard");
    }
    if (arg_primary) snprintf(P.primary, sizeof P.primary, "%s", arg_primary);
    if (arg_secondary) snprintf(P.secondary, sizeof P.secondary, "%s", arg_secondary);
    if (arg_apps) snprintf(P.apps_dir, sizeof P.apps_dir, "%s", arg_apps);
    const char *nr = getenv("LOONGMATE_NO_RESCAN");
    P.no_rescan = nr && !strcmp(nr, "1");
    snprintf(P.cli, sizeof P.cli, "%s/engine/cli.py", P.pak);
    snprintf(P.ui, sizeof P.ui, "%s/engine/ui_sdl.py", P.pak);

    /* cùng thứ tự ưu tiên với launch.sh */
    char c[8][560];
    int n = 0;
    const char *env_py = getenv("LOONGMATE_PYTHON");
    if (arg_py) snprintf(c[n++], sizeof c[0], "%s", arg_py);
    if (env_py && *env_py) snprintf(c[n++], sizeof c[0], "%s", env_py);
    snprintf(c[n++], sizeof c[0], "%s/runtime/python/bin/python3", P.userdata);
    snprintf(c[n++], sizeof c[0], "/userdata/loongmate-runtime/python/bin/python3");
    snprintf(c[n++], sizeof c[0], "%s/runtime/python/bin/python3", P.pak);
    snprintf(c[n++], sizeof c[0], "/usr/bin/python3");
    snprintf(c[n++], sizeof c[0], "/bin/python3");
    P.python[0] = 0;
    for (int i = 0; i < n; i++)
        if (file_ok(c[i], X_OK)) { snprintf(P.python, sizeof P.python, "%s", c[i]); break; }
    if (!P.python[0]) { snprintf(err, errsz, "không tìm thấy Python (LOONGMATE_PYTHON hoặc /userdata/loongmate-runtime)"); return -1; }
    if (!file_ok(P.cli, R_OK)) { snprintf(err, errsz, "thiếu %s", P.cli); return -1; }
    return 0;
}

const char *plan_file(const char *kind) {
    static char b[640];
    snprintf(b, sizeof b, "%s/jobs/native-%s.plan.json", P.userdata, kind);
    return b;
}
void plan_remove(const char *kind) { unlink(plan_file(kind)); }

#define MAX_ARGV 40

/* Như cli_task nhưng đối số thêm là mảng có độ dài tuỳ ý (vd. rom delete P1 P2 ... cho cả lô). */
int cli_task_v(const char *label, int timeout_ms, ui_task_cb cb, const char *arg, const char *const *extra, int nextra) {
    char **argv = malloc((size_t)(nextra + 16) * sizeof *argv);
    if (!argv) { ui_info1("Lỗi nội bộ", "Hết bộ nhớ khi dựng lệnh engine."); return 0; }
    int n = 0;
    argv[n++] = P.python;
    argv[n++] = P.cli;
    if (P.primary[0]) { argv[n++] = "--primary"; argv[n++] = P.primary; }
    if (P.secondary[0]) { argv[n++] = "--secondary"; argv[n++] = P.secondary; }
    argv[n++] = "--userdata"; argv[n++] = P.userdata;
    argv[n++] = "--quiet"; argv[n++] = "--json";
    if (P.no_rescan) argv[n++] = "--no-rescan";
    for (int i = 0; i < nextra; i++) argv[n++] = (char *)extra[i];
    argv[n] = NULL;
    int rc = 0;
    if (!P.python[0]) ui_info1("Không chạy được engine", "Chưa có Python/engine (xem log). Không thể thực hiện: %s", label);
    else rc = ui_task(label, argv, timeout_ms, cb, arg);
    free(argv);     /* lm_job_start đã fork+execv xong: tiến trình con giữ bản sao riêng */
    return rc;
}

int cli_task(const char *label, int timeout_ms, ui_task_cb cb, const char *arg, ...) {
    const char *ex[MAX_ARGV];
    int n = 0;
    va_list ap;
    va_start(ap, arg);
    const char *a;
    while ((a = va_arg(ap, const char *)) != NULL) {
        if (n >= MAX_ARGV - 1) { va_end(ap); ui_info1("Lỗi nội bộ", "Quá nhiều đối số cho lệnh engine."); return 0; }
        ex[n++] = a;
    }
    va_end(ap);
    return cli_task_v(label, timeout_ms, cb, arg, ex, n);
}

/* Thay tiến trình bằng giao diện Python. Không truyền --pak: ui_sdl.py không có cờ đó (nó tự biết thư mục của nó). */
void ui_exec_python_ui(void) {
    if (!file_ok(P.ui, R_OK) || !P.python[0]) {
        ui_info1("Không mở được giao diện Python", "Thiếu %s hoặc không tìm thấy Python.", P.ui[0] ? P.ui : "engine/ui_sdl.py");
        return;
    }
    char *argv[16];
    int n = 0;
    argv[n++] = P.python;
    argv[n++] = P.ui;
    if (P.primary[0]) { argv[n++] = "--primary"; argv[n++] = P.primary; }
    if (P.secondary[0]) { argv[n++] = "--secondary"; argv[n++] = P.secondary; }
    argv[n++] = "--userdata"; argv[n++] = P.userdata;
    if (P.apps_dir[0]) { argv[n++] = "--apps-dir"; argv[n++] = P.apps_dir; }
    argv[n] = NULL;
    cat_log("lm: execv %s %s ...", P.python, P.ui);
    cat_quit();   /* nhả màn hình + tay cầm trước khi giao quyền */
    execv(argv[0], argv);
    fprintf(stderr, "lm: execv %s that bai: %s\n", argv[0], strerror(errno));
    exit(1);
}
