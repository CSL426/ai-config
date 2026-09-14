# 一台機器,兩個 GitHub 帳號

公司帳號和個人帳號放在同一台機器上,推送時常常推錯身分。這篇說明為什麼
`acg login` 解不掉這個問題,以及該用什麼取代它。

## 徵狀

推送或抓取失敗,訊息是:

```
ERROR: Repository not found.
fatal: Could not read from remote repository.
```

關鍵在於它說的是「找不到」而不是「沒有權限」。GitHub 對私有儲存庫一律回
404,不會告訴未授權的一方那個儲存庫存在。所以真正的原因是**目前生效的帳號
看不到這個儲存庫**,不是儲存庫被刪掉了。很容易往錯的方向查。

## 為什麼 `acg login` 解不掉

`acg login <帳號>` 把一個 credential helper 寫進資料儲存庫的本地 git 設定,
git 需要憑證時由 acg 向 gh 拿那個帳號的 token。這對**資料儲存庫本身**有效,
因為綁定寫在那個 repo 的 local config 裡。

但它救不了其他儲存庫,原因有三個,每一個都足以擋死:

- `gh auth git-credential` 不接受 `--user`。無法讓 helper 針對不同 repo 回不同
  帳號的 token。
- gh 只看 `hosts.yml` 裡當下的 `user:` 欄位,那是整台機器共用的狀態。
- 把帳號寫進 remote URL(`https://USER@github.com/...`)沒有用,gh 的 helper
  不讀 URL 裡的 username。

所以 HTTPS 加 gh 這條路上,一台機器同時只有一個 GitHub 身分。要切換就得
`gh auth switch`,而那會影響所有儲存庫,忘記切回來就是下一次的 404。

## 解法:每個帳號一把 SSH 金鑰加一個 Host 別名

SSH 完全繞過 credential helper,身分由金鑰決定,而金鑰由 remote URL 裡的
Host 別名挑。設定一次之後,兩個帳號可以並存,不需要切換。

### 一、為這個帳號產一把專用金鑰

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_csl426 -C "csl426"
```

檔名帶帳號,之後看得出來哪把是哪個。

### 二、把公鑰加到那個 GitHub 帳號

```bash
gh auth refresh -h github.com -s admin:public_key
gh ssh-key add ~/.ssh/id_ed25519_csl426.pub --title "工作機"
```

第一行常被略過然後卡住。gh 原本的 token 通常沒有 `admin:public_key` 這個
scope,少了它 `gh ssh-key add` 會回 404,而那個 404 同樣看不出真正原因。

`gh auth refresh` 要開瀏覽器做 device 授權,**沒辦法自動化**,這一步一定要人
自己操作。

注意 `gh ssh-key add` 加到的是**目前生效的帳號**。要加到另一個帳號,先
`gh auth switch` 過去再執行。

### 三、在 `~/.ssh/config` 建立別名

```
Host github-csl426
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_csl426
    IdentitiesOnly yes
```

`IdentitiesOnly yes` 不可省略。少了它,ssh 會把 agent 裡所有金鑰依序試一遍,
第一把被接受的就定身分,結果仍然是隨機推錯帳號。

### 四、把儲存庫的 remote 指向別名

```bash
git remote set-url origin git@github-csl426:CSL426/CI-workflow.git
```

注意冒號前面是**別名**不是 `github.com`。驗證:

```bash
ssh -T git@github-csl426
```

它會回 `Hi <帳號>! You've successfully authenticated`,那個名字必須是你要的
帳號。

設好之後,`gh auth switch` 切到哪個帳號都不影響這個儲存庫。

## 不要把 `~/.ssh/config` 交給 acg 同步

acg 管的是 `data/claude/`、`data/codex/` 與 agy 的對應設定,`~/.ssh` 不在範圍
內,也不該加進去:

- 資料儲存庫是 git repo 並推上遠端。就算不含私鑰,`~/.ssh/config` 本身也會洩漏
  所有內部主機名、跳板設定與使用者名稱,那是偵察用的資訊。
- 每台機器的金鑰檔名與路徑本來就不同。同步了 config 卻沒有對應的金鑰,只會讓
  `IdentityFile` 指向不存在的檔案。

這是一條**慣例**,不是一份設定檔。各機器照著設一次,不要試圖同步結果。

## 附帶:資料儲存庫的綁定會自己跟上執行檔

從 1.0.41 起,`acg login` 寫進去的 helper 指向**安裝位置**的執行檔,而不是當下
啟動的那一份。獨立執行檔常常先從下載資料夾跑一次才被安裝到
`~/.local/bin`,舊版會把下載資料夾的路徑寫死,隔天安裝完就對不上。

每次推送檢查前也會比對綁定的路徑和目前該用的執行檔,不同就自動改指,所以更新
或搬移之後不需要重新 `acg login`。

這解決的是「執行檔搬家」,和上面的多帳號問題無關,兩者不要混為一談。
