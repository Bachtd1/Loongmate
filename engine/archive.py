"""Giải nén archive ngoài .zip cho Nhập game: .7z, .rar (RAR4/RAR5), .tar, .tar.gz/.tgz, .tar.bz2, .tar.xz, .gz, .bz2, .xz, .zst,
.cab, .lzh, .arj. .zip vẫn do importer đọc trực tiếp (không cần giải nén trước).

Luồng (quét Import/ vẫn CHỈ ĐỌC): importer hiện archive là "cần giải nén"; người dùng bấm GIẢI NÉN ->
  1. liệt kê nội dung (không ghi gì) và kiểm như zip: đường dẫn ('..', tuyệt đối, ổ đĩa), độ sâu, số mục, symlink/hardlink/
     thiết bị, mật khẩu, kích thước từng file và tổng, tỉ lệ nén (zip bomb);
  2. kiểm chỗ trống trên thẻ chứa archive;
  3. giải vào thư mục tạm <thẻ>/.loongmate-import/.unpack-*/ trên CÙNG thẻ, rồi kiểm lại cây đã giải (không symlink, tổng
     dung lượng/số file trong giới hạn) — lệch thì xoá thư mục tạm, không để lại gì;
  4. đổi tên nguyên tử thành Import/<tên archive>/ (không ghi đè: trùng thì thêm " (2)"), chuyển archive (và các phần nếu
     là archive nhiều phần) vào Import/.done/.
Sau đó các file giải ra hiện như mục thường trong Nhập game (kiểm file chạy được, BIOS... như mọi nguồn khác).

tar và nén đơn (.gz/.bz2/.xz): thư viện chuẩn Python. Định dạng còn lại: 7-Zip chính thức (7zzs, bản tĩnh ARM64) đi kèm
trong <pak>/bin/7zzs (pak/fetch-7zip.sh tải bản ghim theo SHA-256), gọi bằng subprocess với danh sách tham số (không shell),
luôn truyền -p (rỗng nếu chưa có mật khẩu) để không bao giờ chờ nhập. Không có 7zzs (chạy trên PC) thì dùng 7zz/7z trong
PATH nếu có, không thì báo.

Archive có mật khẩu (.7z/.rar): KHÔNG dò mật khẩu (không brute force). Chỉ thử một danh sách ngắn (tối đa MAX_CANDIDATES)
lấy từ chính người đăng/người dùng: chú thích (comment) của archive, tên file ("[pass xyz]"), file .txt/.nfo cạnh archive
(dòng "pass: ...", "mật khẩu: ..." hoặc mọi dòng của file tên password*.txt / pass*.txt / mk*.txt), Import/passwords.txt
(người dùng tự ghi, mỗi dòng một mật khẩu) và các mật khẩu đã giải thành công trước đó (userdata). Mỗi lần thử chỉ đọc
danh sách (archive mã hoá cả tên file) hoặc kiểm một file nhỏ (`7zz t`), không ghi gì. Không ra thì trả danh sách ứng viên
để người dùng chọn hoặc gõ."""
import bz2
import gzip
import lzma
import os
import re
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

