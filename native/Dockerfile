# Toolchain biên dịch chéo ARM64 cho LoongMate native (Pocket 1 / Leaf mlp1).
# Dựng lại được: docker build -t loongmate-native-build -f native/Dockerfile native
#
# Gốc: Ubuntu 22.04 + gcc-aarch64-linux-gnu (glibc 2.35 headers). Binary sinh ra chỉ tham chiếu
# symbol glibc <= 2.35, chạy được trên glibc 2.38 của máy. Thư viện để LINK (SDL2, SDL2_ttf,
# SDL2_image, freetype, libc, libm...) là bản copy TỪ CHÍNH MÁY (xem fetch-sysroot.sh),
# nên SONAME/phiên bản khớp máy. Không dùng image mlp1-toolchain của UMRK vì pull từ
# ghcr.io bị chặn/hết giờ trên mạng này (blob ở pkg-containers.githubusercontent.com).
FROM ubuntu:22.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gcc-aarch64-linux-gnu libc6-dev-arm64-cross make binutils-aarch64-linux-gnu file \
 && rm -rf /var/lib/apt/lists/*
COPY sysroot/device-libs.tar /tmp/device-libs.tar
# /sysroot/lib = nội dung /usr/lib của máy (chỉ các lib cần để link). Bỏ libgcc_s.so và
# libjpeg.so.8 lạ: không cần và dễ gây nhầm với bản của toolchain.
RUN mkdir -p /sysroot/lib && tar -xf /tmp/device-libs.tar -C /sysroot/lib && rm -f /tmp/device-libs.tar \
 && rm -f /sysroot/lib/libgcc_s.so
WORKDIR /work
