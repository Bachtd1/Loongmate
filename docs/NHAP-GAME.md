# Nhập game (Local Importer)

Library → **Nhập game**: đưa game BẠN TỰ CÓ (bản dump của chính bạn, homebrew) vào thư viện Leaf ngay trên máy, có kiểm an toàn,
tự nhận diện hệ máy, cài chống mất điện, rồi tự Rescan Library. Đây là giai đoạn A của `leaf-sources.md`.

## Pháp lý (đọc trước)

- Chỉ nhập **nội dung bạn có quyền sử dụng**. LoongMate **không tải ROM từ internet**, không cào website, không có danh mục ROM nào.
- Không dùng ROM thương mại để thử: test và sandbox dùng byte header tự dựng theo tài liệu kỹ thuật + dữ liệu ngẫu nhiên.
- BIOS: chỉ nhận file có md5 khớp `bios_hashes.json` (hash công khai của libretro); LoongMate không tải BIOS.

## Cách dùng

1. **Đưa file vào máy.** Thư mục `Import/` ở gốc **mỗi** thẻ (Library → Nhập game → *Mở thư mục nhập* sẽ tạo nếu thiếu). Chép vào đó
   file ROM, `.zip`, hoặc thư mục game (qua USB/máy tính, hoặc `python pc/import_push.py FILE...` — xem dưới).
2. **Library → Nhập game**: danh sách mọi mục trong `Import/` của cả hai thẻ, mỗi mục có hệ máy nhận diện được (tag xanh), hoặc lý do
   bị chặn / chưa hỗ trợ.
   - **X** chọn/bỏ chọn nhiều mục (như màn Xoá ROM); **A** trên mục "chọn hệ" mở danh sách hệ ứng viên; **A** trên mục thường = nhập riêng mục đó.
   - Mục đầu danh sách "Nhập N mục đã chọn" → **kế hoạch**.
3. **Kế hoạch** (chưa ghi gì): thẻ đích + lý do chọn, từng mục là *cài* / *đã có* / *trùng tên* / *BIOS*.
   - Trùng tên khác nội dung: **A** đổi hành động *giữ (mặc định) / thay (bản cũ vào `.loongmate-import/trash/`) / bản sao / huỷ*.
   - BIOS khớp md5: xác nhận **riêng** từng file (A). Không bao giờ ghi đè BIOS đã có.
4. **THỰC HIỆN** → hộp xác nhận (mặc định **Huỷ**) → "Đang nhập game… (Ns)" → kết quả từng mục (đã cài / đã có / bị chặn + lý do).
   Màn đứng yên tại danh sách, đã quét lại; mục đã cài biến mất khỏi danh sách.
5. File gốc trong `Import/` chỉ rời chỗ **sau khi cài thành công**: mặc định chuyển vào `Import/.done/`; bật "Dọn file đã nhập" để xoá.
   Mục chưa cài (bị chặn/huỷ/lỗi) luôn ở nguyên chỗ. Nguồn là đường dẫn tuỳ ý (CLI) không bao giờ bị chuyển/xoá.

Cả hai giao diện (native `lm-home`, Python `ui_sdl.py`) cùng logic; native chỉ gọi `cli.py import ... --json`.

### Chọn thẻ đích

Mặc định thẻ **phụ** (`UMRK_SECONDARY_SDCARD_PATH`, kiểm là điểm mount thật, ghi được, đủ chỗ cho dung lượng giải nén + staging + dự phòng
128 MiB). Nếu thẻ phụ không dùng được (không gắn / chỉ đọc / thiếu chỗ) thì **hỏi**: kế hoạch trả `needs_card_choice` và không cài gì
cho tới khi bạn chọn thẻ Leaf. Không có đường lùi âm thầm. Hai thẻ có thể đổi chỗ sau mỗi lần khởi động: engine luôn dùng biến môi trường
của Leaf (`SDCARD_PATH`, `UMRK_SECONDARY_SDCARD_PATH`), không ghi cứng `/mnt/sdcard`.

## An toàn

