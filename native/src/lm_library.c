/* LoongMate native — Library: quét, danh sách vấn đề, sửa an toàn, sửa hàng loạt + hoàn tác, BIOS,
 * dọn ROM trùng (3 bước), chuyển game giữa hai thẻ. Mọi việc đi qua cli.py --json; lệnh ghi chỉ nhận
 * --yes SAU khi người dùng đã bấm xác nhận trong hộp thoại native (mặc định Huỷ).
 *
 * Dọn ROM trùng / sửa hàng loạt / chuyển game dùng "kế hoạch đã duyệt": bước xem trước gọi CLI với --plan-out
 * <userdata>/jobs/native-<loại>.plan.json, màn hình hiện ĐÚNG các mục của kế hoạch đó, và bước thực hiện gọi
 * `--apply --plan <file> --yes` nên engine chỉ làm các mục đã xem (không quét lại để tìm thêm). Xoá file kế hoạch
 * khi thực hiện xong hoặc khi màn kế hoạch bị đóng (huỷ). */
#define _GNU_SOURCE
#include "lm_app.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static cJSON *g_report;          /* báo cáo `scan` đầy đủ của phiên; cache đĩa chỉ giữ tóm tắt */
static cJSON *g_dup;             /* xem trước dedupe của phiên = đúng bản đã ghi vào file kế hoạch (có plan_id) */
static int g_dup_k = -1;         /* prefer-card của g_dup: 0 auto, 1 primary, 2 secondary */
static int g_dup_art = 1;        /* chép ảnh bìa: cờ này đi vào kế hoạch lúc xem trước nên đổi nó phải xem trước lại */
static cJSON *g_undo;            /* `fix undo --check` mới nhất, hoặc NULL */
static int g_plan_n;             /* số mục kế hoạch vừa hiện trong hộp xác nhận (để báo "đã làm X/N") */
static char g_plan_dest[16];

static const char *const SEV_KEYS[3] = { "critical", "warning", "info" };
static const char *const SEV_NAME[3] = { "NGHIÊM TRỌNG", "CẢNH BÁO", "GHI CHÚ" };
static const int SEV_COLOR[3] = { ST_RED, ST_YELLOW, ST_UNKNOWN };

static const char *sd_name(const char *card) { return card && !strcmp(card, "primary") ? "SD1" : "SD2"; }
static const char *mbs(double bytes) { static char b[4][32]; static int i; i = (i + 1) & 3; ui_fmt_bytes(b[i], sizeof b[i], bytes); return b[i]; }

static void clear_dup(void) {
    cJSON_Delete(g_dup);
    g_dup = NULL;
    g_dup_k = -1;
    plan_remove("dedupe");
}

/* danh sách "dòng chữ" cho kết quả --apply --plan: các mục bị từ chối kèm lý do */
static void add_refused(lines_t *L, const cJSON *refused, const char *what_key) {
    cJSON *x;
    cJSON_ArrayForEach(x, refused) {
        const char *who = what_key ? jstr(x, what_key) : NULL, *path = jstr(x, "path");
        if (who && path) lines_add(L, "!! TỪ CHỐI [%s] %s: %s", who, path, jstrd(x, "reason", ""));
        else lines_add(L, "!! TỪ CHỐI %s: %s", path ? path : (who ? who : "?"), jstrd(x, "reason", ""));
    }
}

/* ============================================================ Library */
static void do_scan(ui_screen *s, ui_item *it);
static void open_issues(ui_screen *s, ui_item *it);
static void ask_fixes(ui_screen *s, ui_item *it);
static void open_batchfix(ui_screen *s, ui_item *it);
static void ask_undo(ui_screen *s, ui_item *it);
static void open_bios(ui_screen *s, ui_item *it);
static void open_dedupe(ui_screen *s, ui_item *it);

static void build_library(ui_screen *s) {
    cJSON *cache = jget(g_ready, "library");
    int avail = jbool_d(cache, "available", 1) && jget(cache, "total_issues");
    if (avail) {
        cJSON *sev = jget(cache, "severity_counts");
        ui_hdr(s, "Số liệu lúc %s: %.0f vấn đề (NT %.0f / CB %.0f / GC %.0f)", jstrd(cache, "generated_at", "không rõ"),
               jnumd(cache, "total_issues", 0), jnumd(sev, "critical", 0), jnumd(sev, "warning", 0), jnumd(sev, "info", 0));
    } else ui_hdr(s, "! Chưa quét lần nào.");
    if (!g_report) ui_hdr(s, "Cache chỉ giữ tóm tắt: chọn Quét lại để xem danh sách vấn đề chi tiết.");
    int nfix = g_report ? jarr_n(g_report, "fixable_actions") : 0;
    char tag[48];

    ui_item *it = ui_add(s, "Quét lại thư viện", "~15 giây", ST_UNKNOWN);
    ui_exp(it, "Quét ROM, ảnh bìa, save, BIOS trên cả hai thẻ (chỉ đọc), chạy các luật kiểm toán, rồi lưu bản tóm tắt.");
    ui_exp(it, "Không sửa hay xoá gì.");
    ui_exp(it, "Sau khi quét, mục Danh sách vấn đề sẽ có chi tiết.");
    ui_act(it, 0, "QUÉT", do_scan);

    snprintf(tag, sizeof tag, "%.0f", g_report ? jnumd(g_report, "total_issues", 0) : 0);
    it = ui_add(s, "Danh sách vấn đề", g_report ? tag : "cần quét", g_report ? ST_YELLOW : ST_UNKNOWN);
    ui_exp(it, g_report ? "Từng vấn đề kèm mô tả và việc cần làm, nhóm theo mức độ."
                        : "Chưa có danh sách chi tiết trong phiên này — hãy Quét lại trước.");
    if (g_report) ui_act(it, 0, "MỞ", open_issues);

    snprintf(tag, sizeof tag, "%d việc", nfix);
    it = ui_add(s, "Áp dụng sửa an toàn", g_report ? tag : "cần quét", ST_UNKNOWN);
    ui_exp(it, "Chỉ gồm các sửa an toàn do Library Doctor đề xuất (vd tạo thư mục thiếu, sửa .cue có ánh xạ rõ ràng).");
    ui_exp(it, "Bạn sẽ thấy danh sách từng việc rồi mới xác nhận; kết quả hiện từng việc một.");
    ui_exp(it, nfix ? "Không xoá file." : (g_report ? "Không có sửa an toàn nào." : "Cần quét trước."));
    if (nfix) ui_act(it, 0, "XEM & ÁP DỤNG", ask_fixes);

    it = ui_add(s, "Sửa hàng loạt", "xem trước", ST_UNKNOWN);
    ui_exp(it, "Quét thư viện rồi gom các sửa một chạm: tạo thư mục chuẩn, sửa .cue, tạo .m3u cho game nhiều đĩa, gộp thư mục tên cũ.");
    ui_exp(it, "Bạn xem từng bước (rủi ro, hoàn tác được không) rồi mới xác nhận; lỗi từng bước không chặn các bước khác.");
    ui_exp(it, "Mọi bước ghi vào nhật ký để hoàn tác (mục bên dưới).");
    ui_act(it, 0, "QUÉT & XEM KẾ HOẠCH", open_batchfix);

    {
        int has = jbool_d(g_undo, "has_batch", 0), known = jget(g_undo, "has_batch") != NULL;
        char ut[48];
        snprintf(ut, sizeof ut, "%.0f thao tác", jnumd(g_undo, "operations", 0));
        it = ui_add(s, "Hoàn tác lô sửa", has ? ut : known ? "không có lô" : "không kiểm được", has ? ST_YELLOW : ST_UNKNOWN);
        ui_exp(it, "Hoàn tác theo thứ tự ngược, dựa vào trạng thái THẬT trên đĩa chứ không tin mù quáng vào nhật ký.");
        ui_exp(it, "Việc nào không hoàn tác an toàn được (đường dẫn cũ đã bị chiếm, file đã bị sửa...) sẽ bị bỏ qua và nêu lý do.");
        if (has) {
            ui_exp(it, "Lô %s (%s): %.0f thao tác.", jstrd(g_undo, "batch_id", "?"), jstrd(g_undo, "created_at", "không rõ lúc"), jnumd(g_undo, "operations", 0));
            cJSON *op;
            cJSON_ArrayForEach(op, jget(g_undo, "by_op")) if (cJSON_IsNumber(op)) ui_exp(it, "• %s x%.0f", op->string, op->valuedouble);
            ui_act(it, 0, "HOÀN TÁC", ask_undo);
        } else if (known) ui_exp(it, "Không có lô sửa hàng loạt nào đang chờ hoàn tác.");
        else ui_exp(it, "! Không đọc được `fix undo --check` từ engine (engine cũ?), nên không biết có lô để hoàn tác hay không.");
    }

    it = ui_add(s, "Kiểm tra BIOS", "md5", ST_UNKNOWN);
    ui_exp(it, "Bảng hệ máy x file BIOS: ok / sai hash / thiếu / chưa có hash nguồn. Chỉ đọc.");
    ui_exp(it, "LoongMate không tải BIOS — bạn tự cung cấp bản hợp lệ.");
    ui_act(it, 0, "KIỂM TRA", open_bios);

    it = ui_add(s, "Dọn ROM trùng", "SHA-256", ST_YELLOW);
    ui_exp(it, "Tìm ROM giống hệt nhau từng byte (băm SHA-256) giữa/ trong hai thẻ để giải phóng dung lượng.");
    ui_exp(it, "Bước 1 chọn giữ bản ở thẻ nào, bước 2 xem từng nhóm, bước 3 xác nhận mới xoá.");
    ui_exp(it, "! Có thể mất vài phút trên máy thật (đã đo ~260 giây).");
    ui_exp(it, "+ Bản có save không bao giờ bị xoá; game có playlist chỉ giữ bản trong playlist.");
    ui_act(it, 0, "BẮT ĐẦU", open_dedupe);

    it = ui_add(s, "Nhập game", "Import/", ST_UNKNOWN);
    ui_exp(it, "Nhập game bạn TỰ ĐƯA VÀO máy: thư mục Import/ ở gốc mỗi thẻ (file ROM, .zip, thư mục). Chỉ nhập nội dung bạn có quyền sử dụng.");
    ui_exp(it, "Tự nhận diện hệ máy bằng header (không đoán): mơ hồ thì hỏi bạn. Cài vào thẻ PHỤ trước; thẻ Leaf chỉ khi bạn đồng ý.");
    ui_exp(it, "Giải nén an toàn trong thư mục tạm rồi mới chuyển vào Roms/; mất điện giữa chừng không để lại file nửa vời.");
    ui_exp(it, "! .7z/.rar chưa hỗ trợ trên máy: giải nén trên PC.");
    ui_act(it, 0, "MỞ", open_import);

    it = ui_add(s, "Thư viện riêng", "PocketBridge", ST_UNKNOWN);
    ui_exp(it, "Duyệt + tải + cài game từ agent PocketBridge do CHÍNH BẠN dựng (Pi/PC/Mac) và đã ghép đôi. LoongMate không có danh mục ROM nào.");
    ui_exp(it, "Kết nối qua Tailscale (SOCKS5 userspace), TLS ghim fingerprint; token lưu ở /userdata (0600).");
    ui_exp(it, "Tải có tiếp tục được (Range + If-Range), kiểm SHA-256 theo server, rồi cài bằng trình Nhập game.");
    ui_exp(it, "Ghép đôi: chép bundle vào <thẻ Leaf>/PocketBridge-import/ rồi chọn Ghép đôi thư viện.");
    ui_act(it, 0, "MỞ", open_lib);

    it = ui_add(s, "Homebrew", "tải trực tiếp", ST_UNKNOWN);
    ui_exp(it, "Tải game homebrew hợp pháp trực tiếp từ Homebrew Hub qua Wi-Fi (không cần PC): chọn rồi cài như một app.");
    ui_exp(it, "CHỈ hiện game có giấy phép rõ ràng (MIT, CC, GPL...); mục không giấy phép bị ẩn, không đoán.");
    ui_exp(it, "HTTPS kiểm chứng thư đầy đủ, chỉ máy chủ Homebrew Hub/GitHub; hash trong danh mục sai thì không cài.");
    ui_exp(it, "Tải có tiếp tục được (Range + If-Range), cài bằng trình Nhập game; ghi chú giấy phép lưu ngoài Roms/.");
    ui_act(it, 0, "MỞ", open_hb);

    it = ui_add(s, "Tải từ web", "https", ST_UNKNOWN);
    ui_exp(it, "Mở một trang web (https) bằng bàn phím trên màn hình, chọn file để tải rồi cài bằng Nhập game.");
    ui_exp(it, "Trước khi tải hiện tên file, dung lượng, hệ máy và MÁY CHỦ THẬT; hộp xác nhận mặc định Huỷ.");
    ui_exp(it, "! Trang cần JavaScript/đếm ngược có thể không tải được. Chỉ tải nội dung bạn có quyền sử dụng.");
    ui_act(it, 0, "MỞ", open_web);

    it = ui_add(s, "Game mới tải về", "gần đây", ST_UNKNOWN);
    ui_exp(it, "Game vừa cài bằng Nhập game / Tải từ web / Homebrew / Thư viện riêng, mới nhất lên đầu (theo installed.jsonl trên từng thẻ).");
    ui_exp(it, "Hiện ngày giờ cài, hệ máy, dung lượng, thẻ và đường dẫn. Chỉ đọc.");
    ui_act(it, 0, "MỞ", open_recent);

    it = ui_add(s, "Tìm kiếm game", "cả hai thẻ", ST_UNKNOWN);
    ui_exp(it, "Gõ một phần tên game (bàn phím trên màn hình) để tìm trên cả SD1 và SD2: hệ máy, thẻ, dung lượng, có save/state không.");
    ui_exp(it, "Lần đầu phải quét danh sách ROM (vài giây). Chỉ đọc.");
    ui_act(it, 0, "TÌM", open_roms_search);

    it = ui_add(s, "Patch ROM (romhack)", "", ST_UNKNOWN);
    ui_exp(it, "Áp file patch .ips/.bps/.ups lên ROM gốc của bạn, tạo file ROM MỚI cạnh bản gốc (bản gốc giữ nguyên, save riêng).");
    ui_exp(it, "BPS/UPS tự tìm đúng ROM gốc bằng CRC; IPS không có mã kiểm tra nên phải chọn tay.");
    ui_exp(it, "Lấy patch: Tải từ web (link .ips/.bps/.ups hoặc zip chứa patch) hoặc chép vào Import/ trên thẻ. Hộp xác nhận mặc định Huỷ.");
    ui_act(it, 0, "MỞ", open_patch);

    it = ui_add(s, "Xoá ROM", "chọn từng game", ST_YELLOW);
    ui_exp(it, "Chọn một game cụ thể để xoá: lọc theo thẻ, duyệt hệ máy > game. Xem danh sách file sẽ xoá (ROM, track, ảnh bìa, MB) trước khi xác nhận.");
    ui_exp(it, "Xoá VĨNH VIỄN, có ghi sổ xoá. Hộp xác nhận mặc định Huỷ.");
    ui_exp(it, "Save/state của game KHÔNG bị xoá (được giữ lại, thành mồ côi nếu bạn không chơi lại game đó).");
    ui_exp(it, "Game nhiều file (.cue/.m3u): chọn file .cue/.m3u để xoá trọn bộ; các track .bin của game có .cue được ẩn khỏi danh sách.");
    ui_act(it, 0, "MỞ", open_roms_delete);
}

