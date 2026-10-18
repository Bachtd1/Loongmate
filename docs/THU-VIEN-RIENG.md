# Thư viện riêng (Library Sources, giai đoạn B)

Library → **Thư viện riêng**: duyệt, tải và cài game từ một thư mục ROM trên máy **của chính bạn** (Raspberry Pi, PC, Mac) — nơi bạn đã chạy agent
[PocketBridge](../../pocketbridge/docs/install-linux.md) và khai báo `library.roots`. Tải về rồi giao cho trình **Nhập game** (`docs/NHAP-GAME.md`):
không có đường cài thứ hai, mọi chốt an toàn của importer vẫn áp dụng. Đây là giai đoạn B của `leaf-sources.md` (phía Pocket 1).

## Pháp lý (đọc trước)

- LoongMate **không có danh mục ROM nào**, không tải gì từ internet, không cào website. Nó chỉ nói chuyện với node bạn đã ghép đôi bằng bundle do chính bạn tạo.
- Chỉ đặt vào thư viện nội dung **bạn có quyền sử dụng** (bản dump của chính bạn, homebrew). Test và sandbox của dự án chỉ dùng byte tự dựng + dữ liệu ngẫu nhiên.

## 1. Dựng thư viện (trên Pi / PC / Mac)

1. Cài agent PocketBridge (xem `docs/install-linux.md`, `install-macos.md`, `install-windows.md` của repo pocketbridge) và đặt `listen` là IP Tailscale hoặc LAN của máy.
2. Khai báo thư viện trong `config.yaml` của agent (cần khởi động lại agent):

```yaml
library:
  max_file_bytes: 4294967296   # file lớn hơn không được liệt kê, tải bị 403 (LoongMate còn giới hạn 2 GiB/file, xem mục Giới hạn)
  hash_workers: 1              # sha256 tính nền; 0 = tắt -> sha256 luôn null -> LoongMate KHÔNG tải (xem "SHA-256")
  roots:
    - id: roms                 # [a-z0-9-]{1,32}
      name: ROM của tôi        # tên hiển thị trên Pocket 1
      path: /srv/roms          # tuyệt đối; Windows: C:\Roms; macOS: /Users/ban/Roms
```

3. Bố cục thư mục: tên thư mục **cấp 1** trùng tên hệ máy chuẩn của Leaf (`GBA`, `SNES`, `PSX`, `GENESIS`… không phân biệt hoa/thường) thì agent gắn `system_hint`
   và LoongMate cài thẳng đúng hệ. Thư mục khác (hoặc file nằm thẳng trong root): LoongMate đoán theo đuôi trong `systems.json` **chỉ khi đuôi chỉ thuộc một hệ**;
   đuôi mơ hồ (`.bin`, `.zip`, `.chd`…) vào nhóm "Chưa rõ hệ" và importer nhận diện bằng header — không được thì **báo và không cài, không đoán**.
   Game nhiều file: để `.cue` và `.bin` **cùng một thư mục con** (vd `PSX/Tên game/Tên game.cue`), chọn cả hai file.
4. Quyền đọc là việc của bạn: agent chạy bằng user riêng, **không tự chmod**. Linux: `sudo setfacl -R -m u:pocketbridge:rX /srv/roms`. Đừng đặt thư viện dưới `/home`, `/root`
   (unit systemd có `ProtectHome`). Root không được chứa hoặc nằm trong thư mục cấu hình/log/state của agent.
5. Kiểm: `sudo -u pocketbridge pocketbridge-agent config check --config /etc/pocketbridge/config.yaml` — thư mục không đọc được báo `LỖI` và mã thoát 1.
6. Tạo bundle ghép đôi (token chỉ có quyền `library:read`, token đổi qua kênh TLS đã ghim fingerprint, không bao giờ hiện trên màn hình/clipboard):

```sh
sudo pocketbridge-agent pair --config /etc/pocketbridge/config.yaml --id pocket1 --scopes library:read \
     --name "Pi nhà" --host 100.64.0.5 --expires 4320h --out pocket1-bundle.json
```

   `--host` là địa chỉ **Pocket 1 dùng để tới agent** (IP Tailscale, hoặc tên MagicDNS `*.ts.net`). **Agent đang chạy cần tới ~5 giây để nạp mã ghép đôi mới**: đợi vài giây trước khi nhập bundle
   (nhập ngay có thể báo "401 mã sai/hết hạn" — kiểm chứng với agent thật 2026-10-04). Mã bootstrap hết hạn sau 15 phút (`--ttl`, tối đa 1 giờ) và **chỉ dùng được một lần**.
7. Chép `pocket1-bundle.json` vào **`<thẻ Leaf>/PocketBridge-import/`** (hoặc `Import/`) trên Pocket 1 (USB/máy tính, hoặc `python pc/import_push.py` rồi chuyển file).

