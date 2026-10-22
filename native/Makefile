# Chạy BÊN TRONG container (xem build.sh). Biên dịch chéo ARM64 với -DPLATFORM_MLP1.
CC      := aarch64-linux-gnu-gcc
OBJDUMP := aarch64-linux-gnu-objdump
SYSLIB  := /sysroot/lib
TP      := third_party
INC     := -I$(TP)/catastrophe/include -I$(TP)/sdl-headers
CFLAGS  := -O2 -std=gnu11 -DPLATFORM_MLP1 $(INC) -Wall -Wextra -Wno-unused-parameter -Wno-format-truncation
# cJSON là mã bên thứ ba, tắt cảnh báo cho nó
VFLAGS  := -O2 -std=gnu11 -w -I$(TP)/catastrophe/include

# Link thẳng với libc/libm/ld.so CỦA MÁY (glibc 2.38) thay vì libc của toolchain (2.35): libSDL2
# của máy dùng symbol GLIBC_2.38 (strlcpy, __isoc23_*...), link với libc 2.35 sẽ báo undefined.
# -nodefaultlibs bỏ libc mặc định; libc_nonshared.a (atexit...) và libgcc lấy từ toolchain.
# ld-linux-aarch64.so.1 phải có trên dòng lệnh vì gcc Ubuntu bật stack-protector (__stack_chk_guard).
# --allow-shlib-undefined: libSDL2_ttf cần libharfbuzz -> libglib... (không kéo về sysroot);
# symbol của chúng do máy giải lúc chạy.
# KHÔNG đặt -rpath: máy tìm thư viện ở /usr/lib theo đường mặc định của ld.so.
LIBC_NS := /usr/aarch64-linux-gnu/lib/libc_nonshared.a
LDFLAGS := -nodefaultlibs -L$(SYSLIB) -Wl,-rpath-link,$(SYSLIB) -Wl,--as-needed -Wl,--allow-shlib-undefined \
           -lSDL2_ttf -lSDL2_image -lSDL2 \
           $(SYSLIB)/libm.so.6 $(SYSLIB)/libpthread.so.0 $(SYSLIB)/libdl.so.2 \
           $(SYSLIB)/libc.so.6 $(SYSLIB)/ld-linux-aarch64.so.1 $(LIBC_NS) -lgcc
B       := build

COMMON  := $(B)/cat_impl.o $(B)/lm_common.o $(B)/cJSON.o
.PHONY: all clean check
all: $(B)/lm-home $(B)/lm-hello check

HOME_OBJS := $(B)/lm_home.o $(B)/lm_ui.o $(B)/lm_cli.o $(B)/lm_library.o $(B)/lm_roms.o $(B)/lm_import.o $(B)/lm_lib.o $(B)/lm_saves.o $(B)/lm_misc.o $(B)/lm_update.o $(B)/lm_hottabs.o $(B)/lm_lock.o $(B)/lm_onboard.o $(B)/lm_leafcfg.o $(B)/lm_osk.o $(B)/lm_thumb.o $(B)/lm_web.o $(B)/lm_patch.o $(B)/lm_run.o $(B)/lm_i18n.o

$(B)/lm-home: $(HOME_OBJS) $(COMMON)
	$(CC) -o $@ $^ $(LDFLAGS)
$(B)/lm-hello: $(B)/lm_hello.o $(COMMON)
	$(CC) -o $@ $^ $(LDFLAGS)

$(B)/%.o: src/%.c | $(B)
	$(CC) $(CFLAGS) -c -o $@ $<
$(B)/cJSON.o: $(TP)/catastrophe/include/cjson/cJSON.c | $(B)
	$(CC) $(VFLAGS) -c -o $@ $<
$(HOME_OBJS) $(B)/lm_hello.o $(B)/lm_common.o $(B)/cat_impl.o: $(wildcard src/*.h) $(TP)/catastrophe/include/catastrophe.h
$(B):
	mkdir -p $(B)

# Phụ thuộc NEEDED + phiên bản glibc cao nhất mà binary đòi (phải <= 2.38).
check: $(B)/lm-home $(B)/lm-hello
	@for f in $(B)/lm-home $(B)/lm-hello; do \
	  echo "== $$f"; file $$f; \
	  $(OBJDUMP) -p $$f | grep -E 'NEEDED|required from' ; \
	  echo "glibc cao nhất: $$($(OBJDUMP) -T $$f | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)"; \
	done

clean:
	rm -rf $(B)