/* `fix undo --check` (chỉ đọc): cho biết có lô để hoàn tác không. then: việc kế tiếp (có thể NULL). */
static void (*g_undo_then)(void);
static void undo_check_done(lm_job *j, const char *arg) {
    int push = atoi(arg);
    cJSON *r = cJSON_Parse(lm_job_stdout(j));
    cJSON_Delete(g_undo);
    g_undo = (cJSON_IsObject(r) && jget(r, "has_batch")) ? r : NULL;
    if (!g_undo) cJSON_Delete(r);
    if (push) ui_push(ui_screen_new("Library", build_library)); else ui_rebuild_all();
    void (*then)(void) = g_undo_then;
    g_undo_then = NULL;
    if (then) then();
}
static void undo_check(int push, void (*then)(void)) {
    g_undo_then = then;
    if (!cli_task("Đang kiểm tra lô hoàn tác", SHORT_TIMEOUT_MS, undo_check_done, push ? "1" : "0", "fix", "undo", "--check", NULL)) {
        g_undo_then = NULL;
        if (push) ui_push(ui_screen_new("Library", build_library));
    }
}
void open_library_screen(void) { undo_check(1, NULL); }

/* ---- quét ---- */
static void scan_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Library Doctor");     /* thoát mã 3 khi có vấn đề nghiêm trọng: JSON vẫn hợp lệ */
    if (!r) return;
    if (!jarr(r, "issues")) {
        lines_t L = {0};
        lines_add(&L, "!! Quét thất bại:");
        lines_add(&L, "%s", jstrd(r, "message", "engine không trả báo cáo"));
        cJSON_Delete(r);
        ui_info("Library Doctor", &L);
        return;
    }
    cJSON_Delete(g_report);
    g_report = r;
    cJSON *sev = jget(r, "severity_counts");
    char hhmmss[16];
    time_t now = time(NULL);
    strftime(hhmmss, sizeof hhmmss, "%H:%M:%S", localtime(&now));
    lines_t L = {0};
    lines_add(&L, "+ Đã quét xong lúc %s", hhmmss);
    lines_add(&L, "Tổng: %.0f vấn đề", jnumd(r, "total_issues", 0));
    lines_add(&L, "Nghiêm trọng: %.0f  Cảnh báo: %.0f  Ghi chú: %.0f", jnumd(sev, "critical", 0), jnumd(sev, "warning", 0), jnumd(sev, "info", 0));
    int nbatch = 0;
    cJSON *ix;
    cJSON_ArrayForEach(ix, jarr(r, "issues")) if (!strcmp(jstrd(ix, "action_type", ""), "batch_fix")) nbatch++;
    lines_add(&L, "%sSửa an toàn: %d việc · Sửa hàng loạt: %d mục", (jarr_n(r, "fixable_actions") || nbatch) ? "+ " : "",
              jarr_n(r, "fixable_actions"), nbatch);
    lines_add(&L, "Xem và sửa: Library > Danh sách vấn đề.");
    ui_info("Library Doctor", &L);
    app_refresh();
}
static void do_scan(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang quét thư viện", LONG_TIMEOUT_MS, scan_done, "", "scan", NULL);
}