MB = 1024 * 1024
TAR_EXTS = {".tar", ".tgz", ".tbz2", ".tbz", ".txz"}
SINGLE = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}
SEVEN_EXTS = {".7z", ".rar", ".zst", ".cab", ".lzh", ".arj"}
UNPACK_EXTS = TAR_EXTS | set(SINGLE) | SEVEN_EXTS
# Phần thứ 2 trở đi của archive nhiều phần: không hiện riêng, đi kèm phần đầu.
_VOLUME_REST = re.compile(r"(\.part(?!0*1\.rar$)\d+\.rar|\.r\d{2}|\.7z\.(?!0*1$)\d+|\.z\d{2})$", re.I)
_VOLUME_FIRST = re.compile(r"(\.part0*1\.rar|\.7z\.0*1)$", re.I)
UNPACK_DIR = ".loongmate-import"
PASSWORD_LIST = "passwords.txt"          # trong Import/ của thẻ: mỗi dòng một mật khẩu, người dùng tự ghi
REMEMBERED = "archive-passwords.txt"     # trong userdata: mật khẩu đã giải thành công (dùng lại cho archive cùng người đăng)
MAX_CANDIDATES = 30
_PW_LINE = re.compile(r"(?i)(?:\bpass(?:word)?|\bpwd?|mật\s*khẩu|mat\s*khau|\bmk|\bunzip|\bunrar)\s*(?:là|is)?\s*[:=：]\s*(.+)")
_PW_NAME = re.compile(r"(?i)[\[(\{](?:pass(?:word)?|pwd?|mk)[\s=:_-]+([^\])\}]+)[\])\}]")
_PW_FILE = re.compile(r"(?i)^(pass|password|pwd|mk|mat\s*khau|mật\s*khẩu)\b.*\.txt$")


def is_volume_rest(name: str) -> bool:
    return bool(_VOLUME_REST.search(name))


def _ext(name: str) -> str:
    n = name.lower()
    if _VOLUME_FIRST.search(n):
        return ".rar" if n.endswith(".rar") else ".7z"
    i = n.rfind(".")
    return n[i:] if 0 < i < len(n) - 1 else ""


def is_tar_name(name: str) -> bool:
    n = name.lower()
    return _ext(n) in TAR_EXTS or bool(re.search(r"\.tar\.(gz|bz2|xz|zst)$", n))


def can_unpack_name(name: str) -> bool:
    return _ext(name) in UNPACK_EXTS


def seven_zip() -> Optional[str]:
    """7zzs đi kèm pak, hoặc LOONGMATE_7Z, hoặc 7zz/7z trong PATH (chạy trên PC)."""
    env = os.environ.get("LOONGMATE_7Z")
    if env and Path(env).is_file():
        return env
    bundled = Path(__file__).resolve().parents[1] / "bin" / "7zzs"
    if bundled.is_file():
        return str(bundled)
    for n in ("7zzs", "7zz", "7z"):
        w = shutil.which(n)
        if w:
            return w
    return None


def download_problem(name: str) -> Optional[str]:
    """File sắp tải (Tải từ web, Thư viện riêng): archive giải được trên máy -> None (tải xong tự giải nén rồi cài); thiếu 7-Zip -> lý do.
    Không phải archive cần giải (ROM, .zip) -> None: importer lo như cũ."""
    if not can_unpack_name(name):
        return None
    ok, why = supported(name)
    return None if ok else why


def needs_seven(name: str) -> bool:
    return not is_tar_name(name) and (_ext(name) in SEVEN_EXTS or _ext(name) == ".zip")


def supported(name: str) -> Tuple[bool, str]:
    """(giải được trên máy này không, lý do nếu không). .zip chỉ qua đây khi có mật khẩu (zip thường importer đọc thẳng)."""
    if not (can_unpack_name(name) or _ext(name) == ".zip"):
        return False, "định dạng chưa hỗ trợ"
    if needs_seven(name) and not seven_zip():
        return False, "thiếu 7-Zip (bin/7zzs) trong LoongMate.pak — cài lại bản phát hành"
    return True, ""


def volume_parts(path: Path) -> List[Path]:
    """Mọi phần của archive nhiều phần (gồm chính nó), để chuyển vào .done cùng nhau."""
    n = path.name
    m = re.match(r"(?i)(.*)\.part0*1\.rar$", n)
    if m:
        pat = re.compile(r"(?i)^" + re.escape(m.group(1)) + r"\.part\d+\.rar$")
    elif re.search(r"(?i)\.7z\.0*1$", n):
        pat = re.compile(r"(?i)^" + re.escape(n[: n.lower().rfind(".7z.") + 3]) + r"\.\d{3}$")
    elif n.lower().endswith(".rar"):
        pat = re.compile(r"(?i)^" + re.escape(n[:-4]) + r"\.(rar|r\d{2})$")
    else:
        return [path]
    try:
        parts = sorted(p for p in path.parent.iterdir() if p.is_file() and pat.match(p.name))
    except OSError:
        parts = []
    return parts or [path]


