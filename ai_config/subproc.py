"""How to decode what a child process prints.

`text=True` on its own decodes with the locale codepage: cp950 on the
Windows machine. A child printing UTF-8 Chinese then raised
UnicodeDecodeError inside subprocess's reader thread, where nothing
caught it. Every call names one of these instead, and replaces what it
cannot decode rather than dying on it.
"""

import locale

# git、gh、Node 寫的 CLI 與 Python 子程序都輸出 UTF-8,跟系統碼頁無關
UTF8 = {"encoding": "utf-8", "errors": "replace"}
# schtasks、powershell 這類 Windows 原生工具照系統碼頁輸出
NATIVE = {"encoding": locale.getpreferredencoding(False), "errors": "replace"}
