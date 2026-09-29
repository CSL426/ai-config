# 執行檔改為 onedir 與 Windows 啟動器

狀態:**Draft**。使用者確認前不改為 Done。

## 為什麼

目前每個平台都發 PyInstaller onefile。onefile 每次啟動先把整個 Python
執行期解壓到暫存目錄,執行中再回頭從自己的檔案讀模組。這帶來兩類問題:

- **換掉執行中的檔案會壞**。Windows 建不了 symlink,PATH 上的
  `ai-config.exe` 是版本目錄裡那支的複本,更新時原地替換。舊行程之後
  再 import 就讀到新檔案,`Error -3 while decompressing data`。1.0.93
  的 hook 修復、1.0.98 的 plugin 更新都死在這裡,後者還先印了
  「Update complete」。
- **每次啟動都慢**。workstation 實測 `--version` onefile 0.65 秒,同一份程式
  onedir 0.35 秒;statusline hook 0.7 秒對 0.3~0.5 秒。hook 在每次送出
  提示、每次工具呼叫都跑。Windows 另有 Defender 掃描解壓出的檔案,
  更新時間從 20 秒漲到 4 分鐘,是否與此有關尚未量測。

## 版面

    ~/.local/share/ai-config/versions/1.0.99/ai-config[.exe]   onedir 主程式
                                            /_internal/          執行期與模組
    ~/.local/share/ai-config/versions/active                     目前版本的記錄

- **Linux、macOS**:`~/.local/bin/ai-config` 照舊是指向
  `versions/<版號>/ai-config` 的 symlink。onedir 經由 symlink 啟動時會
  找到真正的 `_internal`(workstation 實測)。
- **Windows**:`~/.local/bin/ai-config.exe` 換成一支 Rust 寫的小啟動器。
  它讀 `versions/active`,以同樣的參數、主控台與標準輸入輸出啟動
  `versions/<版號>/ai-config.exe`,等它結束並傳回結束碼。啟動器本身
  幾乎不會變,更新只是新增版本目錄、改寫 `active`,沒有任何正在執行的
  檔案被覆寫。

啟動器要點:
- Ctrl+C 交給子行程處理,啟動器自己不因此結束;不用
  `SetConsoleCtrlHandler(NULL, TRUE)`,那會被子行程繼承。
- 不放進 Job Object:GUI 會脫離成孫行程,Job 會把它一起殺掉。
- 以環境變數 `AI_CONFIG_LAUNCHER` 告訴子行程啟動器的位置。雙擊時主控台
  掛著啟動器與主程式兩個行程,主程式用它判斷主控台是不是自己的。
- `active` 讀不到或指向不存在的版本時,改用磁碟上最新的版本並提示。
- 舊版的 onefile 版本目錄照樣能被啟動器執行,回滾不受影響。

## 發佈物

| 資產 | 內容 |
| --- | --- |
| `ai-config-<平台>.tar.gz`(Linux、macOS) | onedir 目錄 |
| `ai-config-windows-x86_64.zip` | onedir 目錄 |
| `ai-config-launcher-windows-x86_64.exe` | 啟動器 |
| `acg.exe` | Windows 單檔可攜版(onefile),給下載後直接雙擊的人 |
| `install.sh`、`install.ps1` | 安裝腳本,與執行檔同一個 release |

每個資產都附 `.sha256`。Linux、macOS 不再發單一執行檔。

## 安裝與更新

安裝腳本下載壓縮檔、驗證雜湊、解到 `versions/<版號>.staging` 再改名,
問解出來的主程式自己的版號,最後切換:Unix 換 symlink,Windows 寫
`active`,並在啟動器內容不同時用「移到旁邊再放新的」換掉它。

更新後要做的事(更新 Claude Code 的 /acg plugin、修 hook 路徑)改由
**安裝腳本呼叫新版執行檔**完成。安裝腳本出自目標 release,所以不論是
哪一版的 acg 發起更新,做這些事的都是新版程式。打包版的 `acg update`
不再自己做這兩步;從原始碼或 uv 安裝的仍在行程內做。

## 過渡

- ≤1.0.98 的 Windows 用戶端更新到這一版時,舊行程仍會在最後 import 而
  崩潰。安裝腳本已先做完更新後的步驟,崩潰只剩一段 traceback,不影響
  結果。
- 第一次更新時,PATH 上那支 onefile 複本會被收進版本目錄(既有的
  adopt 流程),再由啟動器取代。

## 驗證

- CI:三個平台建 onedir 並跑 `help`;Windows 另外經由啟動器跑一次
  `help` 與 git credential helper 煙霧測試。啟動器在 Linux 與 Windows
  跑 `cargo test`。
- 實機:workstation、arm-box 本機;Windows 請 `debug acg` session 量測更新時間、
  `acg --version` 與 hook 的啟動時間,並確認 cmd 打 `acg`、雙擊、
  `acg gui` 三種啟動方式。

## 之後

### 第二階段:hook 熱路徑

第一階段做完再量。hook 仍明顯拖慢的話,把 statusline、memory-entry
等高頻小指令移進啟動器,其餘照舊交給 Python。

### macOS

目前 macOS 有建置但沒有 GUI。onedir 之後加上 GUI 只需要在建置時帶入
pywebview 的 Cocoa 後端(pyobjc)與前端資源,與 Windows 相同。另要做:
`gui --shortcut` 在 `~/Applications` 建一個 `.app`;確認 curl 下載的
未簽章執行檔不受 Gatekeeper 隔離(curl 不加 quarantine 屬性),以及
是否要簽章與公證。**需要一台 Mac 實測**。

### Linux GUI

pywebview 在 Linux 要用系統的 GTK/WebKit2GTK 或 Qt,都很難打包進獨立
執行檔。兩個方向:

1. **瀏覽器模式**:`acg gui` 在 127.0.0.1 起一個本機伺服器(隨機 port、
   一次性 token、檢查 Host 與 Origin),用系統瀏覽器開。前端的
   `window.pywebview.api` 改由一層 fetch 轉接,Python 端沿用同一個
   `GuiApi`。不需要任何系統套件,SSH 轉發 port 也能用。缺點:沒有原生
   資料夾對話框,要改成在頁面內瀏覽目錄。
2. **pywebview GTK**:要求使用者先裝 WebKit2GTK 與 PyGObject,打包版要
   帶 gi 的 typelib,跨發行版容易壞。

建議 1。**待使用者決定**。

## 未知

- Windows 更新 4 分鐘的原因(onefile 解壓、Defender、下載速度都可能)。
- onedir 解壓後的檔案數(數百個)在 Windows 上被 Defender 掃描的成本。
- 啟動器多一層行程對 git credential helper、PowerShell 補全、工作排程器
  的影響,要在 Windows 實測。
