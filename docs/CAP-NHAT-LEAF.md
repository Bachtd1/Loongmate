# Khi cập nhật Leaf thì làm gì

Dành cho người dùng Pocket 1 đã cài LoongMate, Tailscale, PocketBridge và (tuỳ chọn) Leaf Hot Tabs.
Cơ sở: tài liệu và script cập nhật chính thức của Leaf **v0.11.0** (máy đang chạy bản này, ghi trong
`.umrk/mlp1/release.json`, `installed_at 2026-09-28`). Mọi kết luận "còn/mất" dưới đây trích nguồn;
chỗ nào chưa tự chạy thử một lần cập nhật thật thì ghi rõ là **chưa kiểm trên máy**.

Đường dẫn trong tài liệu này viết theo thẻ Leaf hiện tại là `/media/sdcard1`. Xem mục 3: vị trí này có thể
đổi sau mỗi lần khởi động, nên đừng chép cứng.

## 0. Đã kiểm trên máy thật: Leaf v0.11.0 -> v0.13.0 (2026-10-17)

- **LoongMate còn nguyên, không cần cài lại.** 60/60 file của `LoongMate.pak` và `LoongMateAgent.pak` giống hệt từng byte
  trước/sau (so md5). Runtime Python `/userdata/loongmate-runtime` còn nguyên (3.12.15). Dữ liệu `.userdata/mlp1/LoongMate/`
  (SaveVault, nhật ký, cấu hình) còn đủ. Giao diện native chạy bình thường với thư viện hệ thống của bản mới.
- **Agent và Tailscale tự chạy lại** sau khởi động (cờ Start with Leaf trong `services-control.db` được giữ).
- **Cấu hình RetroArch không đổi**: `retroarch.cfg`, override, core option, remap giống hệt bản trước cập nhật
  (so bằng `leafconfig diff`).
- **Hot Tabs mất** đúng như dự kiến (launcher bị thay); bộ cài v0.11.0 không khớp v0.13.0, cần build lại (mục 6).
  **Thay thế không cần build lại:** Máy -> Leaf Hot Tabs -> "Tự tiếp tục qua Agent" (hoặc `cli.py autoresume on`). LoongMate Agent
  đọc dòng `launch-game ... resume=none` trong log của Leaf rồi nạp state (slot 99, không có thì state mới nhất) qua lệnh
  `retroarch-action load-state` của chính Leaf — không sửa file nào của Leaf nên không mất khi cập nhật. Đã thử trên máy
  (Leaf 0.13.0, 2026-10-17): mở từ tab Games nạp state sau 3 giây, bằng thời gian tab Recents tự nạp; mở từ Recents thì
  Agent không xen vào. Cần Agent đang chạy (Settings -> Services); chỉ game RetroArch.
- **Hai thẻ đổi chỗ** sau lần khởi động ngay sau đó (thẻ Leaf từ `/media/sdcard1` sang `/mnt/sdcard`); LoongMate tự dò lại.
- Nếu runtime Python trong `/userdata` bị xoá (cập nhật/reset máy), `launch.sh` tự giải nén lại từ `loongmate-runtime.tar.gz`
  đi kèm pak.

**Cấu hình Leaf/RetroArch giờ có sao lưu riêng:** Máy -> Cấu hình Leaf (hoặc `cli.py leafconfig snapshot|list|diff|restore`).
Agent tự sao lưu mỗi ngày và chụp thêm bản "sau cập nhật" ngay khi thấy Leaf đổi phiên bản; màn hình hiện "Leaf vừa cập nhật"
để so xem khoá cấu hình nào đã đổi, khôi phục theo nhóm nếu cần.

## 1. Cập nhật Leaf thay những gì, giữ những gì

Nguồn: `leaf-docs/.../guide/updating.md`, `Leaf/README.md`, và `umrk-launcher-install.sh` trong
`leaf-mlp1-sd-v0.11.0.zip` (script chạy khi cập nhật).

**Bị thay hoàn toàn (release-managed):**

| Thư mục | Bằng chứng |
|---|---|
| `.system/leaf/platforms/mlp1/launcher/` (gồm `bin/jawaka-launcher`) | `umrk-launcher-install.sh:1753` `replace_dir "$RELEASE_LAUNCHER" "$ACTIVE_LAUNCHER"` |
| `.system/leaf/platforms/mlp1/{bin,lib,cores,info,defaults,platform.d,autoconfig,boot-animation,emulators,runtime,shaders,i18n,assets,manifest.json}` | `umrk-launcher-install.sh:1757-1769` (xoá rồi chép lại) |
| 6 app do Leaf quản lý: `Apps/mlp1/SSHServer.pak`, `Thing-File.pak`, `CentralScrutinizer.pak`, `Fugazi.pak`, `Joe's Calibrage.pak`, `Apps/shared/RetroArch.pak` | `managed-apps.txt` trong zip; `umrk-launcher-install.sh:1669-1692` (`replace_dir` từng pak) |
| Phần rootfs của máy: `/etc/init.d/S50leaf`, `/usr/bin/umrk-leaf-session`, `umrk-mount-stubs`... | `umrk-launcher-install.sh:1661-1666` |
| Nguyên tắc chung: "Everything under `.system/leaf/platforms/mlp1` is release-managed (replaced on install/update)" | `Leaf/README.md:401-402` |

