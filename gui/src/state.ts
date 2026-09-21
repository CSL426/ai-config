/** State every area reads and several of them write.

 * These were module-level `let`s in one file. An exported `let` is a
 * read-only binding to whoever imports it, so the moment the areas live
 * in separate files they need a shared object to write through instead.
 */

import type {
  MemoryInfo, PushScope, SettingsInfo, SkillEntry, ToolScope,
} from "./bridge";

export type MainView = "status" | "output" | "skills" | "export" | "apply" | "memory";
export type PendingPreview = {
  kind: "push" | "apply" | "memory";
  token: string;
  scope: PushScope;
  label: string;
};

export const state = {
  currentView: "status" as MainView,
  outputReturn: "status" as MainView,
  selectedTool: "all" as ToolScope,
  configured: false,
  running: false,
  connected: false,
  restartRequired: false,
  skillsLoading: false,
  settingsLoading: false,
  pendingPreview: null as PendingPreview | null,
  previewOpener: null as HTMLElement | null,
  memoryInfo: null as MemoryInfo | null,
  memoryLoading: false,
  projectToken: null as string | null,
  pendingUpdate: null as string | null,
  updateInstalled: false,
  settingsInfo: null as SettingsInfo | null,
  settingsOpener: null as HTMLElement | null,
  skills: [] as SkillEntry[],
  skillDirectory: null as string | null,
  selectedSkills: new Set<string>(),
};