| Mối nguy | Chặn thế nào |
|---|---|
| Path traversal / đường dẫn tuyệt đối / `C:` | từ chối cả archive; kiểm lại đường dẫn canonical nằm trong staging khi giải nén |
| symlink, hardlink, device trong zip (external_attr) hoặc trong thư mục | từ chối cả nguồn |
| File thực thi/script | đuôi `.sh .exe .bat .cmd .com .msi .apk .elf .so .dll .dylib .py .pyc .js .jar .ps1 .vbs .scr .pl .rb .php`, magic ELF, PE (`MZ`→`PE\0\0`), shebang `#!/` (chỉ khi không phải ROM nhận diện được) |
| Archive lồng nhau | `.zip .7z .rar .gz .tar .bz2 .xz .zst .cab...` bên trong archive/thư mục: chặn |
| Zip bomb | giới hạn (bảng dưới) + tỉ lệ nén theo entry **và** cả archive; đếm byte THẬT khi giải nén, không tin kích thước khai báo |
| Zip hỏng / có mật khẩu / nén lạ | CRC kiểm từng entry khi giải nén; entry mật khẩu và phương pháp nén lạ bị chặn |
| Tên trùng không phân biệt hoa/thường, ký tự FAT32 cấm | trùng: chặn; ký tự cấm: đổi `_` với file lẻ, **chặn** với game nhiều file (đổi tên sẽ làm hỏng tham chiếu `.cue`) |
| Ghi đè save/state/BIOS | đích luôn là `Roms/<hệ>/…` (kiểm canonical); save/state trong gói bị bỏ qua; BIOS đã có không bao giờ bị ghi đè |
| Mất điện / reboot giữa chừng | staging cùng thẻ + nhật ký `job.json` ghi TRƯỚC khi đụng `Roms/`; xem "Phục hồi" |
| Kế hoạch bị sửa | `plan_id` + engine **quét lại nguồn thật** khi thực hiện, không tin đường dẫn đích trong file kế hoạch |

### Giới hạn mặc định (`importer.Limits`)

| Giới hạn | Giá trị | Ghi chú |
|---|---|---|
| Số entry / archive | 1000 | |
| Độ sâu thư mục | 8 | |
| Mỗi file | 2 GiB | FAT32 trần 4 GiB − 1 |
| Tổng giải nén | 4 GiB | |
| Tỉ lệ nén | ≤ 150:1 | áp cho entry ≥ 4 MiB và cho cả archive (chặn zip chồng lấn) |
| Dự phòng trống thẻ đích | 128 MiB | |
| BIOS tối đa đem băm md5 | 16 MiB | |

Đây là giá trị "hợp lý cho máy cầm tay", không phải chuẩn nào; đổi bằng tham số `limits` của hàm engine.

## Định dạng hỗ trợ và phân loại

- File ROM đơn, `.zip` (thư viện chuẩn `zipfile`), thư mục. **`.7z`/`.rar` (và `.gz .tar ...`) chưa hỗ trợ trên máy**: báo "giải nén trên PC rồi
  đưa file ROM (hoặc .zip) vào Import/". Không gọi binary ngoài (máy không có `7z`/`unrar`).
- Phân loại theo thứ tự: **header** (GB/GBC, GBA, NDS, NES/FDS, SNES, N64, Genesis/32X, SMS/GG, đĩa PSX/Saturn/Mega-CD/Dreamcast/PSP) →
  **phần mở rộng duy nhất** trong `systems.json` → **mơ hồ = hỏi** (`needs_choice`, liệt kê hệ ứng viên). Nguồn từng header ghi trong đầu `engine/importer.py`.
  Header thắng phần mở rộng (kèm cảnh báo nếu lệch).
- Mơ hồ điển hình: `.bin` (32X/A7800/ATARI2600/GENESIS/MD32X), `.cue/.m3u` không đọc được header, `.chd`, `.iso`, header "SEGA 32X" (folder 32X hay MD32X).
- Game nhiều file (`.cue`+`.bin`, `.m3u`+đĩa, `.gdi`): cài cả bộ, **giữ layout tương đối** so với gốc bộ; thiếu file tham chiếu = chặn.
  Trùng tên: "bản sao" của game nhiều file nằm trong thư mục con `<tên> (N)/` (tham chiếu tương đối vẫn đúng).
- Zip gồm nhiều ROM: mỗi ROM là một mục riêng. Zip nhiều **hệ** (chưa có manifest) vẫn nhập từng mục, kèm cảnh báo.
- Zip toàn file không nhận diện (≥ 2 file, kiểu bộ ROM arcade): cài **nguyên file zip**, hỏi hệ trong danh sách hệ chỉ nhận `.zip`.
- Thư mục đích lấy từ `systems.json` (`folder` chuẩn của Leaf), tự tạo nếu thiếu.
- BIOS: md5 khớp `bios_hashes.json` → đề xuất vào `BIOS/<tên chuẩn>` (đổi tên nếu cần); tên giống BIOS mà md5 sai → **bị chặn, không coi là game**.