**Được giữ (Leaf cam kết không đụng):** `Roms/`, `Saves/`, `States/`, `.userdata/`, `.umrk/`
(`updating.md:66-69`; `Leaf/README.md:402-404`; `LEAF-INSTALL.txt` trong zip: "never touches your data").
Script cài chỉ ghi vào `.umrk/mlp1/` file `release.json` và hai file đánh dấu (`umrk-launcher-install.sh:1709,1785-1786`)
và chỉ tạo thư mục trống nếu thiếu (`:1734`, `:1720`). Thư mục `Apps/` chỉ bị đụng đúng 6 pak ở trên;
app của chúng ta (`LoongMate.pak`, `LoongMateAgent.pak`, `Tailscale.pak`, `PocketBridge.pak`) không nằm trong
danh sách đó nên **không bị xoá hay ghi đè**.

## 2. Bảng từng thành phần

| Thành phần | Sau cập nhật | Cần làm | Cách kiểm |
|---|---|---|---|
| **LoongMate** (`Apps/mlp1/LoongMate.pak`; dữ liệu ở `.userdata/mlp1/LoongMate/`: SaveVault, nhật ký, cấu hình) | **Còn** (không thuộc danh sách bị thay). | Không cần cài lại. Nếu tab Apps không hiện: System, Rescan Library | Mở app; tab SaveVault còn đủ snapshot |
| **LoongMate Agent** (service `org.loongmate.agent`) | **Pak còn.** Cờ "Start with Leaf" nằm ở `.umrk/mlp1/services-control.db`, trong `.umrk/` nên theo tài liệu là **còn** (`Jawaka/README.md:266-274`; installer không hề nhắc `services-control`). *Chưa kiểm trên máy qua một lần cập nhật thật.* | Settings, Services: nếu thấy tắt thì bật **Start with Leaf** và **Run** | Services hiện đang chạy; `agent_state.json` có giờ mới (trong `.userdata/mlp1/LoongMate/`) |
| **Tailscale** (`Apps/mlp1/Tailscale.pak` + service `org.loongmate.tailscale`; binary và state ở `.userdata/mlp1/tailscale/`) | **Còn** (pak, binary, state đăng nhập đều ngoài vùng bị thay). Cờ service như Agent | Như Agent: kiểm Services, bật nếu bị tắt. Không cần đăng nhập lại nếu `state/tailscaled.state` còn | `tailscale status` thấy máy online; PC `ssh` được vào IP Tailscale |
| **SSH Server** của Leaf (`SSHServer.pak`) | **Pak bị thay** bằng bản mới (có trong `managed-apps.txt`). State của nó ở `.userdata/mlp1/umrk-ssh-server/` (theo `docs/GHI-CHU-PHAT-TRIEN.md`, đã đọc từ log máy thật) nên theo tài liệu là còn; chưa kiểm sau một lần cập nhật thật. Cờ "Start with Leaf" của SSH cũng ở `services-control.db` | Kiểm Services: SSH Server phải đang chạy, nếu không PC (kéo save, deploy) mất đường vào. Nếu tắt: bật lại trên máy | Từ PC: `ssh -i ~/.ssh/pocket1_loongmate -p 2222 sshadmin@<IP> true`. Nếu `ssh` báo khoá host đổi: xoá dòng cũ trong `known_hosts` rồi xác nhận lại |
| **PocketBridge.pak** | **Còn** (không thuộc danh sách bị thay) | Không | Mở app, thấy danh sách node |
| **Leaf Hot Tabs** (repo riêng `Bachtd1/leaf-hottabs`; thay đúng `launcher/bin/jawaka-launcher`) | **MẤT.** `launcher/` bị thay nguyên thư mục nên máy trở về launcher gốc, vô hại (`leaf-hottabs/docs/README.md:108-110`; cơ chế ở `umrk-launcher-install.sh:1753`). Bản sao lưu trong `.userdata/mlp1/hot-tabs/backup/` chỉ khớp phiên bản Leaf cũ, **không được chép lại** | Trên PC (Git Bash), trong thư mục `leaf-hottabs`: `sh scripts/update-for-leaf.sh` rồi `sh scripts/update-for-leaf.sh --install` và **khởi động lại máy**. Xem mục 6 | LoongMate → Máy → Leaf Hot Tabs: "Đang dùng bản Hot Tabs" |
| **Lịch kéo save trên PC** (Task Scheduler `LoongMate-Pull`) | **Còn** (chạy trên PC, không phụ thuộc Leaf). Từ nay script tự dò thẻ Leaf (mục 3). `install_schedule.ps1` không truyền đường dẫn cứng nào | Không cần đổi lịch. Cần SSH Server trên máy chạy (xem hàng SSH) | Chạy tay `python pc\pull_saves.py`; log in dòng `Thẻ Leaf: ...`; `pull.log` nằm cạnh thư mục SaveVault trên PC |