/* ---- danh sách vấn đề: việc sửa được lên đầu; mỗi dòng nói rõ có sửa được không và sửa ở đâu ---- */
static void build_issues(ui_screen *s) {
    cJSON *issues = g_report ? jarr(g_report, "issues") : NULL, *i;
    int nfix = g_report ? jarr_n(g_report, "fixable_actions") : 0, nbatch = 0;
    cJSON_ArrayForEach(i, issues) if (!strcmp(jstrd(i, "action_type", ""), "batch_fix")) nbatch++;
    ui_hdr(s, "%d mục từ lần quét gần nhất. Ghi chú là thông tin, không phải lỗi.", issues ? cJSON_GetArraySize(issues) : 0);
    if (nfix || nbatch) {
        ui_head(s, "Sửa ngay");
        if (nfix) {
            ui_item *it = ui_add(s, "Sửa an toàn", "", ST_GREEN);
            snprintf(it->tag, sizeof it->tag, "%d việc", nfix);
            ui_exp(it, "Ghép ảnh bìa lệch tên vào đúng game, cất ảnh thừa, tạo thư mục chuẩn, sửa .cue...");
            ui_exp(it, "Mọi file bị thay đều vào Quarantine (khôi phục được). Xem danh sách rồi xác nhận (mặc định Huỷ).");
            ui_act(it, 0, "XEM", ask_fixes);
        }
        if (nbatch) {
            ui_item *it = ui_add(s, "Sửa hàng loạt", "", ST_YELLOW);
            snprintf(it->tag, sizeof it->tag, "%d mục", nbatch);
            ui_exp(it, "Đưa game Leaf không hiện về đúng thư mục (vd. CPS1 -> ARCADE, .gbc trong GB -> GBC), gộp thư mục tên cũ.");
            ui_exp(it, "Có nhật ký và Hoàn tác. Xem kế hoạch từng bước rồi xác nhận.");
            ui_act(it, 0, "XEM", open_batchfix);
        }
    }
    for (int k = 0; k < 3; k++) {
        int cnt = 0;
        cJSON_ArrayForEach(i, issues) if (!strcmp(jstrd(i, "severity", ""), SEV_KEYS[k])) cnt++;
        if (!cnt) continue;
        ui_head(s, "%s (%d)", SEV_NAME[k], cnt);
        cJSON_ArrayForEach(i, issues) {
            if (strcmp(jstrd(i, "severity", ""), SEV_KEYS[k])) continue;
            const char *at = jstrd(i, "action_type", "");
            int batch = !strcmp(at, "batch_fix");
            ui_item *it = ui_add(s, jstrd(i, "title", "?"), batch ? "sửa được" : "", batch ? ST_GREEN : SEV_COLOR[k]);
            ui_exp(it, "%s", jstrd(i, "title", "?"));
            ui_exp(it, "Mức độ: %s", SEV_NAME[k]);
            ui_exp(it, "Mã lỗi: %s", jstrd(i, "code", "?"));
            ui_exp(it, "%s", "");
            ui_exp(it, "%s", jstrd(i, "description", ""));
            if (jstr(i, "guide")) { ui_exp(it, "%s", ""); ui_exp(it, "Cách làm: %s", jstr(i, "guide")); }
            int ni = jarr_n(i, "items");
            if (ni > 8) {                       /* mô tả đã có 8 tên đầu: liệt kê đủ khi mở rộng */
                ui_exp(it, "%s", "");
                ui_exp(it, "Toàn bộ (%d):", ni);
                cJSON *x;
                int shown = 0;
                cJSON_ArrayForEach(x, jarr(i, "items")) {
                    if (shown++ >= 60) { ui_exp(it, "... và %d mục khác", ni - 60); break; }
                    if (cJSON_IsString(x)) ui_exp(it, "  %s", x->valuestring);
                }
            }
            if (batch) ui_act(it, 0, "SỬA", open_batchfix);
        }
    }
}
static void open_issues(ui_screen *s, ui_item *it) { (void)s; (void)it; ui_push(ui_screen_new("Danh sách vấn đề", build_issues)); }

/* ---- sửa an toàn ---- */
static void fixes_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả áp dụng sửa");
    if (!r) return;
    lines_t L = {0};
    if (!jbool_d(r, "fix_applied", 0)) {
        lines_add(&L, "!! Chưa áp dụng sửa nào:");
        lines_add(&L, "%s", jstrd(r, "fix_refused_reason", "engine báo không có sửa nào được áp dụng"));
    } else {
        cJSON *fr;
        cJSON_ArrayForEach(fr, jarr(r, "fix_results")) {
            int ok = jbool_d(fr, "success", 0);
            lines_add(&L, "%s%s: %s (%s)", ok ? "+ " : "!! ", jstrd(fr, "label", "?"), ok ? "Thành công" : "Thất bại", jstrd(fr, "message", ""));
        }
        lines_add(&L, "%s", "");
        lines_add(&L, "Hãy Quét lại để cập nhật danh sách vấn đề.");
    }
    cJSON_Delete(r);
    cJSON_Delete(g_report);
    g_report = NULL;
    ui_info("Kết quả áp dụng sửa", &L);
    app_refresh();
}
static void do_fixes(const char *arg) {
    (void)arg;
    cli_task("Đang áp dụng sửa", LONG_TIMEOUT_MS, fixes_done, "", "scan", "--fix", "--yes", NULL);
}
static void ask_fixes(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cJSON *fix = g_report ? jarr(g_report, "fixable_actions") : NULL;
    lines_t L = {0};
    lines_add(&L, "Sẽ thực hiện %d việc sau, lần lượt:", fix ? cJSON_GetArraySize(fix) : 0);
    cJSON *a;
    cJSON_ArrayForEach(a, fix) lines_add(&L, "• %s", jstrd(a, "label", "?"));
    lines_add(&L, "%s", "");
    lines_add(&L, "Mỗi việc chỉ ghi trong vùng cho phép; kết quả từng việc sẽ hiện sau đó.");
    lines_add(&L, "! Engine quét lại ngay trước khi áp dụng, nên danh sách thực tế có thể khác nếu thẻ vừa thay đổi; kết quả hiện đúng những gì đã làm.");
    ui_confirm("Áp dụng sửa an toàn", &L, "Áp dụng", do_fixes, "");
}

/* ---- sửa hàng loạt + hoàn tác ---- */
#define NKIND 5
static const char *const KIND_ORDER[NKIND] = { "create_dirs", "fix_cue", "create_m3u", "legacy_folder", "move_misplaced" };
static const char *kind_vi(const char *k) {
    if (!strcmp(k, "create_dirs")) return "Tạo thư mục chuẩn";
    if (!strcmp(k, "fix_cue")) return "Sửa tham chiếu .cue";
    if (!strcmp(k, "create_m3u")) return "Tạo playlist .m3u";
    if (!strcmp(k, "legacy_folder")) return "Gộp thư mục tên cũ";
    if (!strcmp(k, "move_misplaced")) return "Chuyển game sai thư mục";
    return k;
}
static const char *risk_vi(const char *r) { return !strcmp(r, "low") ? "thấp" : !strcmp(r, "medium") ? "TRUNG BÌNH" : r; }

static void build_batchfix(ui_screen *s) {
    cJSON *plan = s->d, *steps = jarr(plan, "steps"), *st;
    int n = steps ? cJSON_GetArraySize(steps) : 0, nmed = 0, nirr = 0;
    cJSON_ArrayForEach(st, steps) {
        if (strcmp(jstrd(st, "risk", ""), "low")) nmed++;
        if (!jbool_d(st, "reversible", 0)) nirr++;
    }
    if (!n) {
        ui_hdr(s, "%s", jstrd(plan, "summary", "Không có gì để sửa tự động."));
        ui_head(s, "(Không có gì để sửa tự động)");
        return;
    }
    char hdr[400] = "";
    for (int k = 0; k < NKIND; k++) {
        int c = 0;
        cJSON_ArrayForEach(st, steps) if (!strcmp(jstrd(st, "kind", ""), KIND_ORDER[k])) c++;
        if (c) { size_t l = strlen(hdr); snprintf(hdr + l, sizeof hdr - l, "%s%s x%d", l ? ", " : "", kind_vi(KIND_ORDER[k]), c); }
    }
    ui_hdr(s, "%d bước: %s", n, hdr);
    char tag[48], lab[64];
    snprintf(lab, sizeof lab, "Áp dụng %d bước", n);
    snprintf(tag, sizeof tag, "%d rủi ro TB", nmed);
    ui_item *it = ui_add(s, lab, nmed ? tag : "rủi ro thấp", nmed ? ST_YELLOW : ST_GREEN);
    ui_exp(it, "Thực hiện lần lượt %d bước bên dưới; lỗi một bước không chặn các bước khác.", n);
    ui_exp(it, "Rủi ro trung bình: %d bước. Không hoàn tác được: %d bước.", nmed, nirr);
    ui_exp(it, "Bước sau là hộp xác nhận (mặc định Huỷ).");
    extern void lm_ask_batchfix(ui_screen *s, ui_item *it);
    ui_act(it, 0, "TIẾP: XÁC NHẬN", lm_ask_batchfix);
    ui_head(s, "Các bước (theo thứ tự thực hiện)");
    for (int k = 0; k < NKIND; k++) {
        cJSON_ArrayForEach(st, steps) {
            if (strcmp(jstrd(st, "kind", ""), KIND_ORDER[k])) continue;
            const char *risk = jstrd(st, "risk", "?");
            int low = !strcmp(risk, "low");
            it = ui_add(s, jstrd(st, "label", "?"), low ? "thấp" : !strcmp(risk, "medium") ? "vừa" : risk, low ? ST_GREEN : ST_YELLOW);
            ui_exp(it, "%s", kind_vi(jstrd(st, "kind", "?")));
            ui_exp(it, "%s", "");
            ui_exp(it, "%s", jstrd(st, "detail", ""));
            ui_exp(it, "%s", "");
            ui_exp(it, "Số file ảnh hưởng: %.0f", jnumd(st, "files_affected", 0));
            ui_exp(it, "%sRủi ro: %s", low ? "" : "! ", risk_vi(risk));
            ui_exp(it, jbool_d(st, "reversible", 0) ? "+ Hoàn tác được: có" : "!! Hoàn tác được: KHÔNG");
        }
    }
}

static void batchfix_free(ui_screen *s) { (void)s; plan_remove("batchfix"); }

static void batchfix_plan_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *plan = app_json_or_error(j, "Sửa hàng loạt");
    if (!plan) { plan_remove("batchfix"); return; }
    if (!jarr(plan, "steps")) {
        lines_t L = {0};
        lines_add(&L, "!! Không lập được kế hoạch:");
        lines_add(&L, "%s", jstrd(plan, "message", "engine không trả kế hoạch"));
        cJSON_Delete(plan);
        plan_remove("batchfix");
        ui_info("Sửa hàng loạt", &L);
        return;
    }
    if (jarr_n(plan, "steps") && !app_plan_written(plan, "batchfix", "Sửa hàng loạt")) { cJSON_Delete(plan); plan_remove("batchfix"); return; }
    ui_screen *s = ui_screen_new("Sửa hàng loạt", build_batchfix);
    s->d = plan;
    s->on_free = batchfix_free;     /* đóng màn = huỷ kế hoạch: xoá file */
    ui_push(s);
}
static void open_batchfix(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    plan_remove("batchfix");        /* không bao giờ dùng lại file của lần trước */
    cli_task("Đang quét & lập kế hoạch", LONG_TIMEOUT_MS, batchfix_plan_done, "", "fix", "batch", "--plan-out", plan_file("batchfix"), NULL);
}

