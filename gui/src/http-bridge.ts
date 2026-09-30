/** Browser mode: acg serves this page on 127.0.0.1 where no native window can
 * open (Linux and macOS executables, a machine reached over SSH), and the bridge
 * becomes HTTP calls to the same Python object.
 *
 * The launch URL carries a one-time code in its fragment, which never reaches a
 * server log; it is traded for a session token that every call sends in a header.
 */

import type { AcgApi } from "./bridge";

const TOKEN_KEY = "acg-token";

async function claim(code: string): Promise<string | null> {
  const response = await fetch("/api/__claim", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }),
  });
  if (!response.ok) return null;
  return (await response.json()).token ?? null;
}

function folderPrompt(what: string): string | null {
  // 瀏覽器裡沒有資料夾對話框,而且 acg 可能在另一台機器上:問路徑,後端照樣驗證
  return window.prompt(`輸入${what}的完整路徑（acg 所在那台機器上的路徑）`);
}

export async function connectHttpBridge(): Promise<boolean> {
  if (window.pywebview || location.protocol !== "http:") return Boolean(window.pywebview);
  const code = new URLSearchParams(location.hash.slice(1)).get("c");
  let token: string | null = null;
  try { token = sessionStorage.getItem(TOKEN_KEY); } catch { /* 無痕或封鎖儲存 */ }
  if (code) {
    history.replaceState(null, "", location.pathname);
    token = await claim(code).catch(() => null);
    if (token) {
      try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* 重新整理時得重開 */ }
    }
  }
  if (!token) return false;

  const call = async (method: string, args: unknown[]) => {
    const response = await fetch(`/api/${method}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Acg-Token": token! },
      body: JSON.stringify(args),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
    return body.result;
  };
  const overrides: Record<string, (...args: unknown[]) => Promise<unknown>> = {
    select_project: async () => {
      const path = folderPrompt("專案資料夾");
      return path === null
        ? { code: 0, output: "", error: null, cancelled: true, project_token: null,
          root: null, memory_root: null, key: null, stable: false }
        : call("select_project", [path]);
    },
    select_skill_directory: async () => {
      const path = folderPrompt("技能資料夾");
      return path === null
        ? { code: 0, output: "", error: null, cancelled: true, path: null }
        : call("select_skill_directory", [path]);
    },
  };
  const api = new Proxy({}, {
    get: (_target, method) => {
      // then 不能是函式,否則 await 這個物件會被當成 promise 卡住
      if (typeof method !== "string" || method === "then") return undefined;
      return overrides[method] ?? ((...args: unknown[]) => call(method, args));
    },
  }) as AcgApi;
  window.pywebview = { api };
  // 伺服器在頁面停止回報後自己結束,不留一個沒人用的行程
  window.setInterval(() => { void call("__ping", []).catch(() => undefined); }, 20_000);
  window.dispatchEvent(new Event("pywebviewready"));
  return true;
}
