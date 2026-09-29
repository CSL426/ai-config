//! The name on PATH on Windows: runs the version recorded in `versions/active`.
//!
//! Windows cannot make a symlink without a privilege most accounts lack, so
//! the stable path used to be a copy of the real executable, replaced in
//! place on every update. A PyInstaller onefile build reads its own modules
//! from that path while it runs, and after the swap it read the new file at
//! the old offsets ("Error -3 while decompressing data"). This launcher
//! never changes between releases: an update writes a new version directory
//! and rewrites the one-line `active` record, and nothing that is running
//! is ever overwritten.
//!
//! On Unix the stable path stays a symlink; this builds there so the same
//! resolution is tested on every platform, and it execs instead of waiting.

use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{self, Command};

#[cfg(windows)]
const EXECUTABLE: &str = "ai-config.exe";
#[cfg(not(windows))]
const EXECUTABLE: &str = "ai-config";

/// Where the installers keep `versions/`; mirrors `ai_config.versions.store_dir`.
fn share_dir() -> Option<PathBuf> {
    if let Some(dir) = env::var_os("AI_CONFIG_SHARE_DIR").filter(|d| !d.is_empty()) {
        return Some(PathBuf::from(dir));
    }
    let home = if cfg!(windows) { env::var_os("USERPROFILE") } else { env::var_os("HOME") };
    home.filter(|h| !h.is_empty())
        .map(|h| PathBuf::from(h).join(".local").join("share").join("ai-config"))
}

#[derive(Debug, PartialEq)]
struct Resolved {
    executable: PathBuf,
    /// Set when `active` could not be used and the newest version stood in.
    fallback_from: Option<String>,
}

/// A version name straight from a file; anything that could step outside
/// `versions/` is not a version name.
fn plain_name(name: &str) -> bool {
    !name.is_empty()
        && name != "."
        && name != ".."
        && !name.contains(['/', '\\', ':'])
}

/// Numeric where the pieces are numbers, so 1.0.100 sorts after 1.0.99.
fn version_key(name: &str) -> Vec<(u8, u64, String)> {
    name.split('.')
        .map(|piece| match piece.parse::<u64>() {
            Ok(number) => (0, number, String::new()),
            Err(_) => (1, 0, piece.to_string()),
        })
        .collect()
}

fn resolve(versions: &Path) -> Result<Resolved, String> {
    let recorded = fs::read_to_string(versions.join("active"))
        .map(|text| text.trim().to_string())
        .unwrap_or_default();
    if plain_name(&recorded) {
        let executable = versions.join(&recorded).join(EXECUTABLE);
        if executable.is_file() {
            return Ok(Resolved { executable, fallback_from: None });
        }
    }
    // 記錄壞了或那一版被刪掉:用磁碟上最新的一版,總比完全不能用好
    let mut found: Vec<String> = fs::read_dir(versions)
        .map_err(|error| format!("找不到已安裝的版本({}):{error}", versions.display()))?
        .filter_map(|entry| entry.ok())
        .filter_map(|entry| entry.file_name().into_string().ok())
        .filter(|name| plain_name(name) && versions.join(name).join(EXECUTABLE).is_file())
        .collect();
    found.sort_by_key(|name| version_key(name));
    match found.pop() {
        Some(newest) => Ok(Resolved {
            executable: versions.join(newest).join(EXECUTABLE),
            fallback_from: Some(if recorded.is_empty() { "(空白)".into() } else { recorded }),
        }),
        None => Err(format!("{} 裡沒有已安裝的版本", versions.display())),
    }
}

fn main() {
    let arguments: Vec<OsString> = env::args_os().skip(1).collect();
    let Some(share) = share_dir() else {
        fail("找不到家目錄,無法定位已安裝的版本");
    };
    let resolved = match resolve(&share.join("versions")) {
        Ok(resolved) => resolved,
        Err(message) => fail(&message),
    };
    if let Some(recorded) = &resolved.fallback_from {
        eprintln!(
            "⚠ versions/active 記錄的 {recorded} 不能用,改用 {};重新執行安裝腳本可修正",
            resolved.executable.display()
        );
    }
    let mut command = Command::new(&resolved.executable);
    command.args(&arguments);
    // 主程式靠它認出:掛在同一個主控台上的另一個行程是自己的啟動器,不是 shell
    if let Ok(launcher) = env::current_exe() {
        command.env("AI_CONFIG_LAUNCHER", launcher);
    }
    run(command, &resolved.executable);
}

#[cfg(unix)]
fn run(mut command: Command, executable: &Path) -> ! {
    use std::os::unix::process::CommandExt;
    let error = command.exec();
    fail(&format!("無法執行 {}:{error}", executable.display()));
}

#[cfg(windows)]
fn run(mut command: Command, executable: &Path) -> ! {
    windows::keep_running_on_ctrl_c();
    match command.status() {
        // Windows 的結束碼是 32 位元;ExitCode 只收得下 0-255
        Ok(status) => process::exit(status.code().unwrap_or(1)),
        Err(error) => fail(&format!("無法執行 {}:{error}", executable.display())),
    }
}