## 3. Hai thẻ nhớ đổi chỗ sau khi khởi động

Quan sát thật ngày 2026-10-03: sau một lần khởi động lại, thẻ chứa Leaf (30 GB) chuyển từ `/mnt/sdcard`
sang `/media/sdcard1`, thẻ 64 GB sang `/mnt/sdcard`. Đây là hành vi của hệ điều hành máy, không phải lỗi Leaf.

- **Leaf và các app trên máy vẫn đúng.** Leaf công bố đường dẫn thật qua `env.sh`
  (`.system/leaf/platforms/mlp1/launcher/env.sh`: "Source this file ... Existing environment values win"),
  `LoongMate.pak/launch.sh` nạp file đó thay vì đoán; Tailscale và Agent làm tương tự.
- **Script trên PC trước đây ghi cứng `/mnt/sdcard`** nên sẽ kéo/cài nhầm thẻ. Đã sửa: `pc/pull_saves.py`,
  `pc/restore_menu.py`, `pc/deploy.py` tự dò qua SSH (`pc/device_paths.py`): tìm thẻ có
  `.system/leaf/platforms/mlp1/launcher/env.sh` trong `/mnt/sdcard`, `/media/sdcard1`, `/media/sdcard2`, `/media/sdcard3`
  (bỏ đường dẫn trùng như `/media/sdcard0 -> /mnt/sdcard`), rồi nạp `env.sh` để lấy `SDCARD_PATH`/`USERDATA_PATH`.
  Không nhớ kết quả qua lần chạy sau. Log ghi `Thẻ Leaf: /media/sdcard1`.
- Không thấy thẻ Leaf: lỗi rõ, mã thoát 1. Thấy hơn một thẻ có Leaf: mã thoát 3, chỉ định bằng `--sdcard /media/sdcard1`.
- Cờ cũ (`--device-vault`, `--apps-dir`, `--app-data`, `--remote-dir`) vẫn ghi đè. **Lưu ý:** `--device-vault` trỏ
  vào thẻ sai thì `pull_saves.py` vẫn tạo thư mục `LoongMate/` và `offsite.json` ở đó (hành vi cũ). Đừng truyền tay
  trừ khi cần.
- Chỗ khác còn ghi `/mnt/sdcard`: xem mục "Còn sót" cuối tài liệu.

## 4. Services có cần Enable lại không

- Cờ **Start with Leaf** lưu ở `.umrk/mlp1/services-control.db` (`Jawaka/README.md:266-270`). Installer không
  đụng tới file này (không có dòng nào nhắc `services-control` trong `umrk-launcher-install.sh`), và Leaf cam kết
  giữ `.umrk/` (`updating.md:66-69`). Vì vậy **dự kiến không phải bật lại**.
- Trạng thái **Run/Stop của phiên làm việc** thì luôn bị xoá mỗi lần daemon khởi động lại
  (`Jawaka/internal/services/supervisor.c:482-483`, "session Run/Stop must not survive a daemon restart"). Service
  nào đã đặt Start with Leaf sẽ tự chạy lại sau khi máy khởi động lại.
- Chưa kiểm trên máy thật qua một lần cập nhật. Phiên bản Leaf mới có thể đổi schema của file; nếu sau cập nhật
  thấy service tắt, vào Settings, Services, bật lại **Start with Leaf** rồi **Run**.

## 5. Checklist 5 phút sau khi cập nhật

1. Máy lên được và vào Leaf. Menu, Info, Device: đúng phiên bản mới.
2. Settings, Services: Tailscale, LoongMate Agent, SSH Server đều bật Start with Leaf và đang chạy.
3. Từ PC: `ssh -i ~/.ssh/pocket1_loongmate -p 2222 sshadmin@<IP Tailscale> true` thông. Nếu cảnh báo khoá host đổi,
   xử lý như bảng ở mục 2.
