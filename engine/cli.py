"""
Lối vào của CLI LoongMate — mã thật nằm ở engine/cli_impl.py.

Vì sao tách: Python KHÔNG lưu bytecode cho file chạy trực tiếp (`python engine/cli.py`), nên một file
hơn 2000 dòng bị biên dịch lại ở MỖI lần gọi (~0,2 s trên máy; native gọi CLI cho mọi thao tác). File
này gần như rỗng nên biên dịch tức thì, còn cli_impl.py được nạp qua import và có __pycache__.

`import engine.cli` trả về chính module cli_impl (cùng đối tượng) nên test/mock.patch trên
`engine.cli` vẫn tác động đúng chỗ mà hàm tra tên toàn cục.
"""
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from engine.cli_impl import main
    sys.exit(main())
else:
    from engine import cli_impl
    sys.modules[__name__] = cli_impl