static void batchfix_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả sửa hàng loạt");
    if (!r) { plan_remove("batchfix"); return; }
    lines_t L = {0};
    int rejected = app_plan_rejected(j, r, "Sửa hàng loạt");
    if (!rejected) {
        cJSON *done = jarr(r, "applied"), *refused = jarr(r, "refused"), *d;
        int nd = done ? cJSON_GetArraySize(done) : 0, nr = refused ? cJSON_GetArraySize(refused) : 0;
        lines_add(&L, "%sĐã làm %d/%d bước trong kế hoạch đã xem, từ chối %d.", nd && !nr ? "+ " : "", nd, g_plan_n, nr);
        cJSON_ArrayForEach(d, done) {
            if (!strcmp(jstrd(d, "kind", ""), "legacy_folder"))
                lines_add(&L, "+ %s: chuyển %.0f, trùng vào Quarantine %.0f, bỏ qua %.0f%s", jstrd(d, "label", "?"), jnumd(d, "moved", 0),
                          jnumd(d, "quarantined_duplicates", 0), jnumd(d, "skipped", 0),
                          jbool_d(d, "source_folder_removed", 0) ? "" : " (thư mục cũ còn sót file nên được giữ)");
            else lines_add(&L, "+ %s", jstrd(d, "label", jstrd(d, "path", "?")));
        }
        cJSON *x;
        cJSON_ArrayForEach(x, refused)
            lines_add(&L, "!! TỪ CHỐI [%s] %s: %s", kind_vi(jstrd(x, "kind", "?")), jstrd(x, "path", "?"), jstrd(x, "reason", ""));
        if (nr) lines_add(&L, "(Mục bị từ chối: phần đó đã đổi hoặc mới xuất hiện từ lúc bạn xem — nó được để nguyên.)");
        lines_add(&L, "Lô: %s", jstrd(r, "batch_id", "None"));
        lines_add(&L, "Nhật ký: %s", jstrd(r, "journal", "None"));
        lines_add(&L, "Hoàn tác: Library > Hoàn tác lô sửa gần nhất.");
        app_add_rescan_lines(&L, jget(r, "rescan"));
    }
    cJSON_Delete(r);
    plan_remove("batchfix");
    cJSON_Delete(g_report);
    g_report = NULL;
    clear_dup();
    ui_pop_to(2);       /* về menu Library */
    if (!rejected) ui_info("Kết quả sửa hàng loạt", &L);
    undo_check(0, app_refresh);
}
static void do_batchfix(const char *arg) {
    (void)arg;
    cli_task("Đang sửa hàng loạt", LONG_TIMEOUT_MS, batchfix_done, "", "fix", "batch", "--apply", "--plan", plan_file("batchfix"), "--yes", NULL);
}

void lm_ask_batchfix(ui_screen *s, ui_item *it) {
    (void)it;
    cJSON *steps = jarr(s->d, "steps"), *st;
    int n = steps ? cJSON_GetArraySize(steps) : 0;
    g_plan_n = n;
    lines_t L = {0};
    lines_add(&L, "Sẽ làm ĐÚNG %d bước đã xem (kế hoạch %.12s), theo thứ tự:", n, jstrd(s->d, "plan_id", "?"));
    for (int k = 0; k < NKIND; k++) {
        cJSON_ArrayForEach(st, steps) {
            if (strcmp(jstrd(st, "kind", ""), KIND_ORDER[k])) continue;
            int low = !strcmp(jstrd(st, "risk", ""), "low");
            lines_add(&L, "%s[%s] %s — %s", low ? "" : "! ", risk_vi(jstrd(st, "risk", "?")), jstrd(st, "label", "?"),
                      jbool_d(st, "reversible", 0) ? "hoàn tác được" : "KHÔNG hoàn tác được");
        }
    }
    lines_add(&L, "%s", "");
    lines_add(&L, "+ Engine không quét lại để tìm thêm việc: chỉ các bước trong kế hoạch này. Phần nào đã đổi từ lúc bạn xem (file .cue đã sửa, thư mục có file mới) sẽ bị TỪ CHỐI riêng, kèm lý do.");
    lines_add(&L, "• Sửa .cue luôn sao lưu bản gốc thành <tên>.cue.bak-loongmate trước.");
    lines_add(&L, "• Gộp thư mục: trùng tên cùng nội dung -> Quarantine; trùng tên khác nội dung -> bỏ qua, nguồn giữ nguyên.");
    lines_add(&L, "• Nhật ký hoàn tác: batchfix-journal.jsonl trong thư mục dữ liệu LoongMate (đường dẫn đầy đủ hiện sau khi áp dụng).");
    lines_add(&L, "• Hoàn tác sau đó: Library > Hoàn tác lô sửa gần nhất.");
    ui_confirm("Xác nhận sửa hàng loạt", &L, "Áp dụng", do_batchfix, "");
}

static void undo_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Hoàn tác lô sửa");
    if (!r) return;
    lines_t L = {0};
    if (cJSON_IsNull(jget(r, "batch_id")) || !jget(r, "batch_id")) {
        lines_add(&L, "Không có lô nào để hoàn tác.");
        cJSON_Delete(r);
        ui_info("Hoàn tác lô sửa", &L);
        undo_check(0, NULL);
        return;
    }
    cJSON *undone = jarr(r, "undone"), *skipped = jarr(r, "skipped"), *u;
    int nu = undone ? cJSON_GetArraySize(undone) : 0;
    lines_add(&L, "%sLô %s: đã hoàn tác %d thao tác, bỏ qua %d.", nu ? "+ " : "", jstrd(r, "batch_id", "?"), nu, skipped ? cJSON_GetArraySize(skipped) : 0);
    cJSON_ArrayForEach(u, undone) lines_add(&L, "+ %s: %s", jstrd(u, "op", "?"), jstrd(u, "path", "?"));
    cJSON_ArrayForEach(u, skipped) lines_add(&L, "! BỎ QUA %s %s: %s", jstrd(u, "op", "?"), jstrd(u, "path", "?"), jstrd(u, "reason", ""));
    app_add_rescan_lines(&L, jget(r, "rescan"));
    cJSON_Delete(r);
    cJSON_Delete(g_report);
    g_report = NULL;
    clear_dup();
    ui_info("Kết quả hoàn tác", &L);
    undo_check(0, app_refresh);
}
static void do_undo(const char *arg) {
    (void)arg;
    cli_task("Đang hoàn tác", LONG_TIMEOUT_MS, undo_done, "", "fix", "undo", "--yes", NULL);
}
static void ask_undo(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    lines_t L = {0};
    if (jbool_d(g_undo, "has_batch", 0)) {
        lines_add(&L, "Sẽ hoàn tác lô %s (%s): %.0f thao tác.", jstrd(g_undo, "batch_id", "?"), jstrd(g_undo, "created_at", "không rõ lúc"), jnumd(g_undo, "operations", 0));
        cJSON *op;
        cJSON_ArrayForEach(op, jget(g_undo, "by_op")) if (cJSON_IsNumber(op)) lines_add(&L, "• %s x%.0f", op->string, op->valuedouble);
    } else lines_add(&L, "Sẽ hoàn tác lô sửa hàng loạt gần nhất chưa hoàn tác.");
    lines_add(&L, "• Theo thứ tự ngược; trạng thái thật trên đĩa quyết định.");
    lines_add(&L, "• File đã bị sửa sau lô đó, hoặc đường dẫn cũ đã bị chiếm: BỎ QUA, không ghi đè.");
    lines_add(&L, "• Kết quả hiện từng nhóm: đã hoàn tác / bỏ qua kèm lý do.");
    ui_confirm("Hoàn tác lô sửa hàng loạt", &L, "Hoàn tác", do_undo, "");
}

/* ---- BIOS ---- */
static void build_bios(ui_screen *s) {
    cJSON *rep = s->d, *sy;
    int ok = 0, wrong = 0, missing = 0, unknown = 0, optmiss = 0;
    cJSON *systems = jarr(rep, "systems");
    ui_item *imp = ui_add(s, "Cài BIOS từ Import/", "Nhập game", ST_UNKNOWN);
    ui_exp(imp, "Có file BIOS bạn tự trích từ máy của mình: chép vào thư mục Import/ trên thẻ, rồi A.");
    ui_exp(imp, "Nhập game nhận ra BIOS bằng md5, đổi đúng tên và đặt đúng thư mục emulator đọc (vd. BIOS/dc/ cho Dreamcast).");
    ui_exp(imp, "LoongMate không tải BIOS từ Internet (BIOS là phần mềm có bản quyền của hãng máy).");
    ui_act(imp, 0, "MỞ", open_import);
    cJSON_ArrayForEach(sy, systems) {
        int hle = jstr(sy, "hle") != NULL;
        ui_head(s, hle ? "%s · chạy được bằng BIOS giả lập" : "%s", jstrd(sy, "system", "?"));
        cJSON *e;
        cJSON_ArrayForEach(e, jarr(sy, "required")) {
            const char *st = jstrd(e, "status", "?");
            const char *tag, *meaning;
            int color;
            if (!strcmp(st, "ok")) { tag = "OK"; color = ST_GREEN; meaning = "md5 khớp nguồn công khai."; }
            else if (!strcmp(st, "wrong_hash")) { tag = "SAI HASH"; color = ST_RED; meaning = "Có file nhưng md5 không nằm trong danh sách hợp lệ."; }
            else if (!strcmp(st, "missing")) { tag = "THIẾU"; color = ST_YELLOW; meaning = "Không thấy trong BIOS/ của thẻ nào."; }
            else if (!strcmp(st, "misplaced")) { tag = "SAI CHỖ"; color = ST_YELLOW; meaning = "Có file nhưng không nằm trong thư mục emulator đọc."; }
            else { tag = "chưa có hash"; color = ST_UNKNOWN; meaning = "Chưa có md5 nguồn công khai nên chỉ biết là file có tồn tại."; }
            int opt = jbool_d(e, "optional", 0);
            char stat[64];
            if (opt && !strcmp(st, "missing")) {      /* thiếu file không bắt buộc KHÔNG phải lỗi */
                optmiss++;
                if (hle) { tag = "giả lập"; color = ST_GREEN; meaning = "chưa có BIOS thật, emulator đang dùng BIOS giả lập."; }
                else { tag = "không bắt buộc"; color = ST_UNKNOWN; meaning = "file này không bắt buộc nên thiếu không phải lỗi."; }
                snprintf(stat, sizeof stat, hle ? "chưa có (đang dùng BIOS giả lập)" : "THIẾU (không bắt buộc)");
            } else {
                if (!strcmp(st, "ok")) ok++; else if (!strcmp(st, "wrong_hash")) wrong++; else if (!strcmp(st, "missing")) missing++; else unknown++;
                snprintf(stat, sizeof stat, "%s", tag);
            }
            ui_item *it = ui_add(s, jstrd(e, "name", "?"), tag, color);
            ui_exp(it, "Hệ máy: %s", jstrd(sy, "system", "?"));
            ui_exp(it, "File: %s", jstrd(e, "name", "?"));
            ui_exp(it, "%sTrạng thái: %s — %s", color == ST_RED ? "!! " : color == ST_YELLOW ? "! " : "", stat, meaning);
            if (opt && strcmp(st, "missing")) ui_exp(it, "Không bắt buộc.");
            if (jstr(e, "found_path")) {
                ui_exp(it, "Đường dẫn: %s", jstr(e, "found_path"));
                ui_exp(it, "md5 hiện có: %s", jstrd(e, "md5", "(không băm được)"));
            }
            {
                cJSON *exm = jarr(e, "expected_md5"), *m;
                if (exm && cJSON_GetArraySize(exm)) {
                    ui_exp(it, "md5 mong đợi (%d giá trị hợp lệ):", cJSON_GetArraySize(exm));
                    cJSON_ArrayForEach(m, exm) if (cJSON_IsString(m)) ui_exp(it, "  %s", m->valuestring);
                } else ui_exp(it, "md5 mong đợi: chưa có nguồn công khai trong bios_hashes.json.");
            }
            ui_exp(it, "%s", "");
            ui_exp(it, "%s", jstrd(e, "note", ""));
        }
    }
    if (!s->n) ui_head(s, "(Không hệ máy nào đang có ROM cần BIOS)");
    ui_hdr(s, "%d hệ máy cần BIOS • ok %d • sai hash %d • thiếu %d • chưa có hash nguồn %d • thiếu nhưng không bắt buộc %d",
           systems ? cJSON_GetArraySize(systems) : 0, ok, wrong, missing, unknown, optmiss);
}
static void bios_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *rep = app_json_or_error(j, "Kiểm tra BIOS");
    if (!rep) return;
    if (!jarr(rep, "systems")) {
        lines_t L = {0};
        lines_add(&L, "!! Không kiểm tra được BIOS:");
        lines_add(&L, "%s", jstrd(rep, "message", "engine không trả bảng BIOS"));
        cJSON_Delete(rep);
        ui_info("Kiểm tra BIOS", &L);
        return;
    }
    ui_screen *s = ui_screen_new("Kiểm tra BIOS", build_bios);
    s->d = rep;
    ui_push(s);
}
static void open_bios(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cli_task("Đang kiểm tra BIOS", LONG_TIMEOUT_MS, bios_done, "", "bios", NULL);
}