fn fail(message: &str) -> ! {
    eprintln!("✗ ai-config:{message}");
    #[cfg(windows)]
    windows::pause_if_console_is_ours();
    process::exit(1);
}

#[cfg(windows)]
mod windows {
    type Bool = i32;

    extern "system" {
        fn SetConsoleCtrlHandler(handler: Option<unsafe extern "system" fn(u32) -> Bool>, add: Bool) -> Bool;
        fn GetConsoleProcessList(list: *mut u32, count: u32) -> u32;
    }

    unsafe extern "system" fn ignore(_event: u32) -> Bool {
        1
    }

    /// Ctrl+C reaches every process on the console; the child decides what it
    /// means and exits, and its code is what we return.
    ///
    /// A handler, not `SetConsoleCtrlHandler(NULL, TRUE)`: that flag is
    /// inherited, and the child would ignore Ctrl+C too.
    pub fn keep_running_on_ctrl_c() {
        unsafe {
            SetConsoleCtrlHandler(Some(ignore), 1);
        }
    }

    /// A double-click gives us a console of our own that closes with us;
    /// without a pause the error would flash and vanish.
    pub fn pause_if_console_is_ours() {
        let mut list = [0u32; 4];
        let count = unsafe { GetConsoleProcessList(list.as_mut_ptr(), list.len() as u32) };
        if count == 1 {
            eprint!("按 Enter 關閉視窗…");
            let _ = std::io::stdin().read_line(&mut String::new());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layout(names: &[&str]) -> tempdir::Dir {
        let dir = tempdir::Dir::new();
        for name in names {
            let root = dir.path().join(name);
            fs::create_dir_all(&root).unwrap();
            fs::write(root.join(EXECUTABLE), b"exe").unwrap();
        }
        dir
    }

    #[test]
    fn the_recorded_version_is_the_one_run() {
        let dir = layout(&["1.0.98", "1.0.99"]);
        fs::write(dir.path().join("active"), "1.0.98\n").unwrap();
        let resolved = resolve(dir.path()).unwrap();
        assert_eq!(resolved.executable, dir.path().join("1.0.98").join(EXECUTABLE));
        assert_eq!(resolved.fallback_from, None);
    }

    #[test]
    fn a_missing_record_falls_back_to_the_newest_numerically() {
        let dir = layout(&["1.0.9", "1.0.100", "1.0.99"]);
        let resolved = resolve(dir.path()).unwrap();
        assert_eq!(resolved.executable, dir.path().join("1.0.100").join(EXECUTABLE));
        assert!(resolved.fallback_from.is_some());
    }

    #[test]
    fn a_record_pointing_at_a_deleted_version_falls_back() {
        let dir = layout(&["1.0.99"]);
        fs::write(dir.path().join("active"), "1.0.50").unwrap();
        let resolved = resolve(dir.path()).unwrap();
        assert_eq!(resolved.executable, dir.path().join("1.0.99").join(EXECUTABLE));
        assert_eq!(resolved.fallback_from.as_deref(), Some("1.0.50"));
    }

    #[test]
    fn a_record_cannot_step_outside_the_versions_directory() {
        let dir = layout(&["1.0.99"]);
        let outside = dir.path().join("..").join(EXECUTABLE);
        fs::write(&outside, b"not ours").ok();
        fs::write(dir.path().join("active"), "..").unwrap();
        let resolved = resolve(dir.path()).unwrap();
        assert_eq!(resolved.executable, dir.path().join("1.0.99").join(EXECUTABLE));
        fs::remove_file(outside).ok();
    }

    #[test]
    fn a_directory_without_the_executable_is_not_a_version() {
        let dir = layout(&["1.0.98"]);
        fs::create_dir_all(dir.path().join("1.0.99.staging")).unwrap();
        fs::create_dir_all(dir.path().join("1.1.0")).unwrap();
        let resolved = resolve(dir.path()).unwrap();
        assert_eq!(resolved.executable, dir.path().join("1.0.98").join(EXECUTABLE));
    }

    #[test]
    fn nothing_installed_is_an_error() {
        let dir = layout(&[]);
        assert!(resolve(dir.path()).is_err());
        assert!(resolve(&dir.path().join("absent")).is_err());
    }

    /// Enough of a temporary directory for these tests, without a dependency.
    mod tempdir {
        use std::path::{Path, PathBuf};
        use std::sync::atomic::{AtomicUsize, Ordering};

        static NEXT: AtomicUsize = AtomicUsize::new(0);

        pub struct Dir(PathBuf);

        impl Dir {
            pub fn new() -> Self {
                let unique = format!(
                    "acg-launcher-{}-{}",
                    std::process::id(),
                    NEXT.fetch_add(1, Ordering::SeqCst)
                );
                // 多包一層,「跳出 versions」的測試才不會寫到共用的暫存目錄
                let path = std::env::temp_dir().join(unique).join("versions");
                std::fs::create_dir_all(&path).unwrap();
                Dir(path)
            }

            pub fn path(&self) -> &Path {
                &self.0
            }
        }

        impl Drop for Dir {
            fn drop(&mut self) {
                if let Some(parent) = self.0.parent() {
                    let _ = std::fs::remove_dir_all(parent);
                }
            }
        }
    }
}
