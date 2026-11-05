use std::fs::OpenOptions;
use std::time::{Duration, Instant};
use crate::paths;
use crate::safety;

pub struct ApplyLockGuard {
    file: std::fs::File,
}

impl Drop for ApplyLockGuard {
    fn drop(&mut self) {
        unlock_file(&self.file);
    }
}

pub fn apply_lock(timeout: Duration) -> Result<ApplyLockGuard, String> {
    let backup_base = paths::backup_base().ok_or_else(|| "Cannot determine backup base".to_string())?;
    safety::assert_root_not_reparse(&backup_base, "backup root")?;
    std::fs::create_dir_all(&backup_base)
        .map_err(|e| format!("Failed to create backup base {}: {e}", backup_base.display()))?;
    safety::assert_root_not_reparse(&backup_base, "backup root")?;

    let lock_path = backup_base.join(".ai-config-backup.lock");
    safety::assert_safe_write_target(&lock_path)?;

    #[allow(unused_mut)]
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path)
        .map_err(|e| format!("Failed to open lock file {}: {e}", lock_path.display()))?;

    #[cfg(windows)]
    {
        use std::io::Write;
        if let Ok(meta) = file.metadata() {
            if meta.len() == 0 {
                let _ = file.write_all(b"\0");
                let _ = file.flush();
            }
        }
    }

    let deadline = Instant::now() + timeout;
    loop {
        if try_lock_file(&file) {
            return Ok(ApplyLockGuard { file });
        }
        if Instant::now() >= deadline {
            return Err(format!("Timed out waiting for apply lock: {}", lock_path.display()));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[cfg(unix)]
fn try_lock_file(file: &std::fs::File) -> bool {
    use std::os::unix::io::AsRawFd;
    let fd = file.as_raw_fd();
    let ret = unsafe { libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) };
    ret == 0
}

#[cfg(unix)]
fn unlock_file(file: &std::fs::File) {
    use std::os::unix::io::AsRawFd;
    let fd = file.as_raw_fd();
    let _ = unsafe { libc::flock(fd, libc::LOCK_UN) };
}

#[cfg(windows)]
mod win_lock {
    use std::os::windows::io::{AsRawHandle, RawHandle};
    const LOCKFILE_EXCLUSIVE_LOCK: u32 = 0x0000_0002;
    const LOCKFILE_FAIL_IMMEDIATELY: u32 = 0x0000_0001;

    #[repr(C)]
    struct OVERLAPPED {
        internal: usize,
        internal_high: usize,
        offset: u32,
        offset_high: u32,
        h_event: *mut std::ffi::c_void,
    }

    extern "system" {
        fn LockFileEx(
            hFile: RawHandle,
            dwFlags: u32,
            dwReserved: u32,
            nNumberOfBytesToLockLow: u32,
            nNumberOfBytesToLockHigh: u32,
            lpOverlapped: *mut OVERLAPPED,
        ) -> i32;
        fn UnlockFileEx(
            hFile: RawHandle,
            dwReserved: u32,
            nNumberOfBytesToUnlockLow: u32,
            nNumberOfBytesToUnlockHigh: u32,
            lpOverlapped: *mut OVERLAPPED,
        ) -> i32;
    }

    pub fn try_lock_file(file: &std::fs::File) -> bool {
        let handle = file.as_raw_handle();
        let mut overlapped: OVERLAPPED = unsafe { std::mem::zeroed() };
        let ret = unsafe {
            LockFileEx(
                handle,
                LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
                0,
                1,
                0,
                &mut overlapped,
            )
        };
        ret != 0
    }

    pub fn unlock_file(file: &std::fs::File) {
        let handle = file.as_raw_handle();
        let mut overlapped: OVERLAPPED = unsafe { std::mem::zeroed() };
        let _ = unsafe { UnlockFileEx(handle, 0, 1, 0, &mut overlapped) };
    }
}

#[cfg(windows)]
use win_lock::{try_lock_file, unlock_file};
