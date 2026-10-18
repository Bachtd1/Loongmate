#define _GNU_SOURCE
#include "lm_run.h"
#include "lm_i18n.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define OUT_CAP (4 * 1024 * 1024)
#define ERR_CAP 4096

struct lm_job {
    pid_t pid;
    int out_fd, err_fd;           /* -1 khi đã EOF */
    char *out; size_t out_len, out_alloc; int out_trunc;
    char err[ERR_CAP + 1]; size_t err_len;
    int done, exit_code, timed_out;
    long long deadline_ms;        /* 0 = không giới hạn */
};

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void set_nb(int fd) {
    int fl = fcntl(fd, F_GETFL, 0);
    if (fl >= 0) fcntl(fd, F_SETFL, fl | O_NONBLOCK);
}

lm_job *lm_job_start(char *const argv[], int timeout_ms, char *err, size_t errsz) {
    int po[2], pe[2];
    if (pipe2(po, O_CLOEXEC) != 0) { snprintf(err, errsz, "pipe: %s", strerror(errno)); return NULL; }
    if (pipe2(pe, O_CLOEXEC) != 0) {
        snprintf(err, errsz, "pipe: %s", strerror(errno));
        close(po[0]); close(po[1]);
        return NULL;
    }
    pid_t pid = fork();
    if (pid < 0) {
        snprintf(err, errsz, "fork: %s", strerror(errno));
        close(po[0]); close(po[1]); close(pe[0]); close(pe[1]);
        return NULL;
    }
    if (pid == 0) {
        int dn = open("/dev/null", O_RDONLY);
        if (dn >= 0) dup2(dn, 0);
        dup2(po[1], 1);           /* dup2 xoá cờ CLOEXEC trên fd đích */
        dup2(pe[1], 2);
        /* không để con thừa kế fd của SDL (màn hình, tay cầm, DRM...): đóng mọi fd > 2 trước khi exec */
        for (int fd = 3; fd < 1024; fd++) close(fd);
        execv(argv[0], argv);
        _exit(127);
    }
    close(po[1]); close(pe[1]);
    lm_job *j = calloc(1, sizeof *j);
    if (!j) { kill(pid, SIGKILL); waitpid(pid, NULL, 0); close(po[0]); close(pe[0]); snprintf(err, errsz, tr("hết bộ nhớ")); return NULL; }
    j->pid = pid; j->out_fd = po[0]; j->err_fd = pe[0];
    set_nb(j->out_fd); set_nb(j->err_fd);
    j->out_alloc = 4096;
    j->out = calloc(1, j->out_alloc + 1);
    j->deadline_ms = timeout_ms > 0 ? now_ms() + timeout_ms : 0;
    return j;
}

static void drain_out(lm_job *j) {
    char buf[4096];
    while (j->out_fd >= 0) {
        ssize_t n = read(j->out_fd, buf, sizeof buf);
        if (n > 0) {
            if (j->out_len + (size_t)n > OUT_CAP) { j->out_trunc = 1; n = (ssize_t)(OUT_CAP - j->out_len); if (n <= 0) continue; }
            if (j->out_len + (size_t)n > j->out_alloc) {
                size_t na = j->out_alloc * 2;
                while (na < j->out_len + (size_t)n) na *= 2;
                char *p = realloc(j->out, na + 1);
                if (!p) { j->out_trunc = 1; return; }
                j->out = p; j->out_alloc = na;
            }
            memcpy(j->out + j->out_len, buf, (size_t)n);
            j->out_len += (size_t)n; j->out[j->out_len] = 0;
        } else if (n == 0) { close(j->out_fd); j->out_fd = -1; }
        else { if (errno == EAGAIN || errno == EINTR) return; close(j->out_fd); j->out_fd = -1; }
    }
}

static void drain_err(lm_job *j) {
    char buf[1024];
    while (j->err_fd >= 0) {
        ssize_t n = read(j->err_fd, buf, sizeof buf);
        if (n > 0) {
            /* giữ phần đuôi: lỗi Python nằm ở cuối traceback */
            if (j->err_len + (size_t)n > ERR_CAP) {
                size_t drop = j->err_len + (size_t)n - ERR_CAP;
                memmove(j->err, j->err + drop, j->err_len - drop);
                j->err_len -= drop;
            }
            memcpy(j->err + j->err_len, buf, (size_t)n);
            j->err_len += (size_t)n; j->err[j->err_len] = 0;
        } else if (n == 0) { close(j->err_fd); j->err_fd = -1; }
        else { if (errno == EAGAIN || errno == EINTR) return; close(j->err_fd); j->err_fd = -1; }
    }
}

int lm_job_poll(lm_job *j) {
    if (j->done) return 1;
    drain_out(j); drain_err(j);
    int st;
    pid_t r = waitpid(j->pid, &st, WNOHANG);
    if (r == 0 && j->deadline_ms && now_ms() > j->deadline_ms) {
        kill(j->pid, SIGKILL);
        waitpid(j->pid, &st, 0);
        j->timed_out = 1; j->exit_code = -1; r = j->pid;
        st = 0;
    } else if (r == j->pid) {
        j->exit_code = WIFEXITED(st) ? WEXITSTATUS(st) : -1;
    }
    if (r == j->pid) {
        drain_out(j); drain_err(j);               /* lấy nốt phần còn trong pipe */
        if (j->out_fd >= 0) { close(j->out_fd); j->out_fd = -1; }
        if (j->err_fd >= 0) { close(j->err_fd); j->err_fd = -1; }
        j->done = 1;
        return 1;
    }
    if (r < 0 && errno != EINTR) { j->exit_code = -1; j->done = 1; return 1; }
    return 0;
}

const char *lm_job_stdout(const lm_job *j) { return j->out ? j->out : ""; }
const char *lm_job_stderr(const lm_job *j) { return j->err; }
int lm_job_exit_code(const lm_job *j) { return j->exit_code; }
int lm_job_timed_out(const lm_job *j) { return j->timed_out; }
int lm_job_stdout_truncated(const lm_job *j) { return j->out_trunc; }

void lm_job_kill(lm_job *j) {
    if (j && !j->done) kill(j->pid, SIGTERM);
}

void lm_job_free(lm_job *j) {
    if (!j) return;
    if (!j->done) { kill(j->pid, SIGKILL); waitpid(j->pid, NULL, 0); }
    if (j->out_fd >= 0) close(j->out_fd);
    if (j->err_fd >= 0) close(j->err_fd);
    free(j->out);
    free(j);
}
