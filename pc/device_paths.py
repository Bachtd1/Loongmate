#!/usr/bin/env python3
"""LoongMate - dò thẻ nhớ chứa Leaf trên Pocket 1 (một nguồn sự thật cho mọi script PC).

Vì sao cần: Pocket 1 có hai khe thẻ, và sau khi khởi động lại hai thẻ có thể ĐỔI VỊ TRÍ mount
(2026-10-03: thẻ Leaf 30 GB từ /mnt/sdcard sang /media/sdcard1, thẻ 64 GB sang /mnt/sdcard).
Ghi cứng "/mnt/sdcard" thì script PC kéo/cài nhầm thẻ hoặc không thấy gì.

Cách làm (giống LoongMate trên máy: pak/Apps/mlp1/LoongMate.pak/launch.sh): thẻ Leaf là thẻ có
`.system/leaf/platforms/mlp1/launcher/env.sh`; nạp chính env.sh đó trong một subshell để lấy
SDCARD_PATH và USERDATA_PATH. Lưu ý: env.sh chỉ "điền chỗ trống" và mặc định SDCARD_PATH=/mnt/sdcard
(env.sh dòng `_umrk_default_sd="/mnt/sdcard"`), nên PHẢI gán SDCARD_PATH=<thẻ đang xét> trước khi
nạp, nếu không thẻ ở /media/sdcard1 cũng bị báo là /mnt/sdcard.

An toàn: danh sách ứng viên là hằng cố định (hoặc do người dùng truyền qua --sdcard), script gửi
sang máy là chuỗi dựng sẵn từ danh sách đó; không có dữ liệu nào đọc từ máy được ghép ngược vào
lệnh, trừ kết quả cuối cùng - bị kiểm bằng regex đường dẫn tuyệt đối "sạch" trước khi dùng.

Không cache qua các lần chạy (thẻ có thể đổi chỗ mỗi lần boot): chỉ cache trên đối tượng transport
trong một lần chạy.
"""
import posixpath
import re
from collections import namedtuple

CANDIDATES = ("/mnt/sdcard", "/media/sdcard1", "/media/sdcard2", "/media/sdcard3")
ENV_REL = ".system/leaf/platforms/mlp1/launcher/env.sh"
_SAFE = re.compile(r"^/[A-Za-z0-9_./-]+$")


class LeafCardError(Exception):
    """Không dò được thẻ Leaf một cách chắc chắn."""


class LeafCardNotFound(LeafCardError):
    pass


class LeafCardAmbiguous(LeafCardError):
    pass


class LeafPaths(namedtuple("LeafPaths", "sdcard userdata")):
    @property
    def vault(self):
        return posixpath.join(self.userdata, "LoongMate", "SaveVault")

    @property
    def app_data(self):
        return posixpath.join(self.userdata, "LoongMate")

    @property
    def apps_dir(self):
        return posixpath.join(self.sdcard, "Apps", "mlp1")

    @property
    def remote_dir(self):
        return posixpath.join(self.sdcard, "LoongMate-update")


def build_script(candidates=CANDIDATES):
    """Script sh chạy trên máy. In mỗi thẻ Leaf thấy được một dòng: LEAF<TAB>SDCARD_PATH<TAB>USERDATA_PATH.
    Bỏ thẻ trùng (symlink như /media/sdcard0 -> /mnt/sdcard) bằng đường dẫn thật `pwd -P`."""
    for c in candidates:
        if not _SAFE.match(c):
            raise ValueError(f"đường dẫn ứng viên không hợp lệ: {c!r}")
    cand = " ".join(f"'{c}'" for c in candidates)
    return (
        "seen=' '; "
        f"for c in {cand}; do "
        "real=$(cd \"$c\" 2>/dev/null && pwd -P) || continue; "
        "case \"$seen\" in *\" $real \"*) continue;; esac; seen=\"$seen$real \"; "
        f"e=\"$real/{ENV_REL}\"; "
        "[ -f \"$e\" ] || continue; "
        "( SDCARD_PATH=\"$real\"; export SDCARD_PATH; unset USERDATA_PATH; . \"$e\" >/dev/null 2>&1; "
        "printf 'LEAF\\t%s\\t%s\\n' \"$SDCARD_PATH\" \"$USERDATA_PATH\" ); "
        "done; true"
    )


def parse(output, candidates=CANDIDATES):
    found = []
    for line in output.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] != "LEAF":
            continue
        sd, ud = parts[1].rstrip("/"), parts[2].rstrip("/")
        if not (_SAFE.match(sd) and _SAFE.match(ud)):
            raise LeafCardError(f"env.sh của Leaf trả đường dẫn lạ, không dám dùng: {sd!r} / {ud!r}")
        if not (ud == sd or ud.startswith(sd + "/")):
            raise LeafCardError(f"USERDATA_PATH ({ud}) không nằm trong SDCARD_PATH ({sd}); không dám đoán")
        found.append(LeafPaths(sd, ud))
    if not found:
        raise LeafCardNotFound(
            "Không thấy thẻ nào chứa Leaf trên máy (tìm '" + ENV_REL + "' trong: " + ", ".join(candidates)
            + "). Máy chưa cài Leaf, thẻ chưa mount, hoặc chỉ định tay bằng --device-vault/--apps-dir/... "
              "(hoặc --sdcard <thư mục thẻ>).")
    if len(found) > 1:
        raise LeafCardAmbiguous(
            "Thấy nhiều hơn một thẻ có Leaf: " + ", ".join(p.sdcard for p in found)
            + ". Không đoán thẻ nào; chỉ định bằng --sdcard <thư mục thẻ> (hoặc các cờ đường dẫn riêng).")
    return found[0]


def detect(transport, candidates=CANDIDATES):
    """Hỏi máy qua transport._run (argv ssh cố định). Ném LeafCardError / Unreachable / TransportError."""
    return parse(transport._run(build_script(candidates), timeout=60), candidates)


def get(transport, sdcard=None):
    """Như detect nhưng cache trên transport trong MỘT lần chạy. sdcard: ép chỉ xét thẻ đó."""
    key = sdcard or ""
    cache = transport.__dict__.setdefault("_leaf_paths_cache", {})
    if key not in cache:
        cache[key] = detect(transport, (sdcard,) if sdcard else CANDIDATES)
    return cache[key]
