# LoongMate native (Catastrophe, ARM64)

Giao diện native cho Pocket 1 dựng bằng toolkit **Catastrophe** (UMRK, header-only C), biên dịch chéo ARM64.
Chỉ có MỘT app **LoongMate**: binary nằm ở `pak/Apps/mlp1/LoongMate.pak/bin/lm-home` (sau khi build chép `native/build/lm-home` vào đó). `LoongMate.pak/launch.sh` chạy nó; nếu binary thiếu / không chạy được / chết ngay lúc khởi động (mã 126, 127 hoặc mã ≠ 0 trong ~3 giây đầu, kể cả chết do signal) thì launcher tự chạy giao diện Python `engine/ui_sdl.py` và ghi lý do vào log. Native thoát mã 0 (người dùng bấm thoát) thì KHÔNG mở Python. `LOONGMATE_UI=python` ép dùng Python. Trong native, màn **Máy → Mở giao diện Python (dự phòng)** `execv` sang `ui_sdl.py` (cùng `--userdata/--primary/--secondary/--apps-dir`).
CI chỉ cho phép `LoongMate.pak/bin/` chứa đúng `lm-home`.

## Nguyên tắc: một nguồn logic

Native **không** tự đọc/ghi file người dùng, không tự băm, không tự xoá. Mọi dữ liệu và hành động đi qua
`engine/cli.py --json` (`fork`+`execv` mảng argv, không qua shell; đọc stdout không chặn bằng `poll`; hiện "Đang ... (Ns)").
Lệnh ghi chỉ nhận `--yes` **sau khi** người dùng bấm xác nhận trong hộp thoại native (mặc định Huỷ). Nhờ vậy mọi chốt an toàn
(SHA-256, giữ bản có save, hoàn tác, sổ ghi, rescan Leaf) nằm trong engine đã kiểm thử.
Màn nào CLI chưa có lệnh JSON tương ứng thì **không làm trong C** (xem "Thiếu CLI"). Từ đợt D native không còn `execv` sang Python UI.

### Kế hoạch đã duyệt (dọn ROM trùng, sửa hàng loạt, chuyển game)

Bước xem trước gọi CLI với `--plan-out <userdata>/jobs/native-<loại>.plan.json` (loại = `dedupe`, `batchfix`, `move`); màn hình hiện đúng
các mục của lần xem trước đó; hộp xác nhận nói "sẽ làm ĐÚNG N mục đã xem" và thực hiện bằng `--apply --plan <file> --yes`
(không truyền lại `--prefer-card/--copy-artwork/--bulk/...`: các tham số nằm TRONG kế hoạch). Engine chỉ làm các mục trong file nên bản trùng/vấn đề mới
xuất hiện sau lúc xem không bị đụng tới; mục nào đã đổi thì bị `refused` kèm `reason` (hiện nguyên văn). Mã thoát 3 (kế hoạch cũ > 24 giờ, bị sửa, sai loại) →
hộp "Kế hoạch đã cũ hoặc không hợp lệ — xem trước lại" và không làm gì. Native xoá file kế hoạch sau khi thực hiện, khi đóng màn kế hoạch (huỷ) và trước mỗi lần xem trước mới.
Nếu JSON xem trước không có `plan_id`/`plan_path` đúng file mong đợi (engine cũ), native từ chối thực hiện.

## Trạng thái các màn (xem `shots/c4_*.png` = ảnh chụp trên máy thật, offscreen, dữ liệu sandbox; `shots/c4emu_*.png` = giả lập)

