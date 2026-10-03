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
use rfvp::soft_render::{PixelFormat, SoftRenderer};
use rfvp::subsystem::resources::prim::{PrimManager, PrimType};
use rfvp::subsystem::resources::thread_manager::ThreadManager;
use rfvp::subsystem::world::GameData;
use rfvp::vm_runner::VmRunner;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!(
            "用法: {} <scene.hcb> (--ticks N | --break-pc ADDR) [--png-dir DIR] [--viewport W H] [--ref-png PATH] [--dump-tree] [--trace-syscall]",
            args[0]
        );
        std::process::exit(2);
    }
    let hcb_path = args[1].clone();
    let mut ticks: usize = 200;
    let mut break_pc: Option<usize> = None;
    let mut png_dir = "/tmp/vmtex".to_string();
    let mut ref_png: Option<String> = None;
    let mut dump_tree = false;
    let mut trace_syscall = false;
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
            "--ref-png" => {
                ref_png = Some(args[i + 1].clone());
                i += 2;
            }
            "--dump-tree" => {
                dump_tree = true;
                i += 1;
            }
            "--trace-syscall" => {
                // 上游 crate::trace 经 RFVP_TRACE_SYSCALL=1 打出每条 syscall 名+实参（log::info→stderr）。
                // 给溯源用：动态 (name,args) + 静态反汇编 join，定位常量源 push 地址。
                trace_syscall = true;
                i += 1;
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
    if trace_syscall {
        std::env::set_var("RFVP_TRACE_SYSCALL", "1");
        // 上游经 log::info 打出：env_logger 默认 Error 会吞掉，需开 info（用户显式 RUST_LOG 优先）。
        if std::env::var("RUST_LOG").is_err() {
            std::env::set_var("RUST_LOG", "info");
        }
    }
    // 默认 Error 级别：不开 trace 时 stderr 保持干净（快照走 stdout 不受影响）。
    // 注意 env_logger 0.11 里 filter_level 会覆盖 RUST_LOG，所以 trace 时绝不能调它。
    if trace_syscall {
        env_logger::Builder::from_default_env().init();
    } else {
        env_logger::Builder::from_default_env()
            .filter_level(log::LevelFilter::Error)
            .init();
    }
    eprintln!(
        "[vm] trace自检 RFVP_TRACE_SYSCALL={:?} enabled={}",
        std::env::var("RFVP_TRACE_SYSCALL"),
        rfvp::trace::enabled(rfvp::trace::TraceKind::Syscall)
    );
    log::info!("[vm] 启动");

    let bytes = std::fs::read(&hcb_path).expect("读 HCB");
    let mut parser = Parser::from_bytes(bytes, Nls::ShiftJIS).expect("解析 HCB");
    let entry = parser.get_entry_point();
    eprintln!("[vm] entry={} ticks={} break_pc={:?}", entry, ticks, break_pc);

    let mut game = GameData::default();
    // headless 窗口预设游戏分辨率（真 HCB 的 Dissolve 等 syscall 会读窗口宽高；
    // 依赖 rfvp scratch patch 0001-window-headless，见本 crate rfvp-patches/）。
    game.window_mut()
        .set_dimensions(viewport.0 as u32, viewport.1 as u32);
    let mut tm = ThreadManager::new();
    tm.start_main(entry);
    let mut vm = VmRunner::new(tm);

    let mut stop_why = format!("ticks耗尽({})", ticks);
    for _ in 0..ticks {
        // 转场时钟：真机帧循环（anzu_scene::update_dissolve）每帧推进，
        // VmRunner::tick 只步进脚本。headless 下不手动带，转场永远冻在第一帧
        //（Dissolve/DissolveWait 脚本也会卡住）。16ms 与 tick 的帧预算对齐。
        // 依赖 rfvp scratch patch（motion_manager_mut，见 rfvp-patches/）。
        game.motion_manager_mut().tick_dissolve(16);
        game.motion_manager_mut().tick_dissolve2(16);
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
    {
        // 卡住时看谁还在 RUNNING、停在哪个 pc（对着反汇编即知等什么）。
        let t = vm.thread_manager();
        for (i, c) in t.contexts.iter().enumerate() {
            eprintln!("[vm] ctx#{} status={:?} pc={}", i, c.get_status(), c.get_pc());
        }
    }
    {
        // 转场状态自检：真机此刻画面是否还罩着 dissolve，一看便知。
        let m = game.motion_manager_ref();
        eprintln!(
            "[vm] dissolve type={:?} alpha={:.3} color_id={} dissolve2 alpha={:.3} color_id={}",
            m.get_dissolve_type(),
            m.get_dissolve_alpha(),
            m.get_dissolve_color_id(),
            m.get_dissolve2_alpha(),
            m.get_dissolve2_color_id(),
        );
    }

    // 真机参考图：同一 MotionManager 经 rfvp SoftRenderer 直出，
    // 与 A 轨快照渲染逐像素对照的标尺（真 HCB 调试头的关键证据）。
    if let Some(ref_path) = ref_png.as_ref() {
        let w = viewport.0 as u32;
        let h = viewport.1 as u32;
        let mut rdr = SoftRenderer::new(w, h, PixelFormat::Rgba8).expect("SoftRenderer::new");
        match rdr.render_frame(game.motion_manager_ref()) {
            Ok(fb) => {
                let px = fb.pixels().to_vec();
                match image::save_buffer(ref_path, &px, w, h, image::ColorType::Rgba8) {
                    Ok(()) => {
                        let st = rdr.stats();
                        eprintln!(
                            "[vm] 参考图 {} (quads={} draws={})",
                            ref_path, st.quad_count, st.draw_calls
                        );
                    }
                    Err(e) => eprintln!("[vm] 参考图保存失败: {}", e),
                }
            }
            Err(e) => eprintln!("[vm] 参考渲染失败: {}", e),
        }
    }

    let mm = game.motion_manager_ref();
    let pm = mm.prim_manager();
    if dump_tree {
        eprintln!(
            "[vm] prim树(截断):\n{}",
            pm.debug_dump_tree(0, 60, 8)
        );
    }
    // 绘制序 DFS（先序 = 真机绘制序）：first_child→next 链表，draw=false 连子树跳过，
    // Group/None 参与树形与 x/y 偏移（自身不画），Snow/Text/Tile 一期跳过自身但仍遍历孩子。
    // 背景：快照数组是 id 序，跨 Group 相对顺序与真机链表序不一致（真 HCB 实锤：白场 250
    // 与夜樱 258 分属不同 Group，id 序画反），必须按链表序重排，否则预览与真机上下盖反。
    // (base_id, draw_id, is_group)：挂 sprt 时画的是 draw_id，树归属与父链按 base 走。
    let mut order: Vec<(i16, i16, bool)> = Vec::new();
    {
        let mut roots = vec![0i16];
        let custom = pm.get_custom_root_prim_id() as i16;
        if custom != 0 {
            roots.push(custom);
        }
        for root in roots {
            // 真机 render_motion 每遍 new visit：遍间独立。
            let mut visited = vec![false; 4096usize];
            let mut stack = vec![root];
            while let Some(id) = stack.pop() {
                if id < 0 || id as usize >= visited.len() || visited[id as usize] {
                    continue;
                }
                visited[id as usize] = true;
                let base = pm.get_prim_immutable(id);
                if !base.get_draw_flag() {
                    continue;
                }
                // sprt 链追踪（同 render_tree；链上任一 draw=false 则整棵跳过）。
                let mut draw_id = id;
                let mut sprt = base.get_sprt();
                let mut chain_ok = true;
                while sprt != -1 {
                    if sprt < 0 || sprt as usize >= visited.len() {
                        chain_ok = false;
                        break;
                    }
                    let s = pm.get_prim_immutable(sprt);
                    if !s.get_draw_flag() {
                        chain_ok = false;
                        break;
                    }
                    draw_id = sprt;
                    sprt = s.get_sprt();
                }
                if !chain_ok {
                    continue;
                }
                let dp = pm.get_prim_immutable(draw_id);
                let is_sprt =
                    matches!(dp.get_type(), PrimType::PrimTypeSprt);
                let is_group = matches!(
                    dp.get_type(),
                    PrimType::PrimTypeGroup | PrimType::PrimTypeNone
                );
                if is_sprt || is_group {
                    order.push((id, draw_id, is_group));
                }
                // 孩子链取自 draw_id（同 render_tree），逆序压栈保链表序。
                let mut kids = Vec::new();
                let mut c = dp.get_first_child_idx();
                let mut steps = 0usize;
                while c != -1 && steps < 4096 {
                    steps += 1;
                    if c < 0 || c as usize >= visited.len() {
                        break;
                    }
                    kids.push(c);
                    c = pm.get_prim_immutable(c).get_next_sibling_idx();
                }
                for k in kids.into_iter().rev() {
                    stack.push(k);
                }
            }
        }
    }
    let snap = pm.capture_snapshot_v1();
    let graphs = mm.graphs();
    let (v3dx, v3dy, v3dz) = (mm.get_v3d_x(), mm.get_v3d_y(), mm.get_v3d_z());

    // 贴图导出（去重）：texture_id -> png 路径（按绘制序走，日志即画序）。
    let mut tex_path: HashMap<i16, String> = HashMap::new();
    let mut seen: HashSet<i16> = HashSet::new();
    for &(_base, draw_id, is_group) in &order {
        if is_group {
            continue;
        }
        let p = &snap.prims[draw_id as usize];
        // alpha==0 真机不遮挡也不可见：不导出（省纹理加载；占位顺序无意义）。
        if p.alpha == 0 {
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
        eprintln!("[vm] prim#{} texture g{} -> {} ({}x{})", draw_id, tid, path, g.get_width(), g.get_height());
        tex_path.insert(tid, path);
    }

    // 组 A 轨 JSON（字段名与 app 的 PrimSer 对齐；数组即绘制序，app 照序画）。
    let mut prims = Vec::new();
    for &(base, draw_id, is_group) in &order {
        if is_group {
            // Group 容器：只给树形+偏移，app 无 image 自动跳过绘制。
            let b = pm.get_prim_immutable(base);
            let parent = b.get_parent();
            prims.push(serde_json::json!({
                "id": base,
                "parent": if parent >= 0 { Some(parent) } else { None },
                "draw": true,
                "x": b.get_x(), "y": b.get_y(),
                "group": true,
            }));
            continue;
        }
        let p = &snap.prims[draw_id as usize];
        if p.alpha == 0 {
            continue;
        }
        let Some(image) = tex_path.get(&p.texture_id) else {
            continue;
        };
        let g = &graphs[p.texture_id as usize];
        // 父链按 base 走（挂 sprt 时画 draw_id 但归属 base；当前真包无挂载，分毫不差）。
        let bpar = pm.get_prim_immutable(base).get_parent();
        prims.push(serde_json::json!({
            "id": draw_id,
            "parent": if bpar >= 0 { Some(bpar) } else { None },
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
