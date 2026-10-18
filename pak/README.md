# Đóng gói LoongMate cho Leaf

`pak/Apps/mlp1/` là khung hai app Leaf: `LoongMate.pak` (giao diện) và `LoongMateAgent.pak` (service chạy nền).
Đã chạy thật trên Miniloong Pocket 1 với Leaf 0.11.0 và 0.13.0.

## Build

```sh
sh native/build.sh                                            # giao diện C -> native/build/lm-home (cần Docker)
cp native/build/lm-home pak/Apps/mlp1/LoongMate.pak/bin/lm-home
sh pak/build.sh                                               # -> pak/build/LoongMate.pak/ và LoongMate.pak.zip
sh pak/build-all.sh                                           # gói phát hành LoongMate-AllInOne.zip (cần HOTTABS_DIR)
```

`build.sh` chép khung pak, đóng `engine/*.py` vào `engine/`, ghi đè `res/systems.json` và `res/bios_hashes.json` từ bản
ở gốc dự án (**đừng sửa tay bản trong `res/`**), chuẩn hoá `launch.sh` sang LF và kiểm `bin/lm-home` có mặt.

## Runtime Python

Engine cần CPython 3.12 ARM64. Runtime (~30 MB) **không nằm trong git**: đặt file nén ở `pak/loongmate-runtime.tar.gz`
(gốc tar là `loongmate-runtime/python/`) hoặc trỏ `LOONGMATE_RUNTIME_TAR` tới nó. `launch.sh` tự giải nén vào
`/userdata/loongmate-runtime/` khi mở app lần đầu, và giải nén lại nếu runtime bị xoá (ví dụ sau khi cập nhật Leaf).

## Khi mở app

`launch.sh` nạp `env.sh` của Leaf (đường dẫn thẻ, userdata), chuẩn bị runtime, chạy `bin/lm-home`; nếu binary không chạy
được thì rơi về giao diện Python. Dữ liệu của app nằm ở `.userdata/mlp1/LoongMate/`; app không sửa file nào của Leaf.

## Cài lên máy

- Người dùng: chép nội dung thư mục `THE-NHO` của gói phát hành vào gốc thẻ Leaf (xem [INSTALL.md](../INSTALL.md)).
- Phát triển: `python pc/deploy.py --key ~/.ssh/<khoá> --host <IP>` đẩy qua SSH, kiểm md5 từng file, giữ bản cũ để
  quay lui.