| Màn | Trạng thái |
|---|---|
| Khung chung (danh sách cuộn, khung giải thích có cuộn L1/R1, chân màn hình theo mục, hộp xác nhận mặc định Huỷ, hộp thông tin cuộn được, tác vụ nền có đếm giây, chống bấm đúp) | Xong, đã chụp |
| Home 4 thẻ (+ dòng bản sao ngoài máy trên thẻ SAVE), X sao lưu, Y gói hỗ trợ | Xong |
| Máy: pin, nhiệt, `health` (6 kiểm tra), xuất gói hỗ trợ | Xong |
| Thẻ nhớ | Xong |
| SaveVault: sao lưu, danh sách MỌI snapshot (`snapshots --json`), xem trước / xác minh / khôi phục theo id (xác nhận), rollback sau mất điện, bản sao ngoài máy, toàn vẹn kho | Xong (đợt D) |
| SaveVault → **Khôi phục save một game** (`saves list --json` → chọn game, tag `N bản · thẻ · core` → `saves history KEY --json` → chọn phiên bản, tag "giống hiện tại" khi mọi file `same_as_current` → xem trước `saves restore KEY --snapshot=ID` KHÔNG `--yes` (`files[].action` create/overwrite/unchanged, cảnh báo bản đang chơi mới hơn) → hộp xác nhận mặc định Huỷ → `--yes` → `restored`/`failed`/`verified`/`pre_restore_id` + hướng dẫn quay lại; `nothing_to_do` → "save hiện tại đã giống bản này"; `{error,message}` hiện nguyên message; xong thì về màn SaveVault vì danh sách đã cũ) | Xong (đợt H); thử trong container ARM64 + sandbox, xem "Đợt H" |
| Library: quét, danh sách vấn đề, sửa an toàn, sửa hàng loạt theo kế hoạch đã duyệt, hoàn tác (bật/tắt theo `fix undo --check`), BIOS (hiện `expected_md5`) | Xong (đợt D) |
| Dọn ROM trùng 3 bước (kế hoạch đã duyệt; bật/tắt chép ảnh bìa nằm trong kế hoạch) | Xong (đợt D) |
| Chuyển game giữa thẻ — gợi ý hàng loạt 1/2/3/5 GB và chọn từng game, đều theo kế hoạch đã duyệt | Xong (đợt D) |
| Xoá ROM (`roms --json` → lọc thẻ → hệ máy → game; X đánh dấu nhiều game trong một hệ → MỘT lần `rom delete P1 P2 ...` xem trước (không `--yes`) rồi `--yes`; xoá xong đứng yên tại màn game, bỏ dòng đã xoá, con trỏ sang game kế, không quét lại). Đợt H: **Y** đổi sắp xếp Tên ↔ Dung lượng (lớn trước); mục **Lọc** (A đổi vòng tất cả / có save / không save) ngay dưới mục "Xoá N game đã chọn"; dấu X giữ khi đổi sắp xếp/lọc, game bị lọc ẩn vẫn nằm trong lô xoá (header ghi "N đang bị bộ lọc ẩn"); mở màn con trỏ vào game đầu tiên; mục **Game lớn nhất (mọi hệ)** = 50 game lớn nhất (lọc save TRƯỚC rồi mới lấy top, theo bộ lọc thẻ của màn hệ máy), tag có tên hệ | Xong (đợt E2 + H) |
| Library → **Nhập game** (Local Importer, `lm_import.c`; chi tiết `docs/NHAP-GAME.md`): `import scan --json` → danh sách mục trong `Import/` của cả hai thẻ (tag hệ máy / "chọn hệ" / bị chặn / chưa hỗ trợ; X chọn nhiều, A trên mục mơ hồ = chọn hệ) → `import plan --select ID... --choice ID=HỆ --plan-out <userdata>/jobs/native-import.plan.json` (thẻ phụ không dùng được = HỎI "Dùng thẻ Leaf", không tự fallback) → màn kế hoạch (đổi hành động khi trùng tên giữ/thay/bản sao/huỷ, xác nhận BIOS riêng) → hộp xác nhận mặc định Huỷ → `import apply --plan FILE --yes [--conflict ID=ACT] [--bios-ok ID] [--delete-source]` → kết quả từng mục, đứng yên tại danh sách đã quét lại. Thử: `native/tools/imp-run.sh` (máy thật, offscreen, sandbox `/userdata/lm-sandbox-imp`), ảnh `shots/c4_imp_*.png` | Xong; chạy trên máy thật (sandbox) |
| Library → **Thư viện riêng** (`lm_lib.c`; chi tiết `docs/THU-VIEN-RIENG.md`): `library overview` → node (+ job tải dở, ghép đôi bundle) → `library roots` → `library list` (hệ máy) → `library list --system S --offline` (game; X chọn nhiều, Y sắp xếp, Lọc đã có/chưa có) → `library fetch --dry-run` (hỏi server size+SHA-256, chọn thẻ như importer, HỎI khi phải dùng thẻ Leaf) → hộp xác nhận mặc định Huỷ → `library fetch --yes --progress` (**thanh % theo byte** từ dòng `LMPROGRESS` trên stderr; **B = tạm dừng**, tải tiếp ở "Tải dở") → kết quả của importer, đứng yên tại danh sách. Native không nói chuyện mạng/không đọc token: chỉ gọi CLI. Thử: `native/tools/lib-emu.sh` (container ARM64 + agent PocketBridge thật), `lib-run.sh` (máy thật, offscreen), `lib_e2e.py`, ảnh `shots/lib_*.png` | Xong; chạy trong container ARM64 (qemu); **chưa chạy trên máy thật** (máy offline lúc làm) |
| Library → **Homebrew** (`lm_lib.c`, cuối file; chi tiết `docs/HOMEBREW.md`): `homebrew list` (hệ máy + job dở) → **Cập nhật danh mục** (`homebrew sync --progress`, thanh trang X/N, B = dừng) → `homebrew list --system S` (game có GIẤY PHÉP RÕ RÀNG; X chọn nhiều, Y Tên/Mới thêm, Lọc đã có/chưa có; khung giải thích = chi tiết: mô tả, tác giả, giấy phép, nguồn) → `homebrew fetch --dry-run` (hỏi máy chủ kích thước, chọn thẻ, HỎI khi phải dùng thẻ Leaf) → hộp xác nhận mặc định Huỷ → `homebrew fetch --yes --progress` (B = tạm dừng) → kết quả của importer, đứng yên tại danh sách. Tải thẳng Internet (HTTPS kiểm chứng thư), không qua PC/Tailscale. Chưa làm: tìm theo chữ cái. Thử: `native/tools/hb-run.sh` (máy thật, offscreen, thẻ giả `/userdata/lm-hb-sandbox`, Internet thật), ảnh `shots/hb_*.png` | Xong; chạy trên máy thật (sandbox) |
| Library → **Tải từ web** (`lm_web.c` + bàn phím `lm_osk.c`; engine `web ... --json`): `web home` (lịch sử, dấu trang, link trong `<thẻ>/Import/links.txt`, job dở) → **Nhập địa chỉ…** (bàn phím trên màn hình, ASCII) → `web open URL` → màn trang (một màn mỗi trang, B quay lại trang trước; ô tìm kiếm thay `{q}` kiểu `quote_plus`; Lưu dấu trang; file tải được; liên kết trong trang (nhãn "tải?"); màn con liên kết ra trang khác và Menu & điều hướng; Lọc liên kết… theo chữ; cảnh báo trang tải bằng JavaScript) → file: `web fetch --dry-run` (hiện tên file, dung lượng, hệ máy, **máy chủ thật sau chuyển hướng**, thẻ đích; HỎI khi phải dùng thẻ Leaf) → hộp xác nhận mặc định Huỷ → `web fetch --yes --progress` (B = tạm dừng; tiếp ở **Tải dở**) → kết quả của importer, đứng yên tại màn trang. Trang lồng nhau tối đa ~8 (ngăn xếp 14). Thử trong container ARM64 với CLI giả (`shots/web_*.png`, font DejaVu) | Xong; **chưa chạy trên máy thật**, chưa thử với engine thật |
| Library → **Patch ROM** (`lm_patch.c`; engine `patch list|candidates|apply|delete --json`): danh sách patch .ips/.bps/.ups (kho của LoongMate + `Import/`; tag định dạng + "CRC", "hỏng" khi lỗi) → A ÁP DỤNG = `patch candidates ID` (BPS/UPS: ROM gốc khớp CRC32, 0 khớp = giải thích; IPS: chọn hệ máy → `--system S` → chọn ROM tay) → hộp xác nhận mặc định Huỷ (IPS có cảnh báo không có mã kiểm tra) → `patch apply ID --rom PATH [--header-skipped] --yes` → ROM MỚI cạnh bản gốc (bản gốc không bị sửa), kết quả hiện tên/dung lượng/"đã kiểm CRC kết quả"; X XOÁ (chỉ patch trong kho, xác nhận). Đường dẫn ROM giữ trong biến toàn cục (có thể > UI_ARGLEN). Tải từ web báo "Đã lưu N patch" khi tải trúng patch. Thử trong container ARM64 với CLI giả (`shots/patch_*.png`, font DejaVu) | Xong; **chưa chạy trên máy thật**, chưa thử với engine thật |
| Lịch sử pin/nhiệt (biểu đồ cột `cat_draw_rounded_rect`, 24 giờ / 7 ngày) | Xong |
| Trạng thái agent (chạy / dừng / chưa từng chạy, toàn vẹn kho, băm trước, tự nạp lại), thiết bị trong mạng (ping, kiểm cổng, yêu thích, Wake-on-LAN) | Xong; "tự nạp lại" chưa hiện được (thiếu CLI) |
| Kiểm tra nút | Xong nhưng hiện **tên nút Catastrophe**, không phải số joystick thô |
| Máy → **Leaf Hot Tabs** (`hottabs status --json` → trạng thái patched/stock/không khớp, A bật/tắt "tự tiếp tục từ Games/Favorites" = `hottabs on|off` không cần xác nhận; Gỡ/Cài lại = xem trước `hottabs install|uninstall` KHÔNG `--yes` (chạy `hottabs.sh --dry-run`) → hộp xác nhận mặc định Huỷ → `--yes` → nhắc khởi động lại máy; `lm_hottabs.c`) | Xong; chụp trên máy thật (chỉ đọc + xem trước), ảnh `shots/h_hottabs_*.png` |
| Sức khoẻ pin (`battery`), Cập nhật LoongMate (`update list` → chọn gói, hiện bản đang cài; `verify`/`apply`/`rollback`) | Xong |
| Chạy trên màn hình thật (driver wayland) và bấm nút thật | **Chưa thử** — cố ý không chạy màn thật qua SSH |

