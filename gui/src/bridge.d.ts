/** Python 端 GuiApi(ai_config/gui.py)的型別契約 — 前後端唯一介面。 */

export type AcgCommand = "status" | "apply" | "pull" | "push";

export interface AcgInfo {
  version: string;
  repo: string;
  provider: "git" | "gdrive";
  tools: string[];
  configured: boolean;
  config_error: string;
}

export interface RunResult {
  code: number;
  output: string;
}

export interface PushPreview extends RunResult {
  needs_confirmation: boolean;
  token: string;
}

export interface SkillEntry {
  name: string;
  shared: boolean;
  shareable: boolean;
}

export interface SkillList {
  skills: SkillEntry[];
}

export interface PackageResult {
  code: number;
  output: string;
  zips: string[];
}

export interface UpdateCheck {
  code: number;
  current: string;
  latest: string;
  up_to_date: boolean;
  output: string;
}

export interface SettingsInfo {
  provider: string;
  repo: string;
  remote_url: string;
  gdrive_space: string;
  gdrive_folder: string;
  gdrive_folder_url: string;
  signed_in: boolean;
}

interface AcgApi {
  get_info(): Promise<AcgInfo>;
  config_info(): Promise<RunResult>;
  settings_info(): Promise<SettingsInfo>;
  open_data_dir(): Promise<RunResult>;
  run(cmd: AcgCommand, tool?: string): Promise<RunResult>;
  preview_push(tool?: string): Promise<PushPreview>;
  confirm_push(tool: string, token: string): Promise<RunResult>;
  list_skills(): Promise<SkillList>;
  package_skills(names: string[]): Promise<PackageResult>;
  share_skills(names: string[]): Promise<RunResult>;
  unshare_skills(names: string[]): Promise<RunResult>;
  check_update(): Promise<UpdateCheck>;
  run_update(): Promise<RunResult>;
  setup_repo(repoUrl: string, dataDir?: string): Promise<RunResult>;
  setup_gdrive(
    dataDir?: string,
    gdriveFolder?: string,
    gdriveSpace?: string,
  ): Promise<RunResult>;
}

declare global {
  interface Window {
    /** pywebview 於 `pywebviewready` 事件後注入;瀏覽器 dev 模式下不存在。 */
    pywebview?: { api: AcgApi };
  }
}
