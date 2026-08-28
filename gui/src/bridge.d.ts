/** Python 端 GuiApi(ai_config/gui.py)的型別契約 — 前後端唯一介面。 */

export type AcgCommand = "status" | "apply" | "pull" | "push";

export interface AcgInfo {
  version: string;
  repo: string;
  tools: string[];
}

export interface RunResult {
  code: number;
  output: string;
}

export interface SkillList {
  skills: string[];
}

export interface PackageResult {
  code: number;
  output: string;
  zips: string[];
}

export interface AcgApi {
  get_info(): Promise<AcgInfo>;
  run(cmd: AcgCommand, tool?: string): Promise<RunResult>;
  list_skills(): Promise<SkillList>;
  package_skills(names: string[]): Promise<PackageResult>;
}

declare global {
  interface Window {
    /** pywebview 於 `pywebviewready` 事件後注入;瀏覽器 dev 模式下不存在。 */
    pywebview?: { api: AcgApi };
  }
}