Nguồn kiểm chứng: Home, Máy, Thẻ nhớ, SaveVault (sao lưu/xem trước/xác minh/khôi phục), Library (quét, vấn đề, sửa an toàn, sửa hàng loạt, hoàn tác, BIOS),
dọn ROM trùng và chuyển game chạy thật trên máy (offscreen, sandbox) — ảnh `c4_*.png` (bản dựng trước các chỉnh nhỏ cuối: ưu tiên nhãn/tag, bố cục biểu đồ).
Các phần thêm/chỉnh SAU khi máy mất kết nối (dòng bản sao ngoài máy, Sức khoẻ pin, Cập nhật, bố cục lịch sử, kiểm tra nút, Rollback, lỗi engine, chống bấm đúp)
chỉ được kiểm trong container ARM64 giả lập (`tools/emu-shot.sh`, font DejaVu thay font Leaf, python3 Ubuntu): đủ để kiểm logic, KHÔNG thay ảnh máy thật.

## Thiếu CLI (native tạm bỏ qua hoặc hiện nguyên trạng, không làm trong C)

- `agent status --json` **không trả** `reload` (`agent_state.json` có `reload {from,to,ts}`): mục "Tự nạp lại gần nhất" luôn hiện "chưa có" kèm ghi chú. Khi CLI thêm trường này (đúng dạng `{from,to,ts}`) native sẽ hiện ngay.
- `rom delete` không có kế hoạch lưu file / `--plan-out`: native xem trước bằng cách gọi **không `--yes`** (stdin của engine là /dev/null), rồi xoá bằng `rom delete <các đường dẫn đã xem là ok> --yes` (chỉ gửi game `plan.ok` và không `merged_into`); engine lập lại danh sách file lúc xoá. `rom delete` **không từ chối** game có save/state, nó **giữ lại** save/state (thành mồ côi). Đánh dấu chỉ trong phạm vi một hệ (rời hệ là bỏ đánh dấu).
- Chọn nhiều game để **chuyển** thẻ: thiếu CLI (`move` chỉ nhận MỘT game mỗi kế hoạch), nên màn Chuyển game vẫn chọn từng game.
  `rom delete <đường dẫn> --yes`; engine lập lại danh sách file lúc xoá (với MỘT game thường giống hệt). Khác với yêu cầu ban đầu: `rom delete` **không từ chối** game có save/state, nó **giữ lại** save/state (thành mồ côi); chỉ `dedupe` mới từ chối xoá bản có save.
