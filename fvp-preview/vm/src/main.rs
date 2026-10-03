//! fvp-preview-vm: B 轨产品（v1：ticks/断点 → 快照 → 预览 JSON）。
//!
//! 把真 HCB 跑进 rfvp VM，停下后把 prim 树 + 贴图导出成 A 轨快照 JSON，
//! 直接管道给 `fvp-preview` 渲染：
//!
//! ```sh
//! FVP_BASE_PATH=/path/to/game fvp-preview-vm scene.hcb --ticks 200 \
//!   --png-dir /tmp/vmtex | fvp-preview
//! ```
//!
//! - `FVP_BASE_PATH`: 游戏资源根（`.bin` 包所在目录；GraphLoad 经 VFS 解包）。
//! - 停止条件：`--ticks N` 跑满 N 帧，或任一 context 到达 `--break-pc ADDR`，
//!   或所有 context 都不再 RUNNING（等待输入/结束），取先到者。
//! - 只导出 `typ==Sprt(4)` 且 `draw` 且贴图可解的 prim（文本/视频二期）。
//! -  viewport 默认 800x600（FVP 系常用；z 分支投影中心依赖它，可配）。

use std::collections::{HashMap, HashSet};

use rfvp::script::parser::{Nls, Parser};
use rfvp::subsystem::resources::thread_manager::ThreadManager;
use rfvp::subsystem::world::GameData;
use rfvp::vm_runner::VmRunner;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!(
            "用法: {} <scene.hcb> (--ticks N | --break-pc ADDR) [--png-dir DIR] [--viewport W H]",
            args[0]
        );
        std::process::exit(2);
    }
    let hcb_path = args[1].clone();
    let mut ticks: usize = 200;
    let mut break_pc: Option<usize> = None;
    let mut png_dir = "/tmp/vmtex".to_string();
    // 游戏原生分辨率（樱萌放 1280x720，VNDB+官网 spec 实锤；z 分支投影中心依赖它）。
    let mut viewport = (1280.0f32, 720.0f32);
    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--ticks" => {
                ticks = args[i + 1].parse().expect("--ticks 需要整数");
                i += 2;
            }
            "--break-pc" => {
                let s = args[i + 1].clone();
                break_pc = Some(
                    s.strip_prefix("0x")
                        .map(|h| usize::from_str_radix(h, 16).expect("hex"))
                        .unwrap_or_else(|| s.parse().expect("十进制地址")),
                );
                i += 2;
            }
            "--png-dir" => {
                png_dir = args[i + 1].clone();
                i += 2;
            }
            "--viewport" => {
                viewport = (
                    args[i + 1].parse().expect("W"),
                    args[i + 2].parse().expect("H"),
                );
                i += 3;
            }
            other => {
                eprintln!("未知参数: {}", other);
                std::process::exit(2);
            }
        }
    }
    std::fs::create_dir_all(&png_dir).expect("建 png-dir");

    let bytes = std::fs::read(&hcb_path).expect("读 HCB");
    let mut parser = Parser::from_bytes(bytes, Nls::ShiftJIS).expect("解析 HCB");
    let entry = parser.get_entry_point();
    eprintln!("[vm] entry={} ticks={} break_pc={:?}", entry, ticks, break_pc);

    let mut game = GameData::default();
    let mut tm = ThreadManager::new();
    tm.start_main(entry);
    let mut vm = VmRunner::new(tm);

    let mut stop_why = format!("ticks耗尽({})", ticks);
    for _ in 0..ticks {
        if let Err(e) = vm.tick(&mut game, &mut parser, 16) {
            eprintln!("[vm] tick 错误（脚本可能已结束或遇到未打桩syscall）: {}", e);
            stop_why = format!("tick错误: {}", e);
            break;
        }
        let t = vm.thread_manager();
        if let Some(pc) = break_pc {
            // 注意：一个 tick 跑多条指令，只能按“首次越过”判停，不能按精确相等
            //（精确单步需要 context_dispatch_opcode 逐条驱动，二期）。
            if t.contexts.iter().any(|c| c.get_pc() >= pc) {
                stop_why = format!("越过 break-pc {}（首次）", pc);
                break;
            }
        }
        // 无 RUNNING context（等待输入/全部结束）：状态已稳定。
        let any_running = t.contexts.iter().any(|c| {
            c.get_status()
                .contains(rfvp::script::context::ThreadState::CONTEXT_STATUS_RUNNING)
        });
        if !any_running {
            stop_why = "无RUNNING context（稳定）".to_string();
            break;
        }
    }
    eprintln!("[vm] 停止：{}", stop_why);

    let mm = game.motion_manager_ref();
    let pm = mm.prim_manager();
    let snap = pm.capture_snapshot_v1();
    let graphs = mm.graphs();
    let (v3dx, v3dy, v3dz) = (mm.get_v3d_x(), mm.get_v3d_y(), mm.get_v3d_z());

    // 贴图导出（去重）：texture_id -> png 路径。
    let mut tex_path: HashMap<i16, String> = HashMap::new();
    let mut seen: HashSet<i16> = HashSet::new();
    for (id, p) in snap.prims.iter().enumerate() {
        if !p.draw_flag || p.typ != 4 {
            continue;
        }
        let tid = p.texture_id;
        if tid < 0 || !seen.insert(tid) {
            continue;
        }
        let Some(g) = graphs.get(tid as usize) else {
            continue;
        };
        let Some(img) = g.get_texture().as_ref() else {
            continue;
        };
        let path = format!("{}/g{}.png", png_dir, tid);
        // DynamicImage 与本 crate image(=0.24.9) 同实例，直接存。
        if let Err(e) = img.save(&path) {
            eprintln!("[vm] 贴图导出失败 g{}: {}", tid, e);
            continue;
        }
        eprintln!("[vm] prim#{} texture g{} -> {} ({}x{})", id, tid, path, g.get_width(), g.get_height());
        tex_path.insert(tid, path);
    }

    // 组 A 轨 JSON（字段名与 app 的 PrimSer 对齐）。
    let mut prims = Vec::new();
    for (id, p) in snap.prims.iter().enumerate() {
        if !p.draw_flag || p.typ != 4 {
            continue;
        }
        let Some(image) = tex_path.get(&p.texture_id) else {
            continue;
        };
        let g = &graphs[p.texture_id as usize];
        prims.push(serde_json::json!({
            "id": id,
            "parent": if p.parent >= 0 { Some(p.parent) } else { None },
            "draw": true,
            "x": p.x, "y": p.y, "z": p.z,
            "angle": p.rotation,
            "fx": p.factor_x, "fy": p.factor_y,
            "attr": p.attr,
            "opx": p.opx, "opy": p.opy,
            "u": g.get_u(), "v": g.get_v(),
            "off_x": g.get_offset_x() as f32,
            "off_y": g.get_offset_y() as f32,
            "alpha": p.alpha,
            "image": image,
        }));
    }
    let out = serde_json::json!({
        "viewport": {"w": viewport.0, "h": viewport.1},
        "camera": {"x": v3dx, "y": v3dy, "z": v3dz},
        "prims": prims,
    });

    println!("{}", out.to_string());
    eprintln!("[vm] 快照 prim={} (draw+sprt+贴图可解)", prims.len());
}
