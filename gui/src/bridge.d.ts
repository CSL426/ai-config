/** Python 端 GuiApi(ai_config/commands/gui.py)的型別契約 — 前後端唯一介面。 */

export type AcgCommand = "status" | "apply" | "pull" | "push";

export interface AcgInfo {
  version: string;
  build_commit: string;
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

export type ToolScope = "all" | "claude" | "codex" | "agy";
export type ApplyCategory = "settings" | "skills" | "all";
export type MemoryAction = "enable" | "disable" | "adopt" | "release";
export type PushScope = ToolScope | "memory";
export type ErrorCode = "INVALID_ARGUMENT" | "NOT_CONFIGURED" | "BUSY"
  | "UNSAFE_PATH" | "CONFLICT" | "STALE_PREVIEW" | "GIT_BLOCKED"
  | "IO_ERROR" | "ROLLBACK_FAILED";
export interface OperationResult extends RunResult {
  error: ErrorCode | null;
  backup_path: string | null;
  recovery_required: boolean;
}
export interface ProjectSelection extends RunResult {
  error: ErrorCode | null;
  cancelled: boolean;
  project_token: string | null;
  root: string | null;
  key: string | null;
  stable: boolean;
}
export interface MemoryLocation {
  label: string;
  path: string;
  token: string;
}
export interface MemoryPermission { allowed: boolean; reason: string; }
export interface MemoryInfo extends RunResult {
  error: ErrorCode | null;
  data_root: string;
  shared_path: string;
  shared_status: "missing" | "ok" | "conflict";
  tracked: boolean;
  git_status: "clean" | "dirty" | "untracked";
  changed_paths: string[];
  entries: {
    tool: Exclude<ToolScope, "all">;
    status: "missing" | "installed" | "blocked";
    reason: string;
    path: string;
    cli_installed: boolean;
  }[];
  project: {
    root: string;
    key: string;
    stable: boolean;
    memory_path: string;
    journal_path: string;
    journal_status: string;
    remember_installed: boolean;
  } | null;
  actions: Record<MemoryAction | "push", MemoryPermission>;
  locations: MemoryLocation[];
}
export interface PreviewChange {
  category: string;
  tool: string;
  operation: string;
  source: string | null;
  destination: string;
  physical_target: string | null;
  shared: boolean;
  reason: string;
}
export interface ChangePreview extends RunResult {
  error: ErrorCode | null;
  token: string;
  needs_confirmation: boolean;
  scope: { tool?: ToolScope; category?: ApplyCategory; action?: MemoryAction; project_key?: string | null };
  changes: PreviewChange[];
  warnings: string[];
}
export interface PushPreview extends RunResult {
  error: ErrorCode | null;
  scope: PushScope;
  changed_paths: string[];
  outgoing_commits: string[];
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

export interface GithubAccess {
  /** 正式建置才有瀏覽器登入;沒有時只能用 gh 已登入的帳號 */
  device_login: boolean;
  repository: string;
  installed: boolean;
  logged_in: boolean;
  account: string;
  accounts: string[];
  can_push: boolean | null;
  actionable: boolean;
  lines: string[];
}

export interface GithubLoginStart extends RunResult {
  device_code?: string;
  user_code?: string;
  verification_uri?: string;
  interval?: number;
}

export interface GithubLoginPoll extends RunResult {
  status: "pending" | "done" | "error";
}

interface AcgApi {
  get_info(): Promise<AcgInfo>;
  config_info(): Promise<RunResult>;
  settings_info(): Promise<SettingsInfo>;
  github_access(): Promise<GithubAccess>;
  github_start_login(): Promise<GithubLoginStart>;
  github_terminal_login(): Promise<RunResult>;
  github_poll_login(deviceCode: string, interval: number): Promise<GithubLoginPoll>;
  github_use_account(account: string): Promise<RunResult>;
  relogin_gdrive(): Promise<RunResult>;
  open_data_dir(): Promise<RunResult>;
  run(cmd: AcgCommand, tool?: string): Promise<RunResult>;
  select_memory_project(): Promise<ProjectSelection>;
  memory_info(projectToken?: string): Promise<MemoryInfo>;
  open_memory_location(locationToken: string): Promise<OperationResult>;
  preview_memory(action: MemoryAction, projectToken?: string): Promise<ChangePreview>;
  confirm_memory(token: string): Promise<OperationResult>;
  preview_apply(tool: ToolScope, category: ApplyCategory): Promise<ChangePreview>;
  confirm_apply(token: string): Promise<OperationResult>;
  cancel_preview(token: string): Promise<OperationResult>;
  preview_push(scope?: PushScope): Promise<PushPreview>;
  confirm_push(scope: PushScope, token: string): Promise<OperationResult>;
  list_skills(): Promise<SkillList>;
  package_skills(names: string[]): Promise<PackageResult>;
  share_skills(names: string[]): Promise<RunResult>;
  unshare_skills(names: string[]): Promise<RunResult>;
  check_update(): Promise<UpdateCheck>;
  run_update(): Promise<RunResult>;
  setup_repo(repoUrl: string, dataDir?: string, account?: string): Promise<RunResult>;
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