## 2. Dùng trên máy

**Library → Thư viện riêng** (cả giao diện native lẫn Python):

| Bước | Màn | Ghi chú |
|---|---|---|
| Ghép đôi | *Ghép đôi thư viện* | liệt kê bundle trong `PocketBridge-import/` và `Import/` của cả hai thẻ (bundle hỏng hiện lý do, không bị đụng). **A** → hộp xác nhận (mặc định Huỷ) → ghim fingerprint, đổi mã một lần, lưu token, **xoá bundle**. |
| Node | *Thư viện riêng* | mỗi node đã ghép đôi (A mở, X gỡ ghép đôi) + các **job tải dở** (A tiếp tục, X huỷ). |
| Thư mục | *thư mục* | các `library.roots` agent khai báo (số file, dung lượng, `đang quét`). |
| Hệ máy | *hệ máy* | tóm tắt theo hệ; "Chưa rõ hệ". Mất mạng → dùng cache và hiện **"DANH SÁCH CŨ (lấy lúc …)"**. |
| Game | *game* | **X** chọn nhiều, **Y** đổi sắp xếp (Tên / Dung lượng), mục **Lọc** (tất cả / chưa có trên máy / đã có trên máy — so tên file với `Roms/<hệ>/` cả hai thẻ). Dấu chọn giữ nguyên khi đổi lọc/sắp xếp. |
| Tải & cài | hộp xác nhận | **A** trên mục "Tải & cài N file" (hoặc A trên một game): hỏi server kích thước + SHA-256, chọn thẻ đích, liệt kê file/kích thước/dung lượng cần → xác nhận (**mặc định Huỷ**). |
| Tiến độ | hộp "Đang tải & cài… NN%" | thanh theo **byte** (đã tải / tổng, file i/n, "kiểm hash", "đang cài"). Native: **B = tạm dừng** (tải tiếp được sau). Python: không có nút dừng (tắt máy cũng tiếp tục được, xem dưới). |
| Kết quả | hộp kết quả | từng game: `installed` / `already_present` / `blocked` / `failed` / `skipped` + lý do **nguyên văn từ importer**; rồi **đứng yên tại danh sách** (đã tải lại: game vừa cài hiện "đã có"). |

**Thẻ đích** chọn như importer: mặc định thẻ **phụ** (gắn, ghi được, đủ chỗ). Không dùng được thì **hỏi** "Dùng thẻ Leaf" — không bao giờ tự chuyển. Dung lượng cần = `2 × tổng file + 128 MiB`
(file tải + staging của importer + dự phòng; file `.zip` giải nén ra lớn hơn thì importer tự kiểm lại khi cài).

**Mất Wi-Fi / ngủ máy / tắt máy giữa chừng:** file tải dở nằm ở `<thẻ đích>/.loongmate-import/downloads/<job>/files/<đường dẫn>.part` cùng `job.json`.
Lần sau mở **Thư viện riêng → "Tải dở: …"** (hoặc `library resume <job>`): tiếp tục bằng `Range` + `If-Range` (ETag `"sha256:…"`).
Server trả **206** → nối; trả **200** (file đã đổi / server mất hash) → tải lại **từ đầu**, **không bao giờ nối** vào `.part` cũ; **416** → kiểm `.part` đã đủ chưa. Xong hoặc huỷ thì job + `.part` bị dọn.

## 3. CLI (`cli.py library … --json`; native chỉ gọi các lệnh này)

```
library nodes | overview | bundles                                  chỉ đọc (overview = nodes + job dở + bundle trong một lần gọi)
library pair BUNDLE.json --yes | unpair NODE --yes                  ghép đôi / gỡ (xoá token + cache)
library roots NODE                                                  chỉ đọc (gọi server)
library list NODE ROOT [--system S] [--query Q] [--offline] [--all] chỉ đọc; chỉ cập nhật CACHE index trong app_data. Có --system/--query/--all thì kèm "entries"
library fetch NODE ROOT PATH... [--card auto|primary|secondary] [--conflict keep|replace|copy|cancel] --dry-run     chỉ kiểm + lập kế hoạch (không ghi gì)
library fetch NODE ROOT PATH... --yes [--progress] [--card ...]      tạo job, tải, kiểm SHA-256, cài bằng importer
library jobs                                                        chỉ đọc
library resume JOB --yes [--progress] | cancel JOB --yes
```

Mã thoát: 0 ok · 1 thất bại/chưa làm (thiếu `--yes` khi không có TTY cũng là 1) · 2 dùng sai. `--progress`: dòng `LMPROGRESS <phase> <done> <total> <i> <n>\t<tên>` lên **stderr** (native đọc để vẽ %); stdout vẫn chỉ là JSON.
Lệnh chỉ đọc **không ghi gì lên thẻ hay `/userdata`** (kiểm bằng test: so cây thư mục trước/sau); `list` chỉ ghi cache `<app_data>/library-cache/<node>__<root>.json`.