/* ============================================================ Dọn ROM trùng (3 bước) */
static const char *const PREFER_KEY[3] = { "auto", "primary", "secondary" };
static const char *const PREFER_NAME[3] = { "Tự động", "Giữ bản ở SD1", "Giữ bản ở SD2" };
static const char *const PREFER_WHY[3] = {
    "(Khuyến nghị) Giữ bản có save > có ảnh bìa > thư mục hệ máy chuẩn > thẻ đang trống hơn > đường dẫn ngắn.",
    "Ưu tiên giữ bản trên SD1 và xoá bản trùng ở SD2 (sau tiêu chí save).",
    "Ưu tiên giữ bản trên SD2 và xoá bản trùng ở SD1 — giải phóng SD1 đang chật." };

static void open_dup_groups(int k);

static void dedupe_free(ui_screen *s) { (void)s; clear_dup(); }   /* rời bước 1 = bỏ kế hoạch: xoá file */

static void toggle_dup_art(ui_screen *s, ui_item *it) {
    (void)it;
    g_dup_art = !g_dup_art;
    clear_dup();            /* cờ nằm trong kế hoạch: kế hoạch cũ không còn đúng ý người dùng */
    ui_rebuild_all();
}

static void build_dedupe(ui_screen *s) {
    ui_hdr(s, "Bước 1/3: chọn giữ bản ở thẻ nào. Bước 2 lập và hiện kế hoạch; bước 3 xoá ĐÚNG các file trong kế hoạch đó.");
    if (g_dup) ui_hdr(s, "Đã băm %.0f file (%s).", jnumd(g_dup, "hashed_files", 0), mbs(jnumd(g_dup, "hashed_bytes", 0)));
    extern void lm_dedupe_go(ui_screen *s, ui_item *it);
    for (int k = 0; k < 3; k++) {
        int have = g_dup && g_dup_k == k;
        cJSON *by = have ? jget(g_dup, "reclaimable_by_card") : NULL;
        ui_item *it = ui_add(s, PREFER_NAME[k], "", have ? ST_GREEN : ST_UNKNOWN);
        ui_exp(it, "%s", PREFER_WHY[k]);
        ui_exp(it, "Bản có save/state không bao giờ bị xoá; bản đang trong playlist luôn được giữ — bất kể lựa chọn.");
        ui_exp(it, "%s", "");
        if (by) {
            ui_exp(it, "Thu hồi trên SD1: %s", mbs(jnumd(by, "primary", 0)));
            ui_exp(it, "Thu hồi trên SD2: %s", mbs(jnumd(by, "secondary", 0)));
            snprintf(it->tag, sizeof it->tag, "SD1 %s / SD2 %s", mbs(jnumd(by, "primary", 0)), mbs(jnumd(by, "secondary", 0)));
        } else {
            ui_exp(it, "Chưa tính. Bấm A để băm các file nghi trùng (có thể vài phút) và lập kế hoạch.");
            snprintf(it->tag, sizeof it->tag, "chưa tính");
        }
        ui_exp(it, have ? "Kế hoạch đã lập được nhớ trong phiên (file kế hoạch giữ nguyên tới khi bạn rời màn này)." : "! Chọn mục này sẽ băm và lập kế hoạch mới (bỏ kế hoạch của lựa chọn khác nếu có).");
        it->iarg = k;
        ui_act(it, 0, have ? "XEM NHÓM" : "CHỌN & TÍNH", lm_dedupe_go);
    }
    ui_item *it = ui_add(s, "Chép ảnh bìa sang thẻ giữ", g_dup_art ? "BẬT" : "TẮT", g_dup_art ? ST_GREEN : ST_UNKNOWN);
    ui_exp(it, "Trước khi xoá, nếu thẻ chứa bản GIỮ chưa có ảnh bìa mà thẻ kia có, ảnh được chép sang (xác minh SHA-256, không ghi đè ảnh có sẵn).");
    ui_exp(it, "Số nhóm thực sự cần chép ảnh: engine chưa báo trước (CLI không có số này); kết quả cuối cho biết đã chép bao nhiêu.");
    ui_exp(it, "Lỗi chép ảnh không chặn việc xoá.");
    ui_exp(it, "! Cờ này nằm TRONG kế hoạch: đổi nó sẽ bỏ kế hoạch đã lập và phải tính lại.");
    ui_exp(it, "# CHƯA KIỂM CHỨNG: Leaf có tự tìm ảnh bìa chéo thẻ hay không; chép thừa một ảnh nhỏ còn hơn mất ảnh.");
    ui_act(it, 0, "BẬT/TẮT", toggle_dup_art);
}
static void open_dedupe(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    ui_screen *sc = ui_screen_new("Dọn ROM trùng", build_dedupe);
    sc->on_free = dedupe_free;
    ui_push(sc);
}

static void dedupe_scan_done(lm_job *j, const char *arg) {
    int k = atoi(arg);
    cJSON *rep = app_json_or_error(j, "Dọn ROM trùng");
    if (!rep) { plan_remove("dedupe"); return; }
    if (!jarr(rep, "groups")) {
        lines_t L = {0};
        lines_add(&L, "!! Không tìm được ROM trùng:");
        lines_add(&L, "%s", jstrd(rep, "message", "engine không trả danh sách nhóm"));
        cJSON_Delete(rep);
        plan_remove("dedupe");
        ui_info("Dọn ROM trùng", &L);
        return;
    }
    if (!app_plan_written(rep, "dedupe", "Dọn ROM trùng")) { cJSON_Delete(rep); plan_remove("dedupe"); return; }
    cJSON_Delete(g_dup);
    g_dup = rep;
    g_dup_k = k;
    ui_rebuild_all();
    open_dup_groups(k);
}
void lm_dedupe_go(ui_screen *s, ui_item *it) {
    (void)s;
    int k = it->iarg;
    if (g_dup && g_dup_k == k) { open_dup_groups(k); return; }
    clear_dup();            /* không bao giờ dùng lại file kế hoạch của lần trước */
    char arg[8];
    snprintf(arg, sizeof arg, "%d", k);
    /* --copy-artwork ở CUỐI: NULL kết thúc danh sách đối số khi tắt */
    cli_task("Đang băm tìm ROM trùng & lập kế hoạch", LONG_TIMEOUT_MS, dedupe_scan_done, arg, "dedupe", "--prefer-card", PREFER_KEY[k],
             "--plan-out", plan_file("dedupe"), g_dup_art ? "--copy-artwork" : NULL, NULL);
}

static int dup_remove_count(const cJSON *rep) {
    int n = 0;
    cJSON *g;
    cJSON_ArrayForEach(g, jarr(rep, "groups")) n += jarr_n(g, "remove");
    return n;
}