- `roms --json` trả cả track `.bin` của game `.cue`; native ẩn `.bin` khi cùng thư mục + hệ có `.cue` mà tên `.cue` (bỏ đuôi) là tiền tố tên `.bin`. Đây là suy đoán từ tên, không phải dữ liệu từ engine; `.bin` lẻ không khớp vẫn hiện.
- `dedupe --plan-out` không đi với `--compare`, nên bước 1 không còn bảng "thu hồi trên SD1/SD2 cho cả 3 lựa chọn" từ một lần băm: số liệu chỉ có cho lựa chọn đã chọn (các lựa chọn khác ghi "chưa tính"). `--copy-artwork` phải đặt lúc xem trước (nằm trong kế hoạch) nên bật/tắt nó bỏ kế hoạch đã lập.
- `roms --json` liệt kê MỌI file trong `Roms/` kể cả ảnh minh hoạ (`Roms/<hệ>/Imgs/*.png` hiện ra như một "game"): native chỉ thêm ghi chú "đây là file ảnh" ở khung giải thích, không ẩn (CLI nên lọc hoặc gắn loại).
- Dọn ROM trùng: CLI không báo số nhóm cần chép ảnh bìa → native mặc định BẬT và nói rõ.
- `update verify/apply` nhận đường dẫn gói; `update list` không có `--apps-dir` (chỉ liệt kê gói trên thẻ chính).
- `snapshots --json`: `verified` chỉ `true` hoặc `null` (không bao giờ `false`); native hiện "chưa có bản ghi kiểm" chứ không báo hỏng.
- `readiness` không chứa `vault_verify`: Home gọi thêm `snapshots --json` sau mỗi lần đọc (hai lần khởi chạy Python mỗi lần làm mới).
- Kiểm tra nút: số joystick thô không có ở Catastrophe.
- `saves restore --json` không chuyển `missing_files` của `restore_snapshot` (chỉ còn trong `message`); native vẫn hiện `missing_files` nếu engine sau này trả.

