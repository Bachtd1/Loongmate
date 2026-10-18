# Đóng góp cho LoongMate

Cảm ơn bạn muốn góp sức. Đóng góp nhỏ và cụ thể được chào đón nhất: một hệ máy, một luật kiểm tra, một đường dẫn save
đã kiểm trên máy, một bản dịch, một lỗi kèm gói chẩn đoán.

## Báo lỗi

1. Trên máy: Home → **Y** (Xuất gói hỗ trợ). Gói đã ẩn tên ROM, mật khẩu Wi-Fi, khoá SSH.
2. Mở issue, ghi: phiên bản Leaf (Menu → Info), bạn làm gì, mong đợi gì, thấy gì; đính kèm gói nếu được.
3. Không đính kèm ROM, BIOS hay save của người khác.

## Cấu trúc mã

| Thư mục | Nội dung |
| --- | --- |
| `engine/` | Engine Python 3.12, chỉ thư viện chuẩn. `cli.py` là điểm vào; mọi lệnh trả JSON với `--json` |
| `native/src/` | Giao diện C (`lm-home`), dùng Catastrophe; gọi engine bằng `fork` + `execv`, không qua shell |
| `pak/` | Khung `.pak` cho Leaf (`launch.sh`, `pak.json`), script đóng gói |
| `tests/` | Test `unittest` cho engine (máy chủ HTTPS giả, giả lập mất điện, thẻ giả) |
| `pc/` | Công cụ phía PC: triển khai qua SSH, kéo save, đồng bộ homebrew |
| `simulator/` | Giả lập giao diện cũ trên trình duyệt (dùng cho test API) |
| `systems.json`, `bios_hashes.json` | Danh mục hệ máy và md5 BIOS công khai (nguồn docs.libretro.com) |

Phần riêng của Leaf nằm ở `engine/leafsys.py` (danh mục hệ máy), `engine/leafconfig.py` (cấu hình),
`engine/autoresume.py` (tự tiếp tục), `engine/hottabs.py` và `engine/probe.py` (thẻ nhớ, `env.sh`).

## Chạy test

```sh
python -m unittest discover -s tests        # ~1060 test, ~3 phút
```

Mọi thay đổi hành vi cần test đi kèm. Lỗi đã sửa thì thêm test tái hiện đúng lỗi đó (fail trước khi sửa, pass sau).

## Build giao diện native

Cần Docker. Lấy sysroot (thư viện của máy) một lần qua SSH, rồi build chéo ARM64:

```sh
LM_SSH="ssh -p 2222 sshadmin@<IP máy>" sh native/fetch-sysroot.sh   # một lần
sh native/build.sh                                                  # -> native/build/lm-home
cp native/build/lm-home pak/Apps/mlp1/LoongMate.pak/bin/lm-home
```

Thử giao diện không cần máy (container ARM64, dữ liệu giả, chụp màn hình):

```sh
sh native/tools/emu-shot.sh home "SHOT"
sh native/tools/emu-shot.sh lib "RIGHT,DOWN,A,SHOT"     # ảnh ra native/build/emu-out/
```

## Đóng gói và cài thử

```sh
# đặt runtime CPython 3.12 ARM64 ở pak/loongmate-runtime.tar.gz (không nằm trong git, xem INSTALL.md)
sh pak/build.sh                                   # -> pak/build/LoongMate.pak.zip
python pc/deploy.py --key ~/.ssh/<khoá> --host <IP>   # đẩy lên máy qua SSH, kiểm hash, có quay lui
```

## Quy ước

- **An toàn trước hết**: không xoá file của người dùng (đưa vào Quarantine), có bước xem trước, xác nhận mặc định Huỷ,
  ghi nguyên tử (file tạm + fsync + đổi tên), không ghi đè khi trùng tên khác nội dung.
- **Không đoán về Leaf**: hành vi của Leaf phải dẫn được nguồn (mã Jawaka, tài liệu Leaf, hoặc kiểm trên máy thật) và ghi
  nguồn trong comment hoặc tài liệu.
- **Không thêm phụ thuộc** vào engine (chỉ thư viện chuẩn Python) để gói chạy được trên máy không có pip.
- **Không nội dung có bản quyền**: không ROM, BIOS, link tới trang ROM thương mại; không mã vượt quảng cáo, đếm ngược,
  captcha hay DRM.
- Script chạy trên máy là POSIX sh, xuống dòng LF (CI kiểm).
- Tài liệu và thông báo cho người dùng viết tiếng Việt; tên hàm, biến tiếng Anh.

## Gửi thay đổi

1. Fork, tạo nhánh từ `main`.
2. Chạy test; nếu đổi giao diện, kèm ảnh chụp emulator hoặc máy thật.
3. Mở pull request, mô tả: vấn đề, cách sửa, đã kiểm trên máy thật hay chỉ bằng test.

Bằng việc gửi đóng góp, bạn đồng ý phát hành nó theo giấy phép MIT của dự án.
