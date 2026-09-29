"""Check that Ctrl+C through the Windows launcher reaches the program.

The launcher must ignore Ctrl+C itself, let the program it started see it,
and return that program's exit code (130 for a cancelled command) without
leaving it orphaned. A control run starts the program directly: when that
one does not react either, the event never arrived and nothing is proven.

Ctrl+C goes to every process on a console, so this gives itself a fresh
console, starts the target on it, stops listening itself, and sends the
event to the whole console.

Usage (Windows only): python ctrl-c-check.py <launcher exe> <program exe>
Exit 0 passed, 1 failed, 2 could not tell (the control run never saw the event).
The environment decides which install the launcher finds (AI_CONFIG_SHARE_DIR).
"""

import ctypes
import subprocess
import sys
import time

CTRL_C_EVENT = 0


def cancel(executable: str, kernel) -> "tuple[int | None, str, int]":
    # 先讓自己處理 Ctrl+C,子行程才會繼承「處理」而不是「忽略」
    kernel.SetConsoleCtrlHandler(None, False)
    with open("CONIN$") as console_in:
        process = subprocess.Popen(
            # __channel 一定停在讀輸入;setup 在已設定的環境可能直接結束
            [executable, "__channel"], stdin=console_in,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        time.sleep(6)  # 等它啟動並停在讀輸入
        kernel.SetConsoleCtrlHandler(None, True)
        kernel.GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)
        try:
            output, _ = process.communicate(timeout=20)
            code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            code = None
    return code, output.decode("utf-8", "replace"), process.pid


def children_of(pid: int) -> list[int]:
    listing = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}').ProcessId"],
        capture_output=True, text=True, check=False,
    )
    return [int(line) for line in listing.stdout.split() if line.isdigit()]


def main() -> int:
    launcher, program = sys.argv[1], sys.argv[2]
    kernel = ctypes.windll.kernel32
    kernel.FreeConsole()
    kernel.AllocConsole()

    control, control_output, _ = cancel(program, kernel)
    print(f"control (no launcher): exit {control}", flush=True)
    if control != 130:
        print(control_output)
        print("SKIP the event did not reach the program; this check proves nothing here")
        return 2

    code, output, pid = cancel(launcher, kernel)
    orphans = children_of(pid)
    print(f"through the launcher: exit {code}, left running: {orphans or 'none'}", flush=True)
    if code != 130 or "Cancelled" not in output or orphans:
        print(output)
        print("FAIL Ctrl+C through the launcher")
        return 1
    print("PASS Ctrl+C reaches the program and 130 comes back through the launcher")
    return 0


if __name__ == "__main__":
    sys.exit(main())