## Đợt H (sắp xếp/lọc ROM, Game lớn nhất, khôi phục save một game)

Sắp xếp và lọc làm TRONG C trên mảng `roms --json` đã nạp (có sẵn `has_save`, `size`, `name`, `card`): không gọi lại CLI mỗi lần bấm Y/A (mỗi lần gọi ~0,4 s + hộp "Đang ..."), và dấu chọn nằm theo chỉ số dòng
nên không thể mất khi đổi. Hệ quả: thứ tự theo Tên là `strcasecmp` (không phân biệt hoa/thường ASCII), KHÔNG bỏ dấu tiếng Việt như `roms --sort name` của CLI (`fold_text`); bản Python UI cũng chỉ `name.lower()`.
`saves list|history|restore` truyền khoá game ở dạng một phần tử argv sau `--` và id snapshot dạng `--snapshot=ID` (khoá/ id bắt đầu bằng `-` không bị hiểu là cờ).
Thử: `native/tools/h-run.sh` (máy thật, offscreen, `/userdata/lm-native-h/`, `/userdata/lm-sandbox-h/`) và `native/tools/emu-shot.sh` với `EMU_PREP='python3 /native/tools/h_prep.py /sb/lm-sandbox-emu /pak world'`
(`h_prep.py`: ~80 ROM kích thước khác nhau, save/state, 4 snapshot với Zelda đổi dần, Kirby chỉ còn trong snapshot; bước `md5` in md5 mọi Saves/States để chứng minh game khác không bị đụng).
`tools/h_fakepy.sh` (chỉ để thử đường lỗi trong container) thay `python3` và trả JSON lỗi giả cho `saves restore --yes`.
Ảnh: `shots/c4emu_h_*.png` (container ARM64 giả lập, font DejaVu — KHÔNG thay ảnh máy thật). Đợt H CHƯA chạy trên máy thật (máy offline lúc làm); `h-run.sh` đã viết sẵn nhưng chưa dùng.

## Build

```sh
native/fetch-sysroot.sh   # một lần: kéo lib từ máy (SSH, chỉ đọc) -> native/sysroot/device-libs.tar
native/build.sh           # docker build + make -> native/build/lm-home, native/build/lm-hello
```