static void dedupe_apply_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả dọn ROM trùng");
    if (!r) { clear_dup(); ui_pop_to(2); return; }
    int planned = dup_remove_count(g_dup), art = g_dup_art;
    int rejected = app_plan_rejected(j, r, "Dọn ROM trùng");
    lines_t L = {0};
    if (!rejected) {
        cJSON *applied = jarr(r, "applied"), *refused = jarr(r, "refused"), *res;
        int nd = cJSON_GetArraySize(applied), nr = refused ? cJSON_GetArraySize(refused) : 0;
        lines_add(&L, "%sĐã xoá %d/%d file trong kế hoạch đã xem, giải phóng %s.", nd && !nr ? "+ " : "", nd, planned, mbs(jnumd(r, "bytes_freed", 0)));
        if (nr) lines_add(&L, "!! %d file bị TỪ CHỐI (vẫn còn nguyên trên thẻ):", nr);
        add_refused(&L, refused, NULL);
        cJSON *results = jarr(r, "results");
        if (art) {
            char sum[400] = "";
            const char *names[8]; int cnt[8], nn = 0;
            cJSON_ArrayForEach(res, results) {
                cJSON *a = jget(res, "artwork");
                if (!cJSON_IsObject(a)) continue;
                const char *stt = jstrd(a, "status", "?");
                int f = -1;
                for (int i = 0; i < nn; i++) if (!strcmp(names[i], stt)) f = i;
                if (f < 0 && nn < 8) { names[nn] = stt; cnt[nn] = 0; f = nn++; }
                if (f >= 0) cnt[f]++;
            }
            for (int i = 0; i < nn; i++) { size_t l = strlen(sum); snprintf(sum + l, sizeof sum - l, "%s%s %d", l ? ", " : "", names[i], cnt[i]); }
            lines_add(&L, "Ảnh bìa: %s  (copied = đã chép sang thẻ giữ)", sum[0] ? sum : "không có nhóm nào xử lý ảnh");
            cJSON_ArrayForEach(res, results) {
                cJSON *a = jget(res, "artwork");
                const char *stt = jstrd(a, "status", "");
                if (a && (!strcmp(stt, "error") || !strcmp(stt, "refused"))) lines_add(&L, "! Ảnh bìa %s: %s", stt, jstrd(a, "message", ""));
            }
        }
        const char *ledger = NULL;
        cJSON_ArrayForEach(res, results) if (!ledger) ledger = jstr(res, "ledger_path");
        if (ledger) lines_add(&L, "Sổ xoá: %s", ledger);
        app_add_rescan_lines(&L, jget(r, "rescan"));
    }
    cJSON_Delete(r);
    clear_dup();                 /* gồm xoá file kế hoạch */
    ui_pop_to(2);
    if (!rejected) ui_info("Kết quả dọn ROM trùng", &L);
    app_refresh();
}

static void do_dedupe(const char *arg) {
    (void)arg;
    /* --yes chỉ sau khi người dùng đã xác nhận. Không truyền --prefer-card/--copy-artwork/--quarantine: các tham số đã nằm TRONG kế hoạch. */
    cli_task("Đang xoá ROM trùng", LONG_TIMEOUT_MS, dedupe_apply_done, "", "dedupe", "--apply", "--plan", plan_file("dedupe"), "--yes", NULL);
}

static void ask_dedupe(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cJSON *rep = g_dup, *groups = jarr(rep, "groups");
    int n = dup_remove_count(rep), ng = groups ? cJSON_GetArraySize(groups) : 0;
    cJSON *by = jget(rep, "reclaimable_by_card");
    lines_t L = {0};
    lines_add(&L, "!! Sẽ làm ĐÚNG %d mục (nhóm) đã xem: XOÁ VĨNH VIỄN %d file (kế hoạch %.12s).", ng, n, jstrd(rep, "plan_id", "?"));
    lines_add(&L, "Thu hồi: %s (SD1 %s, SD2 %s).", mbs(jnumd(rep, "reclaimable_bytes", 0)), mbs(jnumd(by, "primary", 0)), mbs(jnumd(by, "secondary", 0)));
    lines_add(&L, "+ Chỉ các file trong danh sách bạn vừa xem bị đụng tới. Bản trùng mới xuất hiện SAU lần xem này sẽ KHÔNG bị xoá.");
    lines_add(&L, "Không dùng Quarantine: dung lượng được giải phóng ngay.");
    lines_add(&L, g_dup_art ? "Ảnh bìa: sẽ chép sang thẻ giữ trước khi xoá nếu thẻ giữ chưa có ảnh (xác minh SHA-256, không ghi đè)."
                            : "Ảnh bìa: KHÔNG chép (đã tắt).");
    lines_add(&L, "Ngay trước khi xoá, mỗi file được băm lại và so với bản giữ; file nào đã đổi, đã có save/state đi kèm, hoặc bản giữ đã mất sẽ bị TỪ CHỐI riêng (kết quả hiện lý do).");
    lines_add(&L, "Mọi lần xoá được ghi vào sổ xoá trong thư mục dữ liệu LoongMate (đường dẫn hiện sau khi xoá).");
    lines_add(&L, "Save/state không bao giờ bị xoá.");
    ui_confirm("Bước 3/3 — Xác nhận xoá ROM trùng", &L, "XOÁ", do_dedupe, "");
}

static void build_dup_groups(ui_screen *s) {
    int k = s->ia;
    cJSON *rep = g_dup;
    if (!rep || g_dup_k != k) { ui_head(s, "(Kế hoạch đã bị bỏ — quay lại và tính lại)"); return; }
    cJSON *groups = jarr(rep, "groups"), *by = jget(rep, "reclaimable_by_card"), *g, *r;
    int n_files = dup_remove_count(rep);
    ui_hdr(s, "Bước 2/3: %d nhóm trùng • xoá %d file = %s (SD1 %s, SD2 %s) • kế hoạch %.12s", groups ? cJSON_GetArraySize(groups) : 0, n_files,
           mbs(jnumd(rep, "reclaimable_bytes", 0)), mbs(jnumd(by, "primary", 0)), mbs(jnumd(by, "secondary", 0)), jstrd(rep, "plan_id", "?"));
    ui_hdr(s, "Danh sách dưới đây CHÍNH LÀ kế hoạch sẽ thực hiện. Ảnh bìa: %s.", g_dup_art ? "sẽ chép sang thẻ giữ" : "không chép");
    if (jnumd(rep, "skipped_budget_files", 0) > 0) ui_hdr(s, "! Bỏ qua %.0f file vượt giới hạn băm.", jnumd(rep, "skipped_budget_files", 0));
    int he = 0;
    cJSON_ArrayForEach(r, jarr(rep, "hash_errors")) if (he++ < 1) ui_hdr(s, "! Không băm được %s: %s", jstrd(r, "path", "?"), jstrd(r, "reason", ""));
    char tag[48];
    if (n_files) {
        snprintf(tag, sizeof tag, "%d file", n_files);
        ui_item *it = ui_add(s, "Xoá TẤT CẢ bản trùng", tag, ST_RED);
        ui_exp(it, "Sẽ xoá %d file, thu hồi %s.", n_files, mbs(jnumd(rep, "reclaimable_bytes", 0)));
        ui_exp(it, "SD1: %s   SD2: %s", mbs(jnumd(by, "primary", 0)), mbs(jnumd(by, "secondary", 0)));
        ui_exp(it, "Bước 3 là hộp xác nhận (mặc định Huỷ). Chỉ các file trong danh sách bên dưới bị đụng tới; mỗi file được băm lại ngay trước khi xoá.");
        ui_act(it, 0, "TIẾP: XÁC NHẬN", ask_dedupe);
    }
    ui_head(s, "Các nhóm trùng");
    cJSON_ArrayForEach(g, groups) {
        cJSON *keep = jget(g, "keep");
        snprintf(tag, sizeof tag, "-%s", mbs(jnumd(g, "size", 0) * jarr_n(g, "remove")));
        ui_item *it = ui_add(s, jstrd(keep, "name", "?"), tag, ST_YELLOW);
        ui_exp(it, "Game: %s (%s) — %s", jstrd(keep, "name", "?"), jstrd(keep, "system", "?"), mbs(jnumd(g, "size", 0)));
        ui_exp(it, "%s", "");
        ui_exp(it, "GIỮ:");
        ui_exp(it, "%s", jstrd(keep, "path", "?"));
        ui_exp(it, "Lý do: %s", jstrd(g, "keep_reason", ""));
        cJSON *o;
        cJSON_ArrayForEach(o, jarr(g, "also_kept")) ui_exp(it, "GIỮ (cũng cần): %s", jstrd(o, "path", "?"));
        ui_exp(it, "%s", "");
        ui_exp(it, "XOÁ:");
        cJSON_ArrayForEach(r, jarr(g, "remove"))
            ui_exp(it, "%s [%s]%s", jstrd(r, "path", "?"), jstrd(r, "card_id", "?"), jbool_d(r, "has_save", 0) ? " !! CÓ SAVE" : "");
        ui_exp(it, "%s", "");
        ui_exp(it, "+ Bản có save không bao giờ bị xoá; game có playlist chỉ giữ bản trong playlist.");
    }
    cJSON *sm = jarr(rep, "skipped_multifile");
    if (sm && cJSON_GetArraySize(sm)) {
        ui_head(s, "Game nhiều file nghi trùng (KHÔNG đề xuất xoá)");
        cJSON *m;
        cJSON_ArrayForEach(m, sm) {
            ui_item *it = ui_add(s, jstrd(m, "name", "?"), "chỉ báo", ST_UNKNOWN);
            ui_exp(it, "%s", jstrd(m, "reason", ""));
            cJSON *p;
            cJSON_ArrayForEach(p, jarr(m, "paths")) if (cJSON_IsString(p)) ui_exp(it, "%s", p->valuestring);
        }
    }
    if (!groups || !cJSON_GetArraySize(groups)) ui_head(s, "(Không có ROM trùng nào để dọn)");
}

static void open_dup_groups(int k) {
    ui_screen *s = ui_screen_new("ROM trùng — các nhóm", build_dup_groups);
    s->ia = k;
    ui_push(s);
}

/* ============================================================ Chuyển game giữa hai thẻ */
static const int CHUNK_GB[4] = { 1, 2, 3, 5 };

static void do_bulk_plan(ui_screen *s, ui_item *it);
static void move_free(ui_screen *s) { (void)s; plan_remove("move"); }   /* đóng màn kế hoạch = huỷ: xoá file */