4. Từ PC: `python pc\pull_saves.py` in `Thẻ Leaf: ...`, kéo xong, `offsite.json` được ghi.
5. Mở LoongMate trên máy: SaveVault còn đủ snapshot; tab Apps còn LoongMate, Tailscale, PocketBridge.
6. `python pc\deploy.py --dry-run` in đúng `<thẻ Leaf>/Apps/mlp1` (không cài gì).
7. Hot Tabs: nếu dùng, chạy hai lệnh ở mục 6. Nếu chưa kịp, máy vẫn dùng bình thường (chỉ mở từ Games/Favorites sẽ không tự tiếp tục).
8. Nên kéo save về PC (`pull_saves.py`) **trước** khi cập nhật, vì cập nhật cần máy khởi động lại.

## 6. Leaf Hot Tabs sau khi cập nhật Leaf

Hot Tabs (mở game từ Games/Favorites cũng tự tiếp tục như Recents) là bản `jawaka-launcher` được build lại cho **đúng**
phiên bản Leaf đang cài. Cập nhật Leaf thay cả thư mục launcher, nên Hot Tabs tự mất; máy vẫn chạy bình thường.

Cài lại — trên PC, mở **Git Bash**, Pocket 1 bật Wi-Fi + SSH server:

```sh
cd leaf-hottabs        # thư mục repo leaf-hottabs đã clone
sh scripts/update-for-leaf.sh             # dựng + đẩy gói, chạy thử; CHƯA cài
sh scripts/update-for-leaf.sh --install   # cài thật
```

Rồi **khởi động lại Pocket 1**. Script tự dừng và không cài gì nếu bản build gốc không khớp md5 bản Leaf chính thức,
hoặc patch không áp được lên mã launcher mới — khi đó cần sửa patch trong `leaf-hottabs` cho khớp mã launcher mới. Đã giả lập trước với Leaf v0.12.1: chạy trọn.

Bật/tắt và kiểm trạng thái ngay trên máy: **LoongMate → Máy → Leaf Hot Tabs**
- *Tự tiếp tục khi mở từ Games/Favorites*: Bật/Tắt, hiệu lực ở lần mở game kế tiếp, không cần khởi động lại.
- *Gỡ Hot Tabs / Cài lại*: có xem trước, hộp xác nhận mặc định Huỷ; xong phải khởi động lại máy.
- "Không khớp phiên bản Leaf — cần build lại" nghĩa là Leaf vừa cập nhật: chạy hai lệnh ở trên.

Khẩn cấp (launcher không lên sau khi cài): cắm thẻ Leaf vào PC, vào `.system/leaf/platforms/mlp1/launcher/bin/`,
xoá `jawaka-launcher`, đổi tên `jawaka-launcher.stock` thành `jawaka-launcher`.

## 7. Lưu ý khi tự cập nhật bằng thẻ nhớ

Giải nén ZIP vào **gốc thẻ Leaf** ngay trên thẻ, không kéo thả từ máy tính (thư mục `.system` ẩn sẽ bị bỏ sót:
`updating.md:57-62`); đặt thẻ ở **khe chính** (`install.md:61-64`). Script cài tự tìm thẻ trong
`$SDCARD_PATH`, `/mnt/sdcard`, `/media/sdcard1` (`umrk-launcher-install.sh:9-24`).

## Còn sót: các chỗ khác ghi `/mnt/sdcard`

Kiểm bằng `grep -rn /mnt/sdcard`. Phân loại: (a) đúng vì đọc môi trường trước; (b) chỉ là mặc định cuối sau môi trường
(chấp nhận); (c) sai thật khi hai thẻ đổi chỗ.

- (a)/(b): `pak/Apps/mlp1/*/launch.sh` và `tailscale/Tailscale.pak/launch.sh` (nạp `env.sh` trước, `/mnt/sdcard` chỉ khi không tìm ra thẻ),
  `engine/update.py:42-43`, `engine/agent.py:67`, `engine/probe.py` (đọc `SDCARD_PATH(S)` trước, chỉ đoán khi không có),
  `native/src/lm_cli.c:30`, `native/src/lm_common.c:33`.
- (c) đã sửa: `engine/nodes.py` `DEVICE_TS_BIN` (tìm binary Tailscale; nay ưu tiên `$USERDATA_PATH/tailscale/tailscale`).
- (c) chưa sửa (chỉ là script dev và ví dụ lệnh trong tài liệu): `native/tools/*.sh` (`c4-shot`, `d4-run`, `deploy-pak.sh`,
  `device-shot`, `e2-run`, `h-run`), `INSTALL.md`, `TAILSCALE.md`, `pc/restore_to_device.md`. Khi dùng các lệnh đó,
  thay `/mnt/sdcard` bằng thẻ Leaf thật (xem bằng `python pc\deploy.py --dry-run`).