## Phục hồi sau mất điện

Cài là giao dịch theo từng game: giải nén vào `<thẻ đích>/.loongmate-import/staging/<job>/` → tính SHA-256 + đọc lại xác minh → kiểm trùng
bằng hash thật → ghi nhật ký `job.json` → đổi tên sang tên tạm ẩn (`.<tên>.<job>.lmtmp`) trong thư mục đích → đổi tên cuối (file `.cue/.m3u` sau cùng) →
`fsync` thư mục → ghi `installed.jsonl` (trên thẻ) + `import-ledger.jsonl` (app_data) → Rescan Library.

- Job dở: `loongmate import jobs` liệt kê; `import recover --yes` dọn staging, hoàn tác game đang commit dở (xoá tên tạm/file do job tạo — chỉ khi SHA-256 đúng —
  và trả bản cũ từ `trash/`), **giữ** game đã commit xong. `import apply` tự dọn job chết trên thẻ đích trước khi chạy.
- File nửa vời không xuất hiện trong `Roms/` (tên tạm bắt đầu bằng `.` nên inventory/Leaf bỏ qua).

## CLI (`cli.py --json`)

```
import scan  [PATH ...]                         chỉ đọc
import plan  [PATH ...] [--card auto|primary|secondary] [--select ID ...] [--choice ID=HỆ ...] [--plan-out FILE]   chỉ đọc (+ ghi FILE)
import apply --plan FILE --yes [--choice ID=HỆ ...] [--conflict ID=keep|replace|copy|cancel ...] [--bios-ok ID|all ...] [--delete-source]
import jobs                                     chỉ đọc
import recover [--yes]
import mkdir [--card primary|secondary]
```

Không `PATH` = `Import/` của cả hai thẻ (nếu đã có). `PATH` là thư mục = MỘT nguồn dạng thư mục (zip bên trong bị chặn: lồng nhau).
Mã thoát: 0 ok · 1 có mục lỗi/bị chặn/chưa cài hoặc thiếu `--yes` · 2 dùng sai · 3 file kế hoạch bị từ chối (sai loại, bị sửa, quá 24 giờ).

**scan** → `{success, import_dirs:[{card,path,exists}], scan_roots, units:[Unit], errors:[{path,message}], skipped:[{source,rel,reason}], summary:{status:count}, limits}`

**Unit**: `id` (ổn định theo nội dung + dấu thời gian nguồn), `source`, `source_kind` (`zip|dir|pool|file`), `card` (`primary|secondary|null`), `in_import`, `origin[]`,
`name`, `kind` (`rom|multi|bios|archive`), `status` (`ready|needs_choice|blocked|unsupported|empty`), `system`, `folder`, `candidates[]`, `by` (`header|extension|user|null`),
`evidence`, `files:[{member,dest_rel,size,role}]`, `bytes`, `reasons[]` (lý do chặn), `warnings[]`, `bios:{name,md5,systems,dest}|null`.

**plan** → như scan cộng `ok`, `message`, `card:{selected,root,reason,needs_choice,options:[{card,root,free_bytes,usable,note}],needed_bytes}`, `needs_card_choice`, `needed_bytes`,
và mỗi unit thêm `action` (`install|present|conflict|choose_system|bios_confirm|bios_present|bios_conflict|blocked|unsupported|empty`), `dest_paths[]`, `conflict:{paths,options}`;
có `--plan-out` thì thêm `plan_id`, `plan_path`. File kế hoạch: `{schema:1, kind:"import", params:{card,scan_roots,choices,needed_bytes,limits}, items:[Unit], plan_id, created_at}`.

**apply** → `{success, job_id, message, results:[{id,name,status,message,dest[],bytes}], installed, already, blocked, failed, skipped, source_cleanup:[{path,action,to?}], card:{selected,root}, changed, rescan, plan_id}`;
`status`: `installed|already_present|kept|cancelled|skipped|blocked|failed`.

**Dùng lại cho giai đoạn B** (file tải từ thư viện riêng): tải về một đường dẫn tạm (không cần là `Import/`), rồi
`import plan <file> --plan-out F --json` → kiểm `ok`/`needs_card_choice`/`units[].action` → `import apply --plan F --yes [--conflict ...]`.
Nguồn ngoài `Import/` không bao giờ bị chuyển/xoá (vòng đời file do bên tải quản lý). Trong Python: `engine.importer.build_plan(...)`/`apply_plan(...)`.