`build.sh` in ra `NEEDED` và phiên bản glibc cao nhất của từng binary (phải <= 2.38).
Kết quả: NEEDED = `libSDL2_ttf-2.0.so.0 libSDL2_image-2.0.so.0 libSDL2-2.0.so.0 libm.so.6 libc.so.6 ld-linux-aarch64.so.1`, glibc cao nhất `GLIBC_2.34`.

## Thử trên máy (không chiếm màn hình thật)

```sh
native/tools/sandbox.sh                      # dựng /userdata/lm-sandbox-c4 (2 thẻ giả có ROM trùng, lỗi thư viện, telemetry, engine hiện tại)
native/tools/c4-shot.sh sandbox <tên> <phím>  # chạy offscreen trên dữ liệu sandbox, kéo ảnh về native/shots/c4_<tên>[.n].png
native/tools/c4-shot.sh real <tên> <phím>     # dữ liệu THẬT: chỉ dùng phím chỉ đọc
```

`--keys` (chỉ để kiểm thử): `UP DOWN LEFT RIGHT A B X Y L1 R1`; `SHOT` chụp giữa chừng (`<tên>.<n>.ppm`);
`SHOTB` chụp khi tác vụ nền đang chạy >= 1,5 giây (thấy "Đang ... (Ns)"; `LM_SHOTB_MS` đổi mốc, vd tải file lớn qua qemu); `BUSYB` bấm B khi tác vụ nền đã chạy tới mốc đó (thử B = tạm dừng); `AA`/`XX`... = bấm hai lần NGAY LẬP TỨC (thử chống bấm đúp). `GATE` = dừng cho tới khi file `<ảnh>.ppm.gate` xuất hiện (`d4_watch.py` tạo sau khi đổi sandbox → thứ tự "xem trước → đổi sandbox → xác nhận" tất định).
Mọi lệnh ghi trong thử nghiệm chỉ chạy trên sandbox, với `LOONGMATE_NO_RESCAN=1` (native truyền `--no-rescan` xuống CLI).
`native/tools/sandbox_crash.py` giả lập phiên khôi phục dở dang để thử màn Rollback.
Đợt D: `native/tools/d4-run.sh setup|sandbox|real|clean` đẩy binary mới vào `/userdata/lm-native-d4/` và engine của working tree vào `/tmp/lm-d4/`, chạy offscreen trên sandbox
`/userdata/lm-sandbox-d4/` (biến `PREP=` gọi `d4_prep.py`: nhiều snapshot, `agent_state.json`, gói cập nhật; `WATCH=` gọi `d4_watch.py`: đổi sandbox NGAY SAU khi kế hoạch được ghi để chứng minh "làm đúng danh sách đã xem") hoặc ở chế độ
`real` chỉ đọc (đặt mốc thời gian rồi liệt kê file trên thẻ thật mới hơn mốc: chỉ được thấy log của chính lm-home, log ssh và file hệ thống; agent của người dùng nếu đang chạy có thể ghi telemetry). `d4-run.sh push` chỉ đẩy lại binary sau khi build.
`native/tools/emu-shot.sh` dùng được với `EMU_PREP='python3 /native/tools/d4_prep.py /sb/lm-sandbox-emu /pak snaps,agent_bad'` (hoặc chạy `d4_watch.py` nền) để thử trong container ARM64.
`native/tools/emu-shot.sh` chạy cùng binary trong container ARM64 (qemu) khi không có máy; `native/tools/deploy-pak.sh` đẩy `bin/lm-home` + `launch.sh` vào `LoongMate.pak` trên máy và so md5 (cài đầy đủ có sao lưu/rollback: `pc/deploy.py`).

Tham số `lm-home`: `--pak DIR` (mặc định `<exe>/../`), `--python PATH` (hoặc `$LOONGMATE_PYTHON`, `<userdata>/runtime/python`,
`/userdata/loongmate-runtime/python`), `--userdata DIR`, `--primary DIR`, `--secondary DIR`, `--apps-dir DIR` (thử cập nhật trên sandbox).

## Phím

D-pad chọn (Lên/Xuống), Trái/Phải lật trang, L1/R1 cuộn khung giải thích, A/X/Y theo nhãn ở chân màn hình, B quay lại.
Start = A, Menu = B (như bản Python). Trong hộp thoại: Lên/Xuống cuộn, Trái/Phải chọn nút, A bấm nút đang chọn (mặc định **Huỷ**), B huỷ.