static void build_transfer(ui_screen *s) {
    cJSON *st = jget(g_ready, "storage"), *p = jget(st, "primary"), *sec = jget(st, "secondary");
    char a[48], b[48];
    if (jbool_d(p, "mounted", 0)) snprintf(a, sizeof a, "%.0f MB trống", jnumd(p, "free_mb", 0)); else snprintf(a, sizeof a, "không có");
    if (jbool_d(sec, "mounted", 0)) snprintf(b, sizeof b, "%.0f MB trống", jnumd(sec, "free_mb", 0)); else snprintf(b, sizeof b, "không có");
    ui_hdr(s, "SD1: %s   SD2: %s", a, b);
    ui_hdr(s, "Các bước ở đây chỉ XEM kế hoạch; chưa chép/xoá gì cho tới khi bạn xác nhận. Xác nhận thực hiện đúng kế hoạch đã xem.");
    static const char *const cards[2] = { "primary", "secondary" };
    for (int i = 0; i < 2; i++) {
        const char *src = cards[i], *dst = cards[1 - i];
        ui_head(s, "Giải phóng %s (chuyển sang %s)", sd_name(src), sd_name(dst));
        for (int g = 0; g < 4; g++) {
            char lab[96];
            snprintf(lab, sizeof lab, "Để %s trống %d GB", sd_name(src), CHUNK_GB[g]);
            ui_item *it = ui_add(s, lab, "", ST_UNKNOWN);
            ui_exp(it, "Đề xuất game nên chuyển từ %s sang %s để %s còn trống ít nhất %d GB (quét ~15 giây).", sd_name(src), sd_name(dst), sd_name(src), CHUNK_GB[g]);
            ui_exp(it, "Ưu tiên game không có save; vừa đủ thì lấy game nhỏ nhất đủ lớn.");
            ui_exp(it, "Bỏ qua game có save/state sửa trong 60 phút qua (coi là đang chơi).");
            ui_exp(it, "Bước này chỉ liệt kê, chưa chuyển gì.");
            ui_arg(it, dst);
            it->iarg = CHUNK_GB[g] * 1024;
            ui_act(it, 0, "XEM GỢI Ý", do_bulk_plan);
        }
    }
    ui_head(s, "Chọn từng game");
    for (int i = 0; i < 2; i++) {
        char lab[64];
        snprintf(lab, sizeof lab, "Từ %s sang %s", sd_name(cards[i]), sd_name(cards[1 - i]));
        ui_item *it = ui_add(s, lab, "chọn game", ST_UNKNOWN);
        ui_exp(it, "Duyệt hệ máy > game trên %s, xem kế hoạch rồi mới xác nhận. Game nhiều file (.cue/.m3u): chọn file .cue/.m3u để chuyển trọn bộ; các track .bin của game có .cue được ẩn khỏi danh sách.", sd_name(cards[i]));
        ui_exp(it, "Bước xem kế hoạch chỉ liệt kê, chưa chép/xoá gì.");
        ui_arg(it, cards[i]);
        ui_act(it, 0, "MỞ", open_roms_move);
    }
}

void open_transfer_screen(ui_screen *s, ui_item *it) {
    (void)s; (void)it;
    cJSON *sec = jget(jget(g_ready, "storage"), "secondary");
    if (!jbool_d(sec, "mounted", 0)) {
        ui_info1("Chuyển game", "Chưa gắn thẻ ở khe 2 — cần cả hai thẻ để chuyển game.");
        return;
    }
    ui_push(ui_screen_new("Chuyển game", build_transfer));
}

static void build_bulk(ui_screen *s) {
    cJSON *plan = s->d, *mv = jarr(plan, "moves"), *m, *w;
    const char *S = sd_name(jstr(plan, "source_card")), *D = sd_name(jstr(plan, "dest_card"));
    int n = cJSON_GetArraySize(mv);
    int reached = jbool_d(plan, "reached", 0);
    char hdr[300];
    if (reached) snprintf(hdr, sizeof hdr, "%d game, %s từ %.0f lên %.0f MB trống.", n, S, jnumd(plan, "free_before_mb", 0), jnumd(plan, "free_after_mb", 0));
    else snprintf(hdr, sizeof hdr, "! CHƯA đạt mục tiêu %.0f MB: %d game, %s từ %.0f lên %.0f MB trống.", jnumd(plan, "target_free_mb", 0), n, S,
                  jnumd(plan, "free_before_mb", 0), jnumd(plan, "free_after_mb", 0));
    ui_hdr(s, "%s", hdr);
    ui_hdr(s, "Kế hoạch %.12s: danh sách dưới đây CHÍNH LÀ các game sẽ chuyển.", jstrd(plan, "plan_id", "?"));
    cJSON_ArrayForEach(w, jarr(plan, "warnings")) if (cJSON_IsString(w)) ui_hdr(s, "! %s", w->valuestring);
    char lab[96];
    snprintf(lab, sizeof lab, "Chuyển %d game sang %s", n, D);
    extern void lm_ask_moves(ui_screen *s, ui_item *it);
    ui_item *it = ui_add(s, lab, mbs(jnumd(plan, "bytes_freed", 0)), ST_YELLOW);
    ui_exp(it, "%d game: giải phóng %s ở %s, chép tổng %s sang %s.", n, mbs(jnumd(plan, "bytes_freed", 0)), S, mbs(jnumd(plan, "bytes_copy", 0)), D);
    ui_exp(it, "%s từ %.0f MB lên %.0f MB trống%s", S, jnumd(plan, "free_before_mb", 0), jnumd(plan, "free_after_mb", 0),
           reached ? "." : " — CHƯA đạt mục tiêu.");
    ui_exp(it, "Bước sau là hộp xác nhận (mặc định Huỷ).");
    ui_act(it, 0, "TIẾP: XÁC NHẬN", lm_ask_moves);
    ui_head(s, "Game sẽ chuyển (%d)", n);
    cJSON_ArrayForEach(m, mv) {
        int hs = jbool_d(m, "has_save", 0);
        char tag[64];
        snprintf(tag, sizeof tag, "%s%s", mbs(jnumd(m, "bytes_freed", 0)), hs ? " +save" : "");
        it = ui_add(s, jstrd(m, "name", "?"), tag, hs ? ST_YELLOW : ST_UNKNOWN);
        ui_exp(it, "Hệ: %s", jstrd(m, "system", "?"));
        ui_exp(it, "Giải phóng ở %s: %s", S, mbs(jnumd(m, "bytes_freed", 0)));
        ui_exp(it, "Chép sang %s: %s", D, mbs(jnumd(m, "bytes_copy", 0)));
        ui_exp(it, hs ? "! CÓ save/state: được CHÉP sang %s nhưng GIỮ LẠI ở %s." : "Không có save/state đi kèm.", D, S);
        ui_exp(it, "%s", "");
        ui_exp(it, "%s", jstrd(m, "rom_path", "?"));
        cJSON *f;
        cJSON_ArrayForEach(f, jarr(jget(m, "plan"), "files")) {
            const char *src = jstrd(f, "src", "?"), *base = strrchr(src, '/');
            ui_exp(it, "• %s: %s (%s)%s", jstrd(f, "kind", "?"), base ? base + 1 : src, mbs(jnumd(f, "size", 0)), jbool_d(f, "remove_src", 1) ? "" : " — chỉ chép");
        }
    }
    cJSON *sk = jarr(plan, "skipped");
    if (sk && cJSON_GetArraySize(sk)) {
        ui_head(s, "Bỏ qua (%d)", cJSON_GetArraySize(sk));
        cJSON_ArrayForEach(m, sk) {
            const char *rp = jstrd(m, "rom_path", "?"), *base = strrchr(rp, '/');
            ui_item *i2 = ui_add(s, base ? base + 1 : rp, "bỏ qua", ST_UNKNOWN);
            ui_exp(i2, "%s", jstrd(m, "reason", ""));
            ui_exp(i2, "%s", rp);
        }
    }
}

static void bulk_plan_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *plan = app_json_or_error(j, "Gợi ý chuyển hàng loạt");
    if (!plan) { plan_remove("move"); return; }
    lines_t L = {0};
    cJSON *w;
    int ok = jbool_d(plan, "ok", 0), nmv = jarr_n(plan, "moves");
    if (!ok || !nmv) {
        lines_add(&L, "%s%s", ok && jbool_d(plan, "reached", 0) ? "+ " : "!! ", jstrd(plan, "message", "engine không báo lý do"));
        cJSON_ArrayForEach(w, jarr(plan, "warnings")) if (cJSON_IsString(w)) lines_add(&L, "! %s", w->valuestring);
        cJSON *sk;
        cJSON_ArrayForEach(sk, jarr(plan, "skipped")) lines_add(&L, "Bỏ qua %s: %s", jstrd(sk, "rom_path", "?"), jstrd(sk, "reason", ""));
        ui_info(ok ? "Gợi ý chuyển hàng loạt" : "Không lập được gợi ý", &L);
        cJSON_Delete(plan);
        plan_remove("move");
        return;
    }
    if (!app_plan_written(plan, "move", "Gợi ý chuyển hàng loạt")) { cJSON_Delete(plan); plan_remove("move"); return; }
    ui_screen *s = ui_screen_new("Gợi ý chuyển hàng loạt", build_bulk);
    s->d = plan;
    s->on_free = move_free;
    snprintf(s->key, sizeof s->key, "%s", jstrd(plan, "dest_card", "secondary"));
    s->ia = (int)jnumd(plan, "target_free_mb", 0);
    ui_push(s);
}
static void do_bulk_plan(ui_screen *s, ui_item *it) {
    (void)s;
    char mbarg[24];
    snprintf(mbarg, sizeof mbarg, "%d", it->iarg);
    plan_remove("move");            /* không bao giờ dùng lại file của lần trước */
    cli_task("Đang quét & lập gợi ý", LONG_TIMEOUT_MS, bulk_plan_done, "", "move", "--bulk", "--to", it->arg, "--target-free-mb", mbarg,
             "--plan-out", plan_file("move"), NULL);
}

