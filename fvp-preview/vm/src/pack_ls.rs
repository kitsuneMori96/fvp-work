//! fvp-pack-ls: 列出 NVSG `.bin` 包内资源名（编辑器资源浏览器前置）。
//!
//! 用法: fvp-pack-ls <pack.bin> [prefix] [--limit N]

use rfvp::script::parser::Nls;
use rfvp::subsystem::resources::vfs::VfsFile;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("用法: {} <pack.bin> [prefix] [--limit N]", args[0]);
        std::process::exit(2);
    }
    let mut limit = 50usize;
    let mut prefix = String::new();
    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--limit" => {
                limit = args[i + 1].parse().expect("--limit 需要整数");
                i += 2;
            }
            s => {
                prefix = s.to_string();
                i += 1;
            }
        }
    }
    let path = std::path::PathBuf::from(&args[1]);
    let stem = path
        .file_stem()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let pack = VfsFile::new(path, stem, Nls::ShiftJIS).expect("解析包");
    let mut names: Vec<&String> = pack.entries.keys().filter(|k| k.starts_with(&prefix)).collect();
    names.sort();
    println!("entries_total={} shown={}", pack.entries.len(), names.len().min(limit));
    for n in names.into_iter().take(limit) {
        println!("{}", n);
    }
}