**Bàn phím trên màn hình** (`ui_osk`): D-pad di chuyển (vòng quanh), A nhấn phím, B xoá lùi (giữ để lặp; text rỗng thì không làm gì, KHÔNG thoát), X dấu cách, Y đổi bàn phím (a-z / A-Z / ký hiệu URL), L1/R1 dời con trỏ, SELECT nhảy tới XONG; XONG áp dụng, HUỶ bỏ. Start đã thành A nên không dùng.
Phím bấm dồn trong 0,4 giây đầu sau khi hộp thoại hiện bị bỏ qua; khi tác vụ nền đang chạy mọi phím bị nuốt (không chạy chồng).

## Mã

- `src/lm_home.c` — điểm vào, Home, Máy, Thẻ nhớ, SaveVault, gói hỗ trợ. `src/lm_library.c` — Library, sửa, BIOS, dọn ROM trùng, chuyển game.
  `src/lm_misc.c` — lịch sử, agent, mạng, kiểm tra nút. `src/lm_update.c` — sức khoẻ pin, cập nhật.
- `src/lm_web.c` — Library → Tải từ web. `src/lm_patch.c` — Library → Patch ROM. `src/lm_osk.c` — bàn phím trên màn hình dùng chung (`ui_osk`).
- `src/lm_saves.c` — SaveVault → Khôi phục save một game (`saves list|history|restore`).
- `src/lm_ui.c` — khung giao diện chung. `src/lm_cli.c` — đường dẫn + gọi CLI. `src/lm_run.c` — `fork`+`execv`, đọc không chặn, timeout.
- `src/lm_common.c` — font tiếng Việt (nạp Nunito-Bold của launcher nếu font theme thiếu glyph U+1EC5), ghi PPM, harness thử nghiệm.
- `src/lm_hello.c` / `src/cat_impl.c` — Hello; nơi duy nhất định nghĩa `CAT_IMPLEMENTATION`. `third_party/` — Catastrophe (MIT), cJSON (MIT), header SDL (zlib).

## Vì sao toolchain như vậy

- Image `ghcr.io/utility-muffin-research-kitchen/mlp1-toolchain` tồn tại nhưng `docker pull` thất bại trên mạng này. Thay vào đó:
  `ubuntu:22.04` + `gcc-aarch64-linux-gnu` (header glibc 2.35), link với **libc/libm/ld.so/SDL2/SDL2_ttf/SDL2_image/freetype/harfbuzz copy từ chính máy**
  (libSDL2 của máy dùng symbol `GLIBC_2.38`). `--allow-shlib-undefined`: libharfbuzz cần libglib... không kéo về sysroot; máy giải lúc chạy.
- Header SDL 2.28.5 / SDL_ttf 2.22.0 / SDL_image 2.8.2 từ source chính thức (`third_party/SOURCES.txt`), khớp phiên bản thư viện trên máy.

## Lệch giữa tài liệu Catastrophe và mã thật

- `cat_draw_screen_title(title, &status_bar)` chỉ chừa chỗ, không vẽ status bar: phải gọi thêm `cat_draw_status_bar`.
- `cat_present()` ngủ chờ input khi không có `cat_request_frame()`; vòng lặp phải xin khung hình khi đang chờ job/chụp ảnh.
- Bảng nút joystick mlp1 trong `catastrophe.h`: A=1, B=0, X=2, Y=3, L1=4, R1=5, Start=9, Menu=10, D-pad là hat.

## Hạn chế đã biết

- Offscreen mặc định tạo cửa sổ 1024x768 trong khi UI vẽ ở 960x720 → SDL co giãn 1,067 lần và làm rơi các hàng 1px (vạch ngang giả trên highlight, pill, khung). Harness ép cửa sổ về 960x720 (máy thật chạy 960x720 nên không bị). Ngoài ra `catastrophe.h` được vá nhẹ (chồng các thanh của rounded rect/pill 1px; ghi trong `third_party/SOURCES.txt`).

- Màn hình thật dùng driver wayland; HW renderer không thử được qua offscreen (Catastrophe tự rơi về software renderer ở đó).
- Đường vẽ tại các góc bo tròn có vài vạch mảnh (đường nối của `cat_draw_rounded_rect`), không ảnh hưởng nội dung.