/* kết quả `move --apply --plan` (chung cho chuyển hàng loạt và chuyển một game) */
static void moves_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *r = app_json_or_error(j, "Kết quả chuyển game");
    if (!r) { plan_remove("move"); ui_pop_to(2); return; }
    int rejected = app_plan_rejected(j, r, "Chuyển game");
    lines_t L = {0};
    if (!rejected) {
        cJSON *applied = jarr(r, "applied"), *refused = jarr(r, "refused"), *res, *g;
        int ng = cJSON_GetArraySize(applied), nr = refused ? cJSON_GetArraySize(refused) : 0, n_mv = 0, n_cp = 0;
        cJSON_ArrayForEach(g, applied) { n_mv += jarr_n(g, "moved"); n_cp += jarr_n(g, "copied"); }
        int rb = jbool_d(r, "rolled_back", 0);
        lines_add(&L, "%sĐã chuyển %d/%d game trong kế hoạch đã xem; giải phóng %s ở %s.", ng && !nr && !rb ? "+ " : "", ng, g_plan_n,
                  mbs(jnumd(r, "bytes_moved", 0)), sd_name(g_plan_dest[0] ? (strcmp(g_plan_dest, "primary") ? "primary" : "secondary") : NULL));
        lines_add(&L, "Chuyển (moved): %d file.   Chỉ chép, nguồn giữ nguyên (copied): %d file (save/state, hoặc ảnh còn được game khác dùng).", n_mv, n_cp);
        lines_add(&L, "%sTừ chối (refused): %d", nr ? "!! " : "", nr);
        add_refused(&L, refused, "game");
        if (nr) lines_add(&L, "(Game bị từ chối: nó đã đổi từ lúc bạn xem hoặc không an toàn để chuyển — được để nguyên ở thẻ nguồn.)");
        if (rb) {
            lines_add(&L, "!! Có game bị lỗi nên đã xoá bản chép dở, nguồn còn nguyên:");
            cJSON_ArrayForEach(res, jarr(r, "results"))
                if (jbool_d(res, "rolled_back", 0)) lines_add(&L, "!! HOÀN TÁC cả game: %s", jstrd(res, "game", "?"));
        }
        lines_add(&L, "Sổ: %s/transfer-ledger.jsonl", P.userdata);
        app_add_rescan_lines(&L, jget(r, "rescan"));
    }
    cJSON_Delete(r);
    plan_remove("move");
    clear_dup();
    ui_pop_to(2);
    if (!rejected) ui_info("Kết quả chuyển game", &L);
    app_refresh();
}

static void do_moves(const char *dest) {
    /* --yes chỉ sau khi người dùng đã xác nhận. Không truyền --bulk/--target-free-mb/--system: đã nằm TRONG kế hoạch; --to phải khớp kế hoạch. */
    cli_task("Đang chuyển game", LONG_TIMEOUT_MS, moves_done, "", "move", "--to", dest, "--apply", "--plan", plan_file("move"), "--yes", NULL);
}

void lm_ask_moves(ui_screen *s, ui_item *it) {
    (void)it;
    cJSON *plan = s->d, *mv = jarr(plan, "moves"), *m;
    const char *S = sd_name(jstr(plan, "source_card")), *D = sd_name(jstr(plan, "dest_card"));
    int n = cJSON_GetArraySize(mv);
    g_plan_n = n;
    snprintf(g_plan_dest, sizeof g_plan_dest, "%s", jstrd(plan, "dest_card", "secondary"));
    lines_t L = {0};
    lines_add(&L, "Sẽ làm ĐÚNG %d mục đã xem: CHUYỂN %d game từ %s sang %s (kế hoạch %.12s).", n, n, S, D, jstrd(plan, "plan_id", "?"));
    lines_add(&L, "• Giải phóng khoảng %s ở %s; chép tổng %s sang %s.", mbs(jnumd(plan, "bytes_freed", 0)), S, mbs(jnumd(plan, "bytes_copy", 0)), D);
    lines_add(&L, "+ Chỉ các game trong danh sách bạn vừa xem. Engine không lập kế hoạch lại; game nào đã đổi (kích thước/giờ sửa file khác lúc xem) sẽ bị TỪ CHỐI riêng.");
    lines_add(&L, "• Mỗi game: chép sang thẻ đích, xác minh SHA-256 từng file, CHỈ KHI cả game đủ mới xoá bản ở thẻ nguồn.");
    lines_add(&L, "• File nào lỗi hoặc lệch hash: xoá bản chép dở, nguồn còn nguyên (hoàn tác cả game đó).");
    lines_add(&L, "• Đích đã có file cùng tên khác nội dung: từ chối cả game, không ghi đè.");
    lines_add(&L, "! Save/state ĐƯỢC CHÉP sang thẻ đích nhưng GIỮ LẠI ở thẻ cũ (CHƯA KIỂM CHỨNG: Leaf/RetroArch tìm save theo thẻ chứa ROM hay luôn ở Primary).");
    lines_add(&L, "• Sổ ghi: %s/transfer-ledger.jsonl", P.userdata);
    lines_add(&L, "%s", "");
    cJSON_ArrayForEach(m, mv) lines_add(&L, "• %s — giải phóng %s", jstrd(jget(m, "plan"), "game", jstrd(m, "name", "?")), mbs(jnumd(m, "bytes_freed", 0)));
    ui_confirm("Xác nhận chuyển game", &L, "CHUYỂN", do_moves, g_plan_dest);
}

/* ---- chuyển MỘT game (chọn từ danh sách ROM của lm_roms.c) ---- */
static void build_single(ui_screen *s) {
    cJSON *plan = s->d, *f, *w;
    const char *S = sd_name(jstr(plan, "source_card")), *D = sd_name(jstr(plan, "dest_card"));
    extern void lm_ask_move_one(ui_screen *s, ui_item *it);
    ui_hdr(s, "%s", jstrd(plan, "message", ""));
    ui_hdr(s, "Kế hoạch %.12s: các file dưới đây CHÍNH LÀ những gì sẽ chép/chuyển.", jstrd(plan, "plan_id", "?"));
    cJSON_ArrayForEach(w, jarr(plan, "warnings")) if (cJSON_IsString(w)) ui_hdr(s, "! %s", w->valuestring);
    char lab[160];
    snprintf(lab, sizeof lab, "Chuyển sang %s", D);
    ui_item *it = ui_add(s, lab, mbs(jnumd(plan, "bytes_freed", 0)), ST_YELLOW);
    ui_exp(it, "Giải phóng %s ở %s; chép tổng %s sang %s.", mbs(jnumd(plan, "bytes_freed", 0)), S, mbs(jnumd(plan, "bytes", 0)), D);
    ui_exp(it, "Bước sau là hộp xác nhận (mặc định Huỷ).");
    ui_act(it, 0, "TIẾP: XÁC NHẬN", lm_ask_move_one);
    ui_head(s, "File trong kế hoạch (%d)", jarr_n(plan, "files"));
    cJSON_ArrayForEach(f, jarr(plan, "files")) {
        const char *src = jstrd(f, "src", "?"), *base = strrchr(src, '/');
        int rm = jbool_d(f, "remove_src", 1);
        it = ui_add(s, base ? base + 1 : src, mbs(jnumd(f, "size", 0)), rm ? ST_UNKNOWN : ST_YELLOW);
        ui_exp(it, "Loại: %s", jstrd(f, "kind", "?"));
        ui_exp(it, "%s", rm ? "CHUYỂN: chép sang thẻ đích, xác minh, rồi xoá ở thẻ nguồn." : "CHỈ CHÉP: giữ nguyên ở thẻ nguồn.");
        ui_exp(it, "Từ: %s", src);
        ui_exp(it, "Sang: %s", jstrd(f, "dst", "?"));
    }
}

static void move_one_plan_done(lm_job *j, const char *arg) {
    (void)arg;
    cJSON *plan = app_json_or_error(j, "Chuyển game");
    if (!plan) { plan_remove("move"); return; }
    if (!jbool_d(plan, "ok", 0)) {
        lines_t L = {0};
        lines_add(&L, "!! Không chuyển được:");
        lines_add(&L, "%s", jstrd(plan, "message", "engine không báo lý do"));
        cJSON_Delete(plan);
        plan_remove("move");
        ui_info("Chuyển game", &L);
        return;
    }
    if (!app_plan_written(plan, "move", "Chuyển game")) { cJSON_Delete(plan); plan_remove("move"); return; }
    ui_screen *s = ui_screen_new("Chuyển một game", build_single);
    s->d = plan;
    s->on_free = move_free;
    ui_push(s);
}

void lm_move_one_preview(const char *rom, const char *dest) {
    plan_remove("move");
    cli_task("Đang lập kế hoạch chuyển game", LONG_TIMEOUT_MS, move_one_plan_done, "", "move", rom, "--to", dest, "--plan-out", plan_file("move"), NULL);
}

void lm_ask_move_one(ui_screen *s, ui_item *it) {
    (void)it;
    cJSON *plan = s->d, *f;
    const char *S = sd_name(jstr(plan, "source_card")), *D = sd_name(jstr(plan, "dest_card"));
    g_plan_n = 1;
    snprintf(g_plan_dest, sizeof g_plan_dest, "%s", jstrd(plan, "dest_card", "secondary"));
    lines_t L = {0};
    lines_add(&L, "Sẽ làm ĐÚNG kế hoạch đã xem: CHUYỂN '%s' từ %s sang %s (kế hoạch %.12s).", jstrd(plan, "game", "?"), S, D, jstrd(plan, "plan_id", "?"));
    lines_add(&L, "• Giải phóng khoảng %s ở %s; chép tổng %s sang %s.", mbs(jnumd(plan, "bytes_freed", 0)), S, mbs(jnumd(plan, "bytes", 0)), D);
    lines_add(&L, "+ Engine không lập kế hoạch lại; nếu file đã đổi (kích thước/giờ sửa khác lúc xem) game sẽ bị TỪ CHỐI.");
    lines_add(&L, "• Chép sang thẻ đích, xác minh SHA-256 từng file, CHỈ KHI cả game đủ mới xoá bản ở thẻ nguồn; lỗi thì xoá bản chép dở, nguồn còn nguyên.");
    lines_add(&L, "• Đích đã có file cùng tên khác nội dung: từ chối, không ghi đè.");
    lines_add(&L, "! Save/state (nếu có) được CHÉP sang nhưng GIỮ LẠI ở thẻ cũ (CHƯA KIỂM CHỨNG: Leaf/RetroArch tìm save theo thẻ chứa ROM hay luôn ở Primary).");
    lines_add(&L, "• Sổ ghi: %s/transfer-ledger.jsonl", P.userdata);
    lines_add(&L, "%s", "");
    cJSON_ArrayForEach(f, jarr(plan, "files")) {
        const char *src = jstrd(f, "src", "?"), *base = strrchr(src, '/');
        lines_add(&L, "• %s: %s (%s)%s", jstrd(f, "kind", "?"), base ? base + 1 : src, mbs(jnumd(f, "size", 0)), jbool_d(f, "remove_src", 1) ? "" : " — chỉ chép");
    }
    ui_confirm("Xác nhận chuyển game", &L, "CHUYỂN", do_moves, g_plan_dest);
}
