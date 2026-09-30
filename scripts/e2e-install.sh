#!/usr/bin/env bash
# End-to-end check of the onedir layout on Linux or macOS, in a scratch HOME
# so the machine's own install and ~/.claude are never touched. CI runs it
# on every build; it also runs by hand on a real machine.
# Usage: e2e-install.sh <archive.tar.gz> <install.sh>
# Needs network: it downgrades to the 1.0.97 release and back.
# Written for bash 3.2, the one macOS ships.
set -uo pipefail
archive="$1"; installer="$2"
root="$(mktemp -d)"; export HOME="$root/home"; mkdir -p "$HOME"
export AI_CONFIG_NO_PLUGIN=1 AI_CONFIG_SKIP_COMPLETION=1 AI_CONFIG_NO_UPDATE_CHECK=1
mkdir -p "$root/data/claude"; export AI_CONFIG_REPO="$root/data"
bin="$HOME/.local/bin"; versions="$HOME/.local/share/ai-config/versions"
fails=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails+1)); fi; }

AI_CONFIG_BINARY_PATH="$archive" bash "$installer" >"$root/install.log" 2>&1
rc=$?
check "install from archive exits 0" '[[ $rc -eq 0 ]]'
target="$(readlink "$bin/ai-config")"
check "PATH entry is a symlink into versions" '[[ "$target" == "$versions/"*"/ai-config" ]]'
check "version dir is onedir under app/" '[[ "$target" == */app/ai-config && -d "$(dirname "$target")/_internal" ]]'
v="$("$bin/acg" --version)"; echo "  $v"
check "acg --version runs" '[[ "$v" == *"ai-config (acg)"* ]]'
check "help runs" '"$bin/acg" help >/dev/null 2>&1'
# BSD date has no %N; python3 is on every machine this runs on
now_ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }
t0=$(now_ms); for i in 1 2 3 4 5; do "$bin/ai-config" --version >/dev/null; done; t1=$(now_ms)
echo "  --version avg: $(( (t1-t0)/5 )) ms"
t0=$(now_ms); for i in 1 2 3 4 5; do "$bin/ai-config" __handoff-statusline eA== 70 </dev/null >/dev/null 2>&1; done; t1=$(now_ms)
echo "  statusline avg: $(( (t1-t0)/5 )) ms"
check "versions marks the active one" '"$bin/acg" versions 2>&1 | grep -q "✓"'
installed="$(printf "%s" "$target" | sed -E "s#.*/versions/([^/]+)/.*#\1#")"

# 退回一個只有單一執行檔的舊版(走那一版自己的安裝腳本),再切回來
"$bin/acg" update 1.0.97 >"$root/down.log" 2>&1
check "pinned downgrade to 1.0.97 succeeds" '"$bin/acg" --version | grep -q 1.0.97'
check "1.0.97 is a single file" '[[ -f "$versions/1.0.97/ai-config" && ! -d "$versions/1.0.97/_internal" ]]'
# 舊版(1.0.97)切回來:它看不到 app/ 裡的 onedir,應改用那一版的安裝腳本,
# 結果必須是一個能跑的 acg,而不是被單獨複製走的 onedir exe
# 舊版只能從 release 下載;發版建置時這一版還沒發布,這一步無從測起
if curl -fsIL "https://github.com/CSL426/ai-config/releases/download/v$installed/install.sh" >/dev/null 2>&1; then
    "$bin/acg" update "$installed" >"$root/back.log" 2>&1
    check "old code switching forward leaves a working acg" '"$bin/acg" --version | grep -q "$installed"'
else
    echo "SKIP old code switching forward: v$installed is not released yet"
    # 下一步要從這個組建切到 1.0.97,所以先用新版程式切回來
    "$versions/$installed/app/ai-config" update "$installed" >"$root/back.log" 2>&1
    check "new code switches back to the build" '"$bin/acg" --version | grep -q "$installed"'
fi
# 新版切到磁碟上已有的版本:只換連結,不下載
"$versions/$installed/app/ai-config" update 1.0.97 >"$root/switch.log" 2>&1
check "new code switches to a version on disk without download" 'grep -q "已切換到" "$root/switch.log" && ! grep -q "Fetching installer" "$root/switch.log" && "$bin/acg" --version | grep -q 1.0.97'
check "no staging or aside dirs left" '! ls -a "$versions" | grep -q "^\.[0-9]"'

# 重裝同一版會整個換掉版本目錄
AI_CONFIG_BINARY_PATH="$archive" bash "$installer" >"$root/reinstall.log" 2>&1
check "reinstall same version" '"$bin/acg" --version >/dev/null && ! ls -a "$versions" | grep -q "^\.[0-9]"'

echo "RESULT: $fails failure(s)"
if [[ $fails -eq 0 ]]; then rm -rf "$root"; exit 0; fi
echo "kept $root"
for log in "$root"/*.log; do echo "--- $log"; cat "$log"; done
exit 1
