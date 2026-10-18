# Thu thập homebrew có giấy phép rõ ràng (`pc/homebrew_sync.py`) và tải trực tiếp trên Pocket 1 (`engine/homebrew.py`)

Công cụ chạy trên PC/Mac/Pi (chỉ cần Python 3, không cài thêm gì). Nó tải **game homebrew có giấy phép rõ ràng** từ
[Homebrew Hub](https://hh3.gbdev.io/) về một thư mục thư viện, để Pocket 1 duyệt và tải qua agent PocketBridge.
Không đụng gì tới Pocket 1; không tải ROM thương mại.

## Chạy

```sh
# 1. Xem trước (không tải ROM, không ghi file vào thư viện; chỉ ghi cache JSON ~1,3 MB vào <dest>/.homebrew-cache)
python pc/homebrew_sync.py --dest /srv/roms --dry-run

# 2. Thử nhỏ: 20 mục đầu, xen kẽ các hệ
python pc/homebrew_sync.py --dest /srv/roms --limit 20

# 3. Tải hết (chạy lại bất cứ lúc nào: chỉ tải mục mới/đổi)
python pc/homebrew_sync.py --dest /srv/roms --systems gb,gbc,gba,nes --libretro-homebrew-dat
```

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--dest DIR` | thư mục thư viện (bắt buộc) |
| `--systems gb,gbc,gba,nes` | hệ cần lấy (mặc định cả bốn) |
| `--dry-run` | chỉ liệt kê + báo cáo |
| `--limit N` | chỉ xử lý N mục đã qua lọc giấy phép (xen kẽ giữa các hệ) |
| `--refresh` | bỏ cache JSON 24 giờ |
| `--force` | tải lại cả file đã có (vẫn không ghi đè file sửa tay / file lạ) |
| `--cache-dir DIR` | mặc định `<dest>/.homebrew-cache` |
| `--libretro-homebrew-dat` | đối chiếu SHA-1 với DAT homebrew của libretro-database, ghi tên chuẩn vào catalog (chỉ để thông tin) |

Mã thoát: `0` ổn; `1` có lỗi tải/ghi; `2` sai tham số, không lấy được danh mục, hoặc `homebrew-catalog.json` hỏng.

## Kết quả trên đĩa

```
<dest>/GB/<Tên game>.gb            ROM
<dest>/GB/<Tên game>.license.txt   giấy phép, tác giả, URL nguồn, slug, SHA-256, ngày tải
<dest>/GBC/ ... <dest>/GBA/ ... <dest>/NES/ ...
<dest>/homebrew-catalog.json       slug, title, system, file, sha256, license, author, source_url, ...
<dest>/.homebrew-cache/            cache JSON/DAT (ẩn; agent bỏ qua file bắt đầu bằng '.')
```

- Tên thư mục hệ lấy từ `systems.json` (thư mục chuẩn của Leaf): `GB`, `GBC`, `GBA`, `NES`. Hệ được quyết định theo **đuôi file**
  (`.gb`→GB, `.gbc`/`.cgb`→GBC (lưu thành `.gbc`), `.gba`→GBA, `.nes`→NES), vì ~16% mục cũ trong Hub không khai `platform`.
- Tên file an toàn cho FAT32/exFAT: thay `<>"/\|?*` và ký tự điều khiển bằng `_`, `:` thành ` -`, bỏ dấu chấm/cách ở cuối, tránh tên thiết bị
  (`CON`, `NUL`, ...), tối đa 100 ký tự. Hai game trùng tên → thêm ` [slug]`.
- **Ghi nguyên tử**: tải vào RAM, kiểm hash, ghi `<tên>.partial` rồi `rename`. Mất điện/đầy đĩa giữa chừng không để lại file nửa vời
  và không làm hỏng bản cũ.
- **Không ghi đè file không do công cụ tạo.** `homebrew-catalog.json` chính là sổ đăng ký: chỉ file có trong sổ *và* còn đúng SHA-256 đã ghi mới
  được cập nhật. File cùng tên mà bạn tự bỏ vào, hoặc file của công cụ mà bạn đã sửa tay, đều được giữ nguyên (báo `BO QUA`).
  Xoá `homebrew-catalog.json` = công cụ coi mọi file hiện có là của bạn. Nếu bạn xoá một ROM do công cụ tạo, lần chạy sau sẽ tải lại.
- Chạy lại: file đã có, cùng URL nguồn và (nếu catalog khai hash) cùng hash thì **không tải lại**. Giới hạn: nếu tác giả đổi file
  mà catalog không có hash thì công cụ không biết — dùng `--force`.

## Dùng làm thư viện PocketBridge

Đặt `--dest` đúng bằng `path` của một root trong `library.roots` của agent (xem `pocketbridge/docs/install-linux.md`, mục 3b "Thư viện ROM"):

```yaml
library:
  roots:
    - id: homebrew
      name: Homebrew có giấy phép
      path: /srv/roms/homebrew
```

Lưu ý từ mục 3b: đừng đặt dưới `/home`; agent (user `pocketbridge`) cần quyền đọc (`setfacl -R -m u:pocketbridge:rX /srv/roms/homebrew`);
agent chỉ phục vụ file thường **không ẩn** nên `.homebrew-cache/` không bị lộ, nhưng `homebrew-catalog.json` và các `.license.txt` thì có
(token `library:read` đọc được mọi file thường). Thư mục cấp 1 trùng tên hệ Leaf (`GB`, `GBC`, `GBA`, `NES`) tự được gắn `system_hint`.

## Tải trực tiếp trên máy (Pocket 1, không qua PC)

Pocket 1 tự lấy danh mục và tải game homebrew **qua Wi-Fi**, chọn rồi cài như một app: Library → **Homebrew** (bản Python: cùng tên). Mã nằm ở
`engine/homebrew.py` (module dùng chung với `pc/homebrew_sync.py`: lọc giấy phép, phân loại, kiểm hash, tên file FAT32 chỉ có MỘT bản).
Phần tải dùng lại đúng cơ chế job bền của [Thư viện riêng](THU-VIEN-RIENG.md) (`engine/libsource.py`: `.part`, Range + If-Range, tiến độ %, chọn thẻ,
tạm dừng/tiếp tục) và **cài bằng đúng trình [Nhập game](NHAP-GAME.md)** — không có đường cài thứ hai.

### Dùng

| Việc | Trên màn hình | CLI (`--json` cho máy đọc) |
|---|---|---|
| Lấy danh mục | Homebrew → **Cập nhật danh mục** (B = dừng) | `homebrew sync [--progress]` |
| Xem theo hệ / tìm | Homebrew → hệ máy (GB, GBC, GBA, NES) | `homebrew list [--system S] [--query Q] [--all]` |
| Chi tiết | khung giải thích bên phải: mô tả, tác giả, giấy phép, nguồn, "đã có chưa" | `homebrew show <slug>` (mục bị ẩn thì nói lý do) |
| Tải & cài | **A** ở game (một game) hoặc **X** chọn nhiều → "Tải & cài N game" → xem kế hoạch → hộp xác nhận (mặc định **Huỷ**) | `homebrew fetch <slug>... [--dry-run \| --yes] [--card auto\|primary\|secondary]` |
| Tiếp tục / huỷ job dở | Homebrew → "Tải dở: …" (A tiếp tục, X huỷ) | `homebrew jobs`, `homebrew resume <job> --yes`, `homebrew cancel <job> --yes` |

- **Y** đổi sắp xếp (Tên ↔ Mới thêm trước); mục **Lọc** (A) tất cả / chưa có / đã có trên máy (so tên file trong `Roms/`); dấu X được giữ khi đổi lọc.
  *Chưa làm*: nhảy theo chữ cái / ô tìm kiếm trên màn hình (khung giao diện chưa có sẵn thành phần này; CLI có `--query`).
- Màn hình ghi rõ **"Chỉ hiện game có giấy phép rõ ràng"**; số mục bị ẩn hiện ở đầu màn. `homebrew show <slug>` của mục bị ẩn trả lý do
  (`no_license`, `unrecognized_license`, `downloads_disabled`, ...).
- Thẻ đích: thẻ **phụ** trước; không dùng được thì **hỏi** có dùng thẻ Leaf không (không bao giờ tự chuyển) — như Nhập game.
- Không cần xác nhận cho `sync` (chỉ ghi cache trong dữ liệu LoongMate, không đụng thẻ). `fetch`/`resume`/`cancel` cần `--yes` (hoặc hộp xác nhận).

### Cái được ghi ở đâu

```
<userdata>/homebrew-cache/catalog.json   danh mục đã lọc (mục đủ điều kiện + danh sách mục bị loại kèm lý do) + thời điểm lấy
<userdata>/homebrew-licenses/<slug>.txt  ghi chú giấy phép của game ĐÃ CÀI (tác giả, giấy phép, nguồn, SHA-256, ngày tải)
<thẻ đích>/.loongmate-import/downloads/<job>/   file đang tải (.part) — xoá khi cài xong
<thẻ đích>/Roms/<hệ>/<Tên game>.<đuôi>   game đã cài (do importer)
```

Ghi chú giấy phép **cố ý nằm ngoài `Roms/`**: chưa kiểm được Leaf có bỏ qua `.txt` trong `Roms/` hay hiện nó như một game, nên không đặt vào đó.
(Bản PC `pc/homebrew_sync.py` vẫn ghi `.license.txt` cạnh ROM vì thư mục đó là thư viện PocketBridge, không phải `Roms/` của Leaf.)

### An toàn mạng (mỗi điểm có test đỏ/xanh trong `tests/test_homebrew_device.py`)

- **HTTPS kiểm chứng thư đầy đủ** (`CERT_REQUIRED` + `check_hostname`) bằng bộ CA của máy: `LOONGMATE_CAFILE`, rồi `/etc/ssl/certs/ca-certificates.crt`
  (có sẵn trên máy, đã kiểm). Không có bộ CA thì **báo lỗi rõ và không tải**; không bao giờ tắt kiểm tra. Đã thử trên máy thật: trỏ `LOONGMATE_CAFILE`
  sang một CA khác → `Chứng thư của hh3.gbdev.io KHÔNG được tin cậy`, không ghi gì.
- **Chỉ hai máy chủ**: `hh3.gbdev.io` và `raw.githubusercontent.com`, https, cổng 443, không userinfo. Chuyển hướng được theo **từng bước** (≤ 3), mỗi bước phải qua
  cùng bộ kiểm; ra host lạ / `http` / `file:` → chặn *trước khi nối* (test chứng minh bằng nhật ký của máy chủ lạ: không có kết nối nào).
- `User-Agent: LoongMate-homebrew/1 (+personal use)` (thiếu thì Hub trả 403); ≤ 1 yêu cầu/giây; thử lại 429/5xx với chờ tăng dần.
- Giới hạn: trang API ≤ 4 MiB, ROM ≤ 128 MiB (file lớn hơn là "vấn đề" ở bước kế hoạch, không tải).
- Hash trong catalog (md5/sha1/sha256, nếu có) được kiểm trên file tải về: **sai → xoá bản tải, dọn cả job, KHÔNG cài**. Catalog không có hash thì không có gì để kiểm — ghi SHA-256 thực tế vào ghi chú giấy phép.
- Tải tiếp (resume) dùng `Range` + `If-Range: <ETag mạnh của raw GitHub>`: 206 hợp lệ → nối; 200 (file đã đổi) → tải lại **từ đầu** (không bao giờ ghép hai phiên bản). Đã kiểm trên raw GitHub thật:
  `Range: bytes=100-` + `If-Range` đúng ETag → 206.
- Đi thẳng Wi-Fi (không qua SOCKS Tailscale); proxy `LOONGMATE_LIB_PROXY` chỉ áp cho Thư viện riêng.

### Số đo thật (2026-10-04)

| | Pocket 1 (Wi-Fi) | PC |
|---|---|---|
| `homebrew sync` (19 trang, ≤ 1 yêu cầu/giây) | **26,2 giây** (23,7 s trong một lần chạy qua màn hình native) | 18,4 giây |
| Kết quả | 107 / 1569 mục đủ điều kiện (GB 72, GBC 14, GBA 15, NES 6), 1462 bị ẩn | giống hệt |
| `fetch life-gb` (Conway's Game of Life, MIT, 32 KB) → thẻ giả | cài vào `Roms/GB/Conways Game of Life.gb`, SHA-256 `b739ec5b…c694b` (khớp bản tải độc lập trên PC) | — |

Thử trên **thẻ giả** `/userdata/lm-hb-sandbox/{sd1,sd2}`, không cài gì vào thẻ thật. Dụng cụ: `native/tools/hb-run.sh` (đẩy bản dựng + engine, chạy CLI, chụp màn native/Python offscreen).

### Giới hạn

- Kích thước file chỉ biết khi hỏi máy chủ lúc "xem kế hoạch" (API không có trường kích thước).
- Chỉ **một** ROM chính mỗi mục (như bản PC). Mục chỉ có `.zip`/`.7z` bị ẩn (`no_rom_file`).
- Danh mục lấy một lần mỗi lần bấm "Cập nhật" (không tự cập nhật nền); lỗi/dừng giữa chừng giữ nguyên danh mục cũ.
- Chưa kiểm: Leaf có bỏ qua `.txt` trong `Roms/` không (nên ghi chú giấy phép để ngoài). Sau khi cài, engine gọi Rescan Library của Leaf như Nhập game (trừ `--no-rescan`); các lần thử ở trên đều tắt rescan.

## Giấy phép: cái gì được nhận, cái gì bị loại

Công cụ **không suy diễn**. Mục được nhận khi giấy phép game (`gameLicense`, hoặc `license` kiểu cũ) nằm trong danh sách trắng, và nếu có
`assetsLicense`/`assetLicense` thì cái đó cũng phải nằm trong danh sách:

`MIT`, `Zlib`, `0BSD`, `Unlicense`, `CC0-1.0`, `MPL-2.0`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `GPL-2.0`/`GPL-3.0`
(+`-only`/`-or-later`), `LGPL-2.0/2.1/3.0` (+ hậu tố như trên), `CC-BY-[1-4].x`, `CC-BY-SA`, `CC-BY-NC`, `CC-BY-NC-SA`, `CC-BY-NC-ND`,
`CC-BY-ND` (đều **phải có số phiên bản**), và `public domain`. Chấp nhận cách viết `CC-BY-SA 4.0` và không phân biệt hoa thường.

NC/ND được nhận vì mục đích là dùng **cá nhân, không thương mại, không sửa đổi** trên máy của bạn. Bạn vẫn có nghĩa vụ ghi công/giữ
nguyên điều khoản khi chia sẻ lại; file `.license.txt` đi kèm để việc đó không bị quên.

Bị **loại** (và báo lý do trong báo cáo cuối):

| Lý do | Ví dụ thật trong dữ liệu |
|---|---|
| không có trường giấy phép | phần lớn danh mục (xem số liệu bên dưới) |
| chuỗi giấy phép lạ / không nhận diện | `Beta Cat`, `CC-BY ish`, `MIT / CC-BY-4.0 (Assets)`, `CC-BY-NC-SA` (thiếu phiên bản), `WTFPL-2.0`, `FSFAP` (hai cái này là giấy phép tự do thật nhưng không có trong danh sách chốt; muốn nhận thì thêm vào `_FIXED` có chủ đích) |
| `use-requirements.disable-downloads: true` | tác giả không cho tải (hiện không có mục nào đặt cờ này; vẫn kiểm) |
| không có file ROM trực tiếp | chỉ có `.zip`/`.7z`/... (nhóm này không xuất hiện trong các mục có giấy phép) |
| hash trong catalog không khớp file thật | kiểm sau khi tải, file bị bỏ. Ví dụ thật: `240p-test-suite` (NES): cả md5/sha1/sha256 trong catalog đều lệch file trên raw GitHub |

Số liệu đo ngày 2026-10-04 (dry-run toàn bộ, 1569 mục duy nhất): nhận **107** (GB 72, GBC 14, GBA 15, NES 6), loại 1462
(GB 817, GBC 411, GBA 217, NES 17), trong đó 1455 không có giấy phép và 7 chuỗi lạ. Con số đổi theo thời gian khi Hub được cập nhật.

## Nguồn và cách lấy (đã kiểm bằng request thật)

- Danh mục: `https://hh3.gbdev.io/api/search?platform=<GB|GBC|GBA|NES>&results=100&page=N` (API ghi trong
  <https://github.com/gbdev/homebrewhub/blob/main/API.md>). `results=100` chạy được (trang tài liệu nói 10). Mỗi mục trả sẵn
  `license`/`gameLicense`/`assetsLicense`, `files[]`, `baserepo`, `slug`.
- File ROM: raw GitHub của kho gốc, dạng `https://raw.githubusercontent.com/<chủ>/<kho>/master/entries/<slug>/<files[].filename>`:
  `gbdev/database` (GB/GBC), `gbadev-org/games` (GBA), `nesdev-org/homebrew-db` (NES). Đã tải thử một file thật từ mỗi kho.
  `/api/entry/<slug>/<file>` trả 404, nên không dùng.
- **Vì sao không clone kho git**: `gbdev/database` ~880 MB, `gbadev-org/games` ~590 MB (GitHub API `size`); API search cho đủ metadata
  chỉ với ~19 yêu cầu.
- Thiếu `User-Agent` thì máy chủ trả HTTP 403; công cụ gửi `LoongMate-homebrew-sync/1 (+personal use)`.
- Lịch sự với máy chủ: ≤ 1 yêu cầu/giây (kể cả khi tải file), thử lại 429/5xx với chờ tăng dần, cache JSON 24 giờ, chỉ tải file mới/đổi.
- Chỉ chọn **một** ROM chính mỗi mục (file `default`, rồi `playable`); các bản ngôn ngữ phụ không tải.

## Ghi công

- **Homebrew Hub** (gbdev) — <https://hh3.gbdev.io/>, dữ liệu: <https://github.com/gbdev/database>, <https://github.com/gbadev-org/games>,
  <https://github.com/nesdev-org/homebrew-db>. Mỗi game thuộc về tác giả của nó và theo giấy phép ghi trong `.license.txt`.
- **libretro-database** — <https://github.com/libretro/libretro-database>, giấy phép **CC-BY-SA-4.0** (GitHub license API trả `cc-by-sa-4.0`).
  Với `--libretro-homebrew-dat`, công cụ tải `metadat/homebrew/Nintendo - Game Boy.dat`, `... Game Boy Advance.dat`,
  `... Nintendo Entertainment System.dat` (không có DAT riêng cho GBC; DAT Game Boy dùng chung) về cache và chỉ lấy tên game khi SHA-1 khớp,
  ghi vào `libretro_name` trong catalog. Không phân phối lại DAT; không dùng kết quả để quyết định nhận/loại. Lần thử thật với 19 file đã tải không khớp mục nào
  (DAT homebrew của libretro chủ yếu là bản dịch/hack, ít trùng Hub).

## Giới hạn và nguồn đã loại

- Chỉ **GB/GBC/GBA/NES**: `https://hh3.gbdev.io/api/stats` (đã gọi) chỉ liệt kê bốn nền tảng này (gb 913, gbc 448, gba 245, nes 23 lúc đo).
- Mục không giấy phép bị loại dù tác giả đăng công khai: không có giấy phép thì mặc định là "giữ mọi quyền".
- **itch.io**: không có API công khai để liệt kê/tải game, và `https://itch.io/robots.txt` cấm `/game/download/` (đã đọc ngày 2026-10-04) → không làm.
- **PDRoms** (<https://pdroms.de>): không dùng. Feed RSS (`/rss/`) có tồn tại và `robots.txt` không cấm, nhưng feed không có trường giấy phép
  đọc được bằng máy, và điều khoản của trang cấm đọc tự động (đã kiểm 2026-10-04 tại
  <https://pdroms.de/privacy-policy-and-disclaimer>): *"Automated parsing and publishing of our news or RSS content is not permitted
  without prior written consent."* Trang cũng ghi kho có fan game *"usually developed without the explicit permission of the respective
  copyright holders"*.
- Danh mục có thể sai/lệch (xem ví dụ hash ở trên); công cụ chỉ chặn được những gì kiểm được. Giấy phép ghi trong catalog là do tác giả/người
  đóng góp Hub tự khai, công cụ không xác minh với kho mã nguồn.
- Thông điệp của công cụ viết không dấu (để chạy được trên mọi console, kể cả cp1252 của Windows).

## Kiểm thử

```sh
python -m unittest tests.test_homebrew_sync     # công cụ PC (HTTP giả)
python -m unittest tests.test_homebrew_device   # tải trực tiếp trên máy: máy chủ HTTPS giả trên loopback, thẻ giả, importer thật
```

`pc/homebrew_sync.py` import logic dùng chung từ `engine/homebrew.py` (lọc giấy phép, `classify_entry`, `hash_mismatch`, `fat32_name`, `raw_url`, ...); 27 test cũ giữ nguyên và vẫn xanh.

Dùng HTTP giả (không mạng). Để chứng minh test biết đỏ: đặt `HOMEBREW_SYNC_PATH` tới một bản `homebrew_sync.py` cố tình làm hỏng
(vd. `license_verdict` luôn trả `ok`, `hash_mismatch` luôn trả `None`, `atomic_write` ghi thẳng vào đích) — các test tương ứng phải đỏ.

`tests/test_homebrew_device.py` (40 test) dựng một máy chủ HTTPS thật trên 127.0.0.1 (chứng thư tự ký GOOD nằm trong `cafile`; UNTRUSTED không nằm trong; WRONGNAME nằm trong nhưng sai tên)
và ghi nhật ký từng request/bắt tay TLS ở phía máy chủ, nên các khẳng định "không chạm máy lạ" dựa trên nhật ký của máy chủ chứ không đọc mã. Phủ: lọc giấy phép + phân trang + lý do ẩn,
User-Agent, giới hạn 1 yêu cầu/giây (đồng hồ giả), chứng thư không tin cậy / sai tên host (không có request HTTP nào tới máy chủ), thiếu bộ CA (lỗi, không hạ cấp), whitelist host/cổng/scheme/userinfo,
chuyển hướng ra host lạ / `http` / `file:` / vòng lặp, hash catalog sai (không cài, dọn job), cài qua importer + ghi chú giấy phép ngoài `Roms/`, tải tiếp bằng Range + If-Range,
file đổi giữa chừng (tải lại từ đầu, không ghép), thẻ phụ vắng (hỏi, không tự dùng thẻ Leaf), CLI (`list/show/sync/fetch`, `fetch` không `--yes` thì từ chối).

**Chứng minh test biết đỏ**: đặt `HOMEBREW_PATH=<bản engine/homebrew.py cố tình làm hỏng>` rồi chạy `python -m unittest tests.test_homebrew_device`. Đã làm (2026-10-04), mỗi lỗi làm đúng các test tương ứng đỏ:

| Chỗ bị phá | Test đỏ |
|---|---|
| tắt kiểm chứng thư (`check_hostname=False`, `CERT_NONE` sau `create_default_context`) | 4: `test_context_is_fully_verifying`, `test_untrusted_certificate_blocked_before_any_http_byte`, `test_wrong_hostname_blocked`, `test_untrusted_cert_never_installs_anything_through_fetch` |
| bỏ chặn host (`check_url` luôn cho qua) | 5, gồm 3 test chuyển hướng/host lạ và `test_production_whitelist_is_exactly_two_hosts_on_443` |
| chuyển hướng không kiểm host đích | 4: ra host lạ, `http`/`file:`, vòng lặp, và `fetch` bị chuyển hướng sang host lạ |
| bỏ kiểm hash catalog (`file_hash_mismatch` trả `None`) | 3: hash sai vẫn được cài, lô có một hash sai, hash sai sau khi tải tiếp |
