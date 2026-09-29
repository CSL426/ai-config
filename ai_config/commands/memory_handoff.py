"""acg memory handoff: hand a work thread over, and the context reminder."""

import shlex
from pathlib import Path

from .. import memory_paths
from ..console import HELP_FLAGS, log_error, log_info, log_success, log_warn
from ..paths import ENTRYPOINT

_HANDOFF_USAGE = (
    f"Usage: {ENTRYPOINT} memory handoff "
    "[list [專案路徑] | write <線> <內容> | claim [線] | done <線> | "
    "remind [status|enable [百分比]|disable]]"
)
_REMIND_USAGE = (
    f"Usage: {ENTRYPOINT} memory handoff remind [status|enable [百分比]|disable]"
)


def _handoff(rest: list[str]) -> int:
    from .. import handoff as hand

    action = rest[0] if rest else "list"
    args = rest[1:]
    if action in HELP_FLAGS:
        log_info(_HANDOFF_USAGE)
        return 0
    try:
        if action == "remind":
            return _handoff_remind(args)
        if action == "list" and len(args) <= 1:
            return _handoff_list(Path(args[0]) if args else None)
        if action == "write" and len(args) >= 2:
            note = hand.write(args[0], " ".join(args[1:]))
            log_success(f"已記下交接:{note.thread}")
            # 接手的人用的是 /acg pickup 或一句「接著做」;這行會被 session
            # 照抄給使用者,只給指令會讓人以為得自己打。指令留著給沒有 plugin 的環境,
            # 名稱含空格時要引號,否則照著貼會被拆成多個參數
            log_info("接手:清空對話後用 /acg pickup,或直接說「接著做」")
            log_info(
                f"沒有 plugin 時:{ENTRYPOINT} memory handoff claim "
                f"{shlex.quote(note.name)}"
            )
            return 0
        if action == "claim" and len(args) <= 1:
            # 不帶名稱就接這個 session 名稱留下的那條線。接走就結案:
            # 這份筆記的任務是交到下一個人手上,收工時由接手的人再寫一份
            note = hand.claim(args[0] if args else "")
            log_success(f"已接手:{note.thread}(這則交接已結案,收工時再寫新的)")
            print()
            print(note.body)
            return 0
        if action == "done" and len(args) == 1:
            note = hand.done(args[0])
            log_success(f"已結束:{note.thread}")
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        log_error(str(exc))
        return 1
    log_error(_HANDOFF_USAGE)
    return 1


def _handoff_remind(args: list[str]) -> int:
    from .. import handoff_reminder as remind

    action = args[0] if args else "status"
    rest = args[1:]
    if action in HELP_FLAGS:
        log_info(_REMIND_USAGE)
        return 0
    if action == "status" and not rest:
        state = remind.status()
    elif action == "enable" and len(rest) <= 1:
        threshold = int(rest[0]) if rest else remind.DEFAULT_THRESHOLD
        state = remind.configure(True, threshold)
    elif action == "disable" and not rest:
        state = remind.configure(False)
    else:
        log_error(_REMIND_USAGE)
        return 1
    if state["installed"]:
        log_success(f"Claude 交接提醒已啟用，門檻 {state['threshold']}%")
    elif state["enabled"]:
        log_warn("交接提醒安裝不完整，請重新執行 handoff remind enable")
    else:
        log_info("Claude 交接提醒未啟用")
    log_info("只提醒，不自動寫入交接；使用新會話確認 hooks 生效")
    return 0


def _handoff_list(cwd: "Path | None" = None) -> int:
    """List one project's threads; a path lets a session read another's."""
    from .. import handoff as hand

    if cwd is not None and not cwd.is_dir():
        raise ValueError(f"找不到這個專案目錄:{cwd}")
    # 每個 session 開工都會走這裡,所以歸檔掛在這:結束很久的線自己
    # 讓開,不必有人記得清。搬不動就算了,列表比歸檔重要
    try:
        archived = hand.archive_finished()
    except OSError:
        archived = []
    # 這些檔案有進版控,不說一聲的話下次 push 會冒出沒人解釋的改名
    if archived:
        log_info(
            f"已把 {len(archived)} 條結案或 {hand.ARCHIVE_AFTER_DAYS} 天沒人動的線"
            f"移到 {hand.ARCHIVE_DIR_NAME}/:{'、'.join(archived)}"
        )
    # 結束的線留在磁碟上當紀錄,但這裡問的是「有什麼可以接手」,
    # 把它們一起列出來只會讓人多判斷一次哪條還活著
    notes = [
        note for note in hand.load_all(memory_paths.project_key(cwd).key)
        if note.state != hand.DONE
    ]
    if not notes:
        where = f"{cwd} 這個專案" if cwd is not None else "這個專案"
        log_info(f"{where}沒有待接手的工作線")
        return 0
    for note in notes:
        age = hand.age_in_days(note.created)
        # 一條線放了幾天,就是該不該接它的理由;當天開的不用說
        waited = f"  ({age} 天前開的)" if age else ""
        # 擱著沒動的線跟剛寫好的長得一樣,而裡面的進度可能早就被
        # 別處的工作蓋過去了。接手前該先讀一遍,不是照著做
        stale = "  ⚠ 可能已過期" if hand.is_stale(note) else ""
        print(f"  ○ {note.name}{waited}{stale}")
        print(f"    {hand.summary(note.body)}")
    return 0