JSON chính — `list`: `{success, node, root, stale, from_cache, fetched_at, error, total, systems:[{system,count,bytes}], entries?:[{path,name,size,mtime,sha256,system,installable,problem,present}]}`
(`stale` = lần đọc server vừa thất bại nên dùng cache; `--offline` thì `from_cache` nhưng không `stale`).
`fetch --dry-run`: `{ok, message, files:[{path,name,size,sha256,system,problem,present}], problems:[{path,reason}], total_bytes, needed_bytes, card:{selected,root,reason,needs_choice,options}, needs_card_choice}`.
`fetch`/`resume`: `{success, started, job_id, state, resumable, message, installed, already, blocked, failed, skipped, results:[{id,name,status,message,dest,bytes}], changed, rescan, error_kind?}`.

## 4. An toàn và cách hoạt động

| Mối nguy | Chặn thế nào |
|---|---|
| Máy lạ giả danh node (MITM) | TLS tự ký, **ghim fingerprint** `sha256:` + hex(SHA-256(DER lá)) của bundle; so ngay sau bắt tay, **trước khi gửi byte HTTP nào**. Lệch → chặn, **không gửi token**, không dùng CA. Lỗi ghim không bị che bằng cache cũ. |
| Lộ token | token dài hạn ở `/userdata/LoongMate/secrets/<id>.token` (**0600**, thư mục **0700**; `/userdata` là ext4). Sau khi ghi, đọc lại quyền thật; filesystem không giữ quyền (FAT32) → xoá + từ chối thay vì lưu bừa. Đọc quyền rộng hơn 0600 → từ chối dùng. Token **không** nằm trong `library-nodes.json`, log, JSON của CLI hay màn hình. |
| Bundle bootstrap bị dùng lại | đổi **một lần**; sau khi token + node nằm trên đĩa thì **xoá bundle**; `409 already_exchanged` → báo "tạo pairing mới", **không tự thử lại**, file giữ nguyên; mất mạng sau khi gửi POST → báo không rõ + tạo mới (agent không trả token lần hai). Chỉ nhận token có `library:read`. |
| Server (hoặc ai đó trên đường) gửi đường dẫn độc | mọi `path` từ index được kiểm lại như agent kiểm đầu vào (không `..`/tuyệt đối/`\`/ký tự điều khiển/thành phần bắt đầu `.`, ≤ 1024 byte, ≤ 32 cấp); mục sai bị **bỏ riêng**. Tên file trên thẻ được làm sạch cho FAT32. JSON bị giới hạn kích thước (index 2 MiB/trang, còn lại 16–64 KiB), số trang và số mục. Schema `library_roots`, `library_index`, `pair_exchange_response`, `pairing_bundle` kiểm bằng code (bundle: không nhận khoá lạ). |
| File hỏng / bị đổi | tải xong **SHA-256 phải khớp** hash server công bố (ETag). Sai → **xoá `.part` và cả job, không cài**. Server **chưa băm xong** (`sha256: null`, không ETag) → báo "server chưa băm xong, thử lại sau" và **không tải** (vì không có gì để kiểm và không tiếp tục an toàn được). Danh sách đã cũ so với server (hash đã đổi) → từ chối, làm mới rồi chọn lại. |
| Nối nhầm khi resume | chỉ nối khi 206 + `Content-Range` đúng điểm bắt đầu/tổng + ETag không lệch; mọi trường hợp khác bỏ `.part`, tải lại từ đầu. `.part` dài hơn file server → bỏ. Server gửi thừa byte → bỏ. |
| Cài | đúng `importer.build_plan` + `apply_plan` (kiểm thực thi/zip bomb/nested/trùng tên/BIOS, giao dịch chống mất điện). Nguồn nằm ngoài `Import/` nên importer không chuyển/xoá; job do LoongMate dọn. Trùng tên khác nội dung: mặc định **giữ bản cũ** (`--conflict`); BIOS **không bao giờ tự xác nhận** từ thư viện (dùng Nhập game). |
| Hết chỗ | kiểm trước khi tải; hết chỗ lúc cài → giữ file đã tải, `resume` khi đã giải phóng chỗ. |

Tailscale **userspace** trên Pocket 1: app chỉ vào tailnet qua **SOCKS5 `127.0.0.1:1055`** (CONNECT không xác thực; tên MagicDNS gửi dạng ATYP=3 vì resolver hệ thống không phân giải MagicDNS). Tự dùng khi host thuộc
`100.64.0.0/10`, `fd7a:115c:a1e0::/48`, đuôi `.ts.net`, hoặc không phân giải được — **và** cổng proxy đang mở; còn lại đi thẳng. Biến môi trường (chỉ để thử): `LOONGMATE_LIB_PROXY=none|host:port`,
`LOONGMATE_SECRETS_DIR=<thư mục>` (mặc định `/userdata/LoongMate/secrets`; thư mục không tồn tại → báo lỗi, **không** lùi về thẻ FAT32). Hành vi giữ cùng client C++ PocketBridge (`client/src/transport/{Socks5,Route,AgentClient}.cpp`).

Tệp trên máy: `<userdata>/library-nodes.json` (id, name, host, port, fingerprint, fallback_hosts, paired_at, scopes), `<userdata>/library-cache/`, `<userdata>/library-ledger.jsonl` (nhật ký: ghép đôi, tạo/xong/huỷ job, hash sai),
`<thẻ đích>/.loongmate-import/downloads/<job>/`. `fallback_hosts` chưa có giao diện sửa: chỉnh tay trong `library-nodes.json` (host dự phòng thử theo thứ tự khi host chính không nối được; lệch ghim thì dừng ngay, không thử host khác).

## 5. Giới hạn / chưa làm / đã biết

- **Không có nút dừng ở giao diện Python** (chỉ native có B = tạm dừng). Tắt tiến trình/máy vẫn tiếp tục được bằng "Tải dở".
- File > 2 GiB (giới hạn importer/FAT32), `.7z/.rar/…` (chưa hỗ trợ trên máy), file thực thi, file rỗng: bị từ chối **trước khi tải**, kèm lý do.
- Tải **tuần tự**, một kết nối giữ sống; không tải song song. Không tải lại tự động trong một lần chạy: mất mạng thì job dừng và báo `resume` (không vòng thử lại vô hạn).
- Index tối đa 100 000 file/thư mục (giới hạn của agent). Native đọc danh sách một hệ qua `stdout` (trần 4 MiB ≈ 11 000 file trong **một** hệ); lớn hơn thì màn game báo lỗi JSON.
- Game nhiều file chỉ ghép đúng khi các file nằm **cùng thư mục con** và được chọn cùng lúc; file `.zip` cùng thư mục với `.cue` thì giao từng file.
- Nhóm "Chưa rõ hệ" mà importer không nhận ra bằng header → `skipped`/`blocked` (không đoán, file tải được dọn). Hãy đưa file vào thư mục tên hệ (`GBA/`, …) để có `system_hint`.
- Danh sách chỉ hiện file có đuôi trong `systems.json` **hoặc** có `system_hint`; luôn ẩn `.txt .md .license` và các đuôi "meta" của importer (`.nfo .png .json …`).
- Một lần ghép đôi chỉ có token `library:read`. Không có chức năng tải lên, xoá, hay sửa thư viện trên server.
- Thời gian/tốc độ trên máy thật qua SD FAT32 và Tailscale **chưa đo** (xem báo cáo kiểm chứng bên dưới).

## 6. Kiểm thử

```
python -m unittest tests.test_libsource          # 65 test hành vi thật: server TLS giả + SOCKS5 giả + thẻ giả + importer thật (2 test quyền 0600 chỉ chạy trên Linux/POSIX)
python -m unittest discover -s tests             # toàn bộ dự án
```

- Server giả (`http.server` + `ssl`) hỗ trợ Range/If-Range/ETag/416, ghi **log từng request (có Authorization hay không) và từng lượt TLS**: "fingerprint sai → không gửi token" được chứng minh bằng log của **server**, không bằng đọc code.
- **Chứng minh test biết đỏ:** `LOONGMATE_LIBSOURCE_PATH=<bản libsource.py cố ý làm hỏng> python -m unittest tests.test_libsource…` — đã thử: bỏ so fingerprint, nối `.part` khi gặp 200, bỏ so SHA-256, bỏ chặn khi server chưa băm, chmod 0644, không xoá bundle → mỗi lần đúng các test tương ứng **đỏ** (`native/tools/lib_red.py` chạy lại toàn bộ).
- **Đầu-cuối với agent PocketBridge thật** (build từ HEAD repo pocketbridge, Docker): `native/tools/lib_e2e.py` (ghép đôi bootstrap, 409, list, tải bị **proxy cắt giữa chừng**, resume 206, cài vào `Roms/<hệ>/`, game .cue+.bin, file đổi giữa chừng → 200 tải lại từ đầu, dọn).
- Native/Python trong container ARM64 (qemu) với agent arm64 thật: `native/tools/lib-emu.sh menu|pair|browse|fetch|pause|py` (ảnh `native/build/emu-out/lib_*.png`; **không** thay ảnh chụp trên máy thật). Dịch vụ thử dùng chung (`lib_devsvc.py`): agent + proxy giới hạn tốc độ/cắt kết nối + thẻ giả.