# ------------------------------------------------------------------ liệt kê (chỉ đọc)
def _list_tar(path: Path) -> List[Dict[str, Any]]:
    out = []
    with tarfile.open(path, "r:*") as t:
        for m in t:
            out.append({"path": m.name, "size": m.size if m.isfile() else 0, "dir": m.isdir(),
                        "link": m.issym() or m.islnk(), "special": not (m.isfile() or m.isdir() or m.issym() or m.islnk()),
                        "encrypted": False})
    return out


def _single_name(path: Path) -> str:
    n = path.name
    return n[: n.lower().rfind(".")] or "file"


def _single_size(path: Path) -> Optional[int]:
    if _ext(path.name) == ".gz":                     # ISIZE: 4 byte cuối = kích thước gốc mod 2^32
        try:
            with open(path, "rb") as f:
                f.seek(-4, os.SEEK_END)
                return int.from_bytes(f.read(4), "little")
        except OSError:
            return None
    return None


def parse_7z_listing(text: str) -> List[Dict[str, Any]]:
    """Đầu ra `7zz l -slt -ba`: các khối "Khoá = giá trị" cách nhau bằng dòng trống."""
    out, cur = [], {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if "Path" in cur:
                attr = cur.get("Attributes", "")
                mode = attr.split()[-1] if attr.split() else ""
                out.append({"path": cur["Path"], "size": int(cur.get("Size") or 0) if (cur.get("Size") or "").isdigit() else 0,
                            "dir": cur.get("Folder") == "+" or attr.startswith("D"),
                            "link": mode.startswith("l") or cur.get("Symbolic Link", "") not in ("", "-") or
                                    cur.get("Hard Link", "") not in ("", "-"),
                            "special": bool(mode) and mode[0] in "bcps",
                            "encrypted": cur.get("Encrypted") == "+", "block": cur.get("Block")})
            cur = {}
            continue
        k, sep, v = line.partition(" = ")
        if sep:
            cur[k.strip()] = v
    return out


def _list_seven(path: Path, exe: str, password: str = "") -> List[Dict[str, Any]]:
    r = subprocess.run([exe, "l", "-slt", "-ba", "-p" + password, "--", str(path)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        if re.search(r"(?i)wrong password|encrypted", err):
            raise NeedsPassword("sai mật khẩu" if password else "archive có mật khẩu (mã hoá cả tên file)")
        raise ArchiveError("7-Zip không đọc được archive: " + (err.strip().splitlines() or ["lỗi không rõ"])[-1][:200])
    return parse_7z_listing(r.stdout.decode("utf-8", "replace"))


class ArchiveError(Exception):
    pass


class NeedsPassword(ArchiveError):
    pass


# ------------------------------------------------------------------ mật khẩu (chỉ danh sách ứng viên, không dò)
def _clean_pw(s: str) -> str:
    s = s.strip().strip("\"'“”‘’`«»").strip()
    return s if 0 < len(s) <= 128 and "\n" not in s and "\x00" not in s else ""


def _pw_from_text(text: str, whole_lines: bool = False) -> List[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _PW_LINE.search(line)
        if m:
            out.append(_clean_pw(m.group(1)))
        elif whole_lines and len(line) <= 128 and not line.startswith("#"):
            out.append(_clean_pw(line))
    return [x for x in out if x]


def archive_comment(path: Path) -> str:
    """Chú thích của archive (RAR/ZIP hay ghi mật khẩu/trang web ở đây). Rỗng nếu không có hoặc không đọc được."""
    exe = seven_zip()
    if not exe or not needs_seven(path.name):
        return ""
    try:
        r = subprocess.run([exe, "l", "-slt", "-p", "--", str(path)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    head = r.stdout.decode("utf-8", "replace").split("\n----------", 1)[0]
    lines, grab = [], False
    for line in head.splitlines():
        if line.startswith("Comment = "):
            grab = True
            lines.append(line[len("Comment = "):])
        elif grab and " = " not in line:
            lines.append(line)
        else:
            grab = False
    return "\n".join(lines).strip()


def password_candidates(path, remember_file=None, comment: Optional[str] = None) -> List[Dict[str, str]]:
    """[{password, source}] theo thứ tự nên thử, không trùng, tối đa MAX_CANDIDATES."""
    path = Path(path)
    found: List[Tuple[str, str]] = []
    if comment is None:
        comment = archive_comment(path)
    if comment:
        got = _pw_from_text(comment)
        if not got and "\n" not in comment and " " not in comment.strip():
            got = [_clean_pw(comment)]                       # chú thích chỉ một từ (thường là chính mật khẩu/trang web)
        found += [(x, "chú thích archive") for x in got if x]
    found += [(_clean_pw(m.group(1)), "tên file") for m in _PW_NAME.finditer(path.name)]
    try:
        sides = sorted(p for p in path.parent.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".nfo")
                       and p.name.lower() != PASSWORD_LIST)[:20]
    except OSError:
        sides = []
    for p in sides:
        try:
            if p.stat().st_size > 64 * 1024:
                continue
            text = p.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        found += [(x, p.name) for x in _pw_from_text(text, whole_lines=bool(_PW_FILE.match(p.name)))]
    lists = [(_card_root(path) / "Import" / PASSWORD_LIST, PASSWORD_LIST), (path.parent / PASSWORD_LIST, PASSWORD_LIST)]
    if remember_file:
        lists.append((Path(remember_file), "đã dùng trước đây"))
    for f, src in lists:
        try:
            if f.is_file() and f.stat().st_size <= 64 * 1024:
                found += [(_clean_pw(x), src) for x in f.read_bytes().decode("utf-8", "replace").splitlines()
                          if x.strip() and not x.lstrip().startswith("#")]
        except OSError:
            pass
    out, seen = [], set()
    for pw, src in found:
        if pw and pw not in seen:
            seen.add(pw)
            out.append({"password": pw, "source": src})
    return out[:MAX_CANDIDATES]


def _password_ok(path: Path, exe: str, password: str, entries: List[Dict[str, Any]]) -> bool:
    """Kiểm một file mã hoá (không ghi gì). 7z đặc (solid): file đầu khối; còn lại: file nhỏ nhất."""
    enc = [e for e in entries if e["encrypted"] and not e["dir"]]
    if not enc:
        return True
    pick = enc[0] if enc[0].get("block") is not None else min(enc, key=lambda e: e["size"])
    try:
        r = subprocess.run([exe, "t", "-bso0", "-bsp0", "-bse0", "-spd", "-p" + password, "--", str(path), pick["path"]],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _open_with_password(path: Path, password: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Danh sách mục nếu mật khẩu đúng, None nếu sai."""
    exe = seven_zip()
    try:
        entries = _list_seven(path, exe, password or "")
    except NeedsPassword:
        return None
    return entries if _password_ok(path, exe, password or "", entries) else None


def remember_password(remember_file, password: str) -> None:
    if not remember_file or not password:
        return
    f = Path(remember_file)
    try:
        old = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
        if password in old:
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(password + "\n")
        os.chmod(f, 0o600)
    except OSError:
        pass


def list_archive(path, password: str = "") -> Dict[str, Any]:
    """{"format", "entries": [{path,size,dir,link,special,encrypted}], "total": byte | None nếu chưa biết}."""
    path = Path(path)
    ok, why = supported(path.name)
    if not ok:
        raise ArchiveError(why)
    if is_tar_name(path.name):
        try:
            entries = _list_tar(path)
        except (tarfile.TarError, OSError, EOFError, lzma.LZMAError, ValueError) as e:
            raise ArchiveError(f"không đọc được tar: {e}")
        fmt = "tar"
    elif _ext(path.name) in SINGLE:
        entries = [{"path": _single_name(path), "size": _single_size(path) or 0, "dir": False, "link": False,
                    "special": False, "encrypted": False}]
        fmt = _ext(path.name)[1:]
    else:
        entries = _list_seven(path, seven_zip(), password)
        fmt = _ext(path.name)[1:]
    files = [e for e in entries if not e["dir"]]
    total = None if fmt in SINGLE and fmt != "gz" else sum(e["size"] for e in files)
    return {"format": fmt, "entries": entries, "files": len(files), "total": total}


def check_listing(listing: Dict[str, Any], archive_size: int, limits) -> List[str]:
    """Cùng luật với zip của importer. Trả danh sách vấn đề (rỗng = giải được)."""
    from engine import importer
    probs: List[str] = []
    entries = listing["entries"]
    if len(entries) > limits.max_entries:
        probs.append(f"quá nhiều mục ({len(entries)} > {limits.max_entries})")
    for e in entries:
        parts, p = importer._split_member(e["path"])
        if e["link"]:
            p.append("symlink/hardlink trong archive")
        if e["special"]:
            p.append("loại file đặc biệt (device/fifo/socket)")
        if e["encrypted"]:
            p.append("có mật khẩu")
        if not e["dir"]:
            if not parts:
                p.append("tên rỗng")
            p += importer._check_member_name(parts, limits)
            if e["size"] > limits.max_file_bytes:
                p.append(f"file quá lớn ({e['size'] // MB} MB > {limits.max_file_bytes // MB} MB)")
        probs += [f"'{e['path']}': {x}" for x in p]
    total = listing.get("total")
    if total is not None:
        if total > limits.max_total_bytes:
            probs.append(f"tổng giải nén {total // MB} MB > {limits.max_total_bytes // MB} MB")
        if total >= limits.ratio_min_bytes and archive_size and total / archive_size > limits.max_ratio:
            probs.append(f"tỉ lệ nén bất thường (> {limits.max_ratio}:1) — nghi zip bomb")
    return probs


# ------------------------------------------------------------------ giải nén
def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.name} ({i})")
        if not q.exists():
            return q
    raise ArchiveError(f"không tìm được tên trống cho {p.name}")


def _card_root(path: Path) -> Path:
    """Thẻ chứa archive = cha của thư mục Import/ (archive luôn nằm trong <thẻ>/Import/)."""
    for parent in path.parents:
        if parent.name.lower() == "import":
            return parent.parent
    return path.parent


def _extract_tar(path: Path, staging: Path, limits) -> None:
    """Tự ghi từng file thường (không dùng tarfile.extract): không bao giờ tạo link/thiết bị, đường dẫn đã kiểm."""
    from engine import importer
    with tarfile.open(path, "r:*") as t:
        written = 0
        for m in t:
            parts, p = importer._split_member(m.name)
            if p or not parts:
                raise ArchiveError(f"đường dẫn không hợp lệ: {m.name}")
            dest = staging.joinpath(*[importer._fat_clean(x)[0] for x in parts])   # thẻ FAT32: bỏ ký tự cấm
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            if not m.isfile():
                raise ArchiveError(f"mục không phải file thường: {m.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = t.extractfile(m)
            with open(dest, "xb") as f:
                while True:
                    b = src.read(1024 * 1024)
                    if not b:
                        break
                    written += len(b)
                    if written > limits.max_total_bytes:
                        raise ArchiveError("vượt giới hạn tổng dung lượng khi giải nén")
                    f.write(b)


def _extract_single(path: Path, staging: Path, limits) -> None:
    opener = SINGLE[_ext(path.name)]
    dest = staging / _single_name(path)
    written = 0
    with opener(path, "rb") as src, open(dest, "xb") as f:
        while True:
            b = src.read(1024 * 1024)
            if not b:
                break
            written += len(b)
            if written > min(limits.max_file_bytes, limits.max_total_bytes):
                raise ArchiveError("file giải ra vượt giới hạn dung lượng")
            f.write(b)


def _extract_seven(path: Path, staging: Path, progress: Optional[Callable[[int], None]], password: str = "") -> None:
    exe = seven_zip()
    cmd = [exe, "x", "-y", "-bd" if progress is None else "-bsp2", "-bso0", "-p" + password, "-o" + str(staging), "--", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    err = b""
    last = -1
    while True:
        chunk = p.stderr.read1(4096) if hasattr(p.stderr, "read1") else p.stderr.read(4096)
        if not chunk:
            break
        err += chunk[-2000:] if len(err) > 4000 else chunk
        if progress:
            for m in re.finditer(rb"(\d{1,3})%", chunk):
                pct = int(m.group(1))
                if pct != last:
                    last = pct
                    progress(pct)
    rc = p.wait()
    if rc != 0:
        msg = err.decode("utf-8", "replace")
        if re.search(r"(?i)wrong password|encrypted", msg):
            raise NeedsPassword("sai mật khẩu" if password else "archive có mật khẩu")
        raise ArchiveError("7-Zip báo lỗi khi giải nén: " + (msg.strip().splitlines() or [f"mã {rc}"])[-1][:200])


def _verify_tree(staging: Path, limits) -> Tuple[int, int]:
    """Cây đã giải: chỉ file thường + thư mục, trong giới hạn. Trả (số file, tổng byte)."""
    n = total = 0
    for root, dirs, files in os.walk(staging):
        for name in dirs + files:
            st = os.lstat(os.path.join(root, name))
            if stat.S_ISLNK(st.st_mode):
                raise ArchiveError(f"archive tạo symlink: {name}")
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise ArchiveError(f"archive tạo file đặc biệt: {name}")
            if stat.S_ISREG(st.st_mode):
                n += 1
                total += st.st_size
                if st.st_size > limits.max_file_bytes:
                    raise ArchiveError(f"file giải ra quá lớn: {name}")
    if n > limits.max_entries:
        raise ArchiveError(f"giải ra quá nhiều file ({n})")
    if total > limits.max_total_bytes:
        raise ArchiveError(f"tổng giải ra {total // MB} MB vượt giới hạn")
    return n, total


def unpack(path, limits=None, progress: Optional[Callable[[int], None]] = None, password: Optional[str] = None,
           auto_password: bool = False, remember_file=None, out_dir=None) -> Dict[str, Any]:
    """Giải archive trong Import/ thành Import/<tên>/. Không ghi đè, không để lại rác khi lỗi.
    Có mật khẩu: password= dùng đúng mật khẩu đó; auto_password=True thử lần lượt password_candidates(). Không đúng/không
    có thì trả {"needs_password": True, "candidates": [...]} để giao diện cho chọn hoặc gõ. Mật khẩu đúng được nhớ vào
    remember_file (nếu có) để lần sau thử trước.
    out_dir: giải vào out_dir/<tên>/ (cùng thẻ, vd thư mục job tải về) và KHÔNG chuyển archive vào .done (người gọi lo)."""
    from engine import importer
    limits = limits or importer.DEFAULT_LIMITS
    path = Path(path)
    if not path.is_file():
        return {"success": False, "message": f"không thấy {path}"}
    used_pw, pw_source = None, None
    try:
        try:
            listing = list_archive(path)
            locked = any(e["encrypted"] for e in listing["entries"])
        except NeedsPassword:
            locked = True
        if locked:
            cands = password_candidates(path, remember_file)
            tries = [{"password": password, "source": "bạn nhập"}] if password else cands if auto_password else []
            for c in tries:
                if _open_with_password(path, c["password"]) is not None:
                    used_pw, pw_source = c["password"], c["source"]
                    break
            if used_pw is None:
                msg = ("sai mật khẩu" if password else
                       f"archive có mật khẩu; đã thử {len(tries)} mật khẩu gợi ý nhưng không đúng" if tries else
                       "archive có mật khẩu")
                return {"success": False, "needs_password": True, "wrong_password": bool(password), "tried": len(tries),
                        "candidates": cands, "message": msg + " — chọn hoặc nhập mật khẩu"}
            listing = list_archive(path, used_pw)
            for e in listing["entries"]:
                e["encrypted"] = False                       # đã kiểm mật khẩu đúng
        size = path.stat().st_size
        probs = check_listing(listing, size, limits)
        if probs:
            return {"success": False, "message": "không giải nén: " + "; ".join(probs[:5]), "problems": probs}
        card = Path(out_dir) if out_dir else _card_root(path)
        if out_dir:
            card.mkdir(parents=True, exist_ok=True)
        need = (listing["total"] if listing["total"] is not None else size * 8) + limits.reserve_bytes
        free = shutil.disk_usage(card).free
        if free < need:
            return {"success": False, "message": f"thẻ còn {free // MB} MB, cần ~{need // MB} MB để giải nén"}
        base = _single_name(path) if not is_tar_name(path.name) else re.sub(r"(?i)\.(tar\.(gz|bz2|xz|zst)|tgz|tbz2?|txz|tar)$", "", path.name)
        base = re.sub(r"(?i)\.part0*1$|\.7z$", "", base) or "archive"
        staging = (card if out_dir else card / UNPACK_DIR) / f".unpack-{os.getpid()}-{int(time.time() * 1000)}"
        staging.mkdir(parents=True)
    except ArchiveError as e:
        return {"success": False, "message": str(e)}
    except OSError as e:
        return {"success": False, "message": f"lỗi đọc/ghi: {e}"}
    try:
        if is_tar_name(path.name):
            _extract_tar(path, staging, limits)
        elif _ext(path.name) in SINGLE:
            _extract_single(path, staging, limits)
        else:
            _extract_seven(path, staging, progress, used_pw or "")
        files, total = _verify_tree(staging, limits)
        if not files:
            raise ArchiveError("archive không có file nào")
        dest = _unique((Path(out_dir) if out_dir else path.parent) / base)
        os.replace(staging, dest)
    except ArchiveError as e:
        shutil.rmtree(staging, ignore_errors=True)
        return {"success": False, "message": str(e)}
    except (OSError, tarfile.TarError, EOFError, lzma.LZMAError, ValueError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        return {"success": False, "message": f"lỗi khi giải nén: {e}"}
    pw_note = ""
    if used_pw is not None:
        remember_password(remember_file, used_pw)
        pw_note = f" Mật khẩu lấy từ: {pw_source}."
    if out_dir:
        return {"success": True, "dest": str(dest), "files": files, "bytes": total, "password_source": pw_source,
                "message": f"Đã giải nén {files} file.{pw_note}"}
    moved = []
    done = path.parent / importer.DONE_DIR
    try:
        done.mkdir(exist_ok=True)
        for part in volume_parts(path):
            target = _unique(done / part.name)
            os.replace(part, target)
            moved.append(part.name)
    except OSError as e:
        return {"success": True, "dest": str(dest), "files": files, "bytes": total, "password_source": pw_source,
                "message": f"Đã giải nén {files} file vào {dest.name}/ (không chuyển được archive gốc vào .done: {e}).{pw_note}"}
    return {"success": True, "dest": str(dest), "files": files, "bytes": total, "archive_moved": moved,
            "password_source": pw_source,
            "message": f"Đã giải nén {files} file vào Import/{dest.name}/; archive gốc chuyển vào Import/.done/.{pw_note}"}