## `pc/import_push.py` (đẩy từ PC)

```
python pc/import_push.py Game.gba Pack.zip "Disc Game/"            # → <thẻ phụ>/Import/
python pc/import_push.py Game.zip --card primary                    # → thẻ Leaf (chỉ khi bạn chủ động chọn)
python pc/import_push.py Game.zip --apply                           # đẩy, rồi chạy import scan + plan trên máy và in kết quả
python pc/import_push.py Game.zip --apply --yes --choice ID=GENESIS # ... và cài thật
```

Thẻ được dò trên máy (`pc/device_paths.py`: thẻ Leaf = thẻ có `env.sh`; thẻ phụ = thẻ khác đang mount trong danh sách ứng viên — **không** nạp `env.sh` để lấy
`UMRK_SECONDARY_SDCARD_PATH`, vì khi hai thẻ đã đổi chỗ `env.sh` trả mặc định `/media/sdcard1` = chính thẻ Leaf). File đi tar qua ssh vào thư mục tạm ẩn
`Import/.incoming-*`, so SHA-256 từng file với bản trên PC rồi mới chuyển ra `Import/`; lệch thì xoá thư mục tạm. Không ghi đè file trùng tên. Không có thẻ phụ → lỗi (mã 3), không tự dùng thẻ Leaf.
Không bao giờ ghép luồng stdin của ssh với `< /dev/null`. `--apply` chỉ xét ĐÚNG những file/thư mục vừa đẩy (các mục khác đang nằm sẵn trong `Import/` không bị đụng). `--sdcard DIR` / `--secondary-sdcard DIR` chỉ định tay thẻ (thử sandbox), `--engine-dir` trỏ engine trên máy khi chưa deploy.

## Giới hạn / chưa làm

- `.7z`/`.rar`: chưa. Không manifest cho gói nhiều hệ. Không đọc header CHD (luôn hỏi hệ). DOS `.exe` bị chặn (là thực thi). PCE/Lynx/Atari không có header kiểm được → dựa đuôi.
- Tiến trình: native/Python hiện "Đang nhập game… (Ns)" (không có thanh phần trăm theo byte).
- **Chưa kiểm chứng**: Leaf có tìm BIOS ở `BIOS/` của thẻ phụ không (BIOS đặt cùng thẻ đích với ROM); Leaf có quét đệ quy thư mục con trong `Roms/<hệ>/`
  (game nhiều file chỉ tạo thư mục con khi "bản sao"); chuỗi `SEGA 32X` @0x100 chỉ dựa tài liệu plutiedev.
- Thời gian đo trên máy thật dùng sandbox ở `/userdata` (flash trong), **không** phải thẻ SD FAT32: tốc độ thẻ thật có thể chậm hơn.

## Xem thêm

- [Thư viện riêng](THU-VIEN-RIENG.md): tải từ agent PocketBridge của bạn rồi cài bằng trình nhập này.
- [Homebrew](HOMEBREW.md#tải-trực-tiếp-trên-máy-pocket-1-không-qua-pc): Pocket 1 tự tải homebrew có giấy phép rõ ràng qua Wi-Fi rồi cài bằng trình nhập này (không có đường cài thứ hai).

## Chạy test và chứng minh test biết đỏ

```
python -m unittest tests.test_importer          # hơn 110 test hành vi thật, thẻ giả trong thư mục tạm, zip tạo ngay trong test
python -m unittest discover -s tests            # toàn bộ
```

Đặt `LOONGMATE_IMPORTER_PATH=<bản importer.py cố tình làm hỏng>` để chạy test lên bản hỏng: path traversal (bỏ chặn `..` và chốt staging), zip bomb (bỏ kiểm tỉ lệ), executable (bỏ chặn đuôi/magic),
fallback âm thầm sang thẻ chính, ghi đè save/BIOS, file nửa vời sau crash (không ghi nhật ký trước commit), ROM hiện trong `Roms/` trước commit — mỗi lỗi phải làm đúng các test tương ứng đỏ.
Mô phỏng mất điện: test thay `importer._checkpoint` bằng hàm ném `BaseException` (không bị `except Exception` bắt, nên không có dọn dẹp nào chạy) hoặc `os._exit` trong tiến trình con thật.
