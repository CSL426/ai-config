//! Give the Windows launcher the app icon: shortcuts point at it, and
//! without one they show the blank default.

fn main() {
    println!("cargo:rerun-if-changed=launcher.rc");
    println!("cargo:rerun-if-changed=../assets/icon.ico");
    // 只在發佈用的 MSVC 組建嵌入;其他目標(本機交叉檢查)沒有資源編譯器
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows")
        && std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc")
    {
        embed_resource::compile("launcher.rc", embed_resource::NONE)
            .manifest_optional()
            .unwrap();
    }
}
