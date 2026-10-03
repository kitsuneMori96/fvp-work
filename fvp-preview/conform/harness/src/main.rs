//! B轨 conformance harness（两轨，零 VFS/零窗口）。
//!
//! - v1 直接渲染：经公开(+3 个 conform-patch accessor)API 直搭
//!   MotionManager（prim 状态 + `GraphBuff::load_from_buff` 纹理），
//!   `SoftRenderer::render_frame` 出像素，与 core 预测逐像素比对。
//!   覆盖：真 `build_draw_model`（z 双分支/pivot 双规则/父链）。
//! - v2 真机 VM：`GameData::default + Parser + ThreadManager + VmRunner`
//!   跑无资源测试 HCB（state.hcb），读回 prim 全字段，验证 syscall 层语义
//!   （钳制/Nil保持/%3600/attr 位/父子链/V3D）。
//!
//! 用法: fvp-conform <testpix.png> <state.hcb> [--v1|--v2|--both]

use fvp_preview_core::affine::Pos2;
use fvp_preview_core::model::{self, Camera, PrimParams, Viewport};
use rfvp::script::parser::{Nls, Parser};
use rfvp::soft_render::{PixelFormat, SoftRenderer};
use rfvp::subsystem::resources::graph_buff::GraphBuff;
use rfvp::subsystem::resources::motion_manager::MotionManager;
use rfvp::subsystem::resources::prim::{PrimManager, PrimType};
use rfvp::subsystem::resources::thread_manager::ThreadManager;
use rfvp::subsystem::world::GameData;
use rfvp::vm_runner::VmRunner;

const VW: u32 = 800;
const VH: u32 = 600;

// ---------------------------------------------------------------------------
// 点-四边形（带边距），像素级断言用
// ---------------------------------------------------------------------------

fn point_in_quad(p: (f32, f32), q: &[Pos2; 4]) -> bool {
    // 四角按序(0,0)-(w,0)-(w,h)-(0,h)：凸四边形，同侧测试。
    let sign = |a: Pos2, b: Pos2| (p.0 - b.x) * (a.y - b.y) - (a.x - b.x) * (p.1 - b.y);
    let d = [
        sign(q[0], q[1]),
        sign(q[1], q[2]),
        sign(q[2], q[3]),
        sign(q[3], q[0]),
    ];
    (d[0] > 0.0 && d[1] > 0.0 && d[2] > 0.0 && d[3] > 0.0)
        || (d[0] < 0.0 && d[1] < 0.0 && d[2] < 0.0 && d[3] < 0.0)
}

fn dist_to_quad(p: (f32, f32), q: &[Pos2; 4]) -> f32 {
    // 点到四边距离（边线段距离最小值；内点返回负的到边最小距离）。
    let mut best = f32::INFINITY;
    for i in 0..4 {
        let a = q[i];
        let b = q[(i + 1) % 4];
        let abx = b.x - a.x;
        let aby = b.y - a.y;
        let t = (((p.0 - a.x) * abx + (p.1 - a.y) * aby) / (abx * abx + aby * aby))
            .clamp(0.0, 1.0);
        let dx = p.0 - (a.x + t * abx);
        let dy = p.1 - (a.y + t * aby);
        best = best.min((dx * dx + dy * dy).sqrt());
    }
    if point_in_quad(p, q) {
        -best
    } else {
        best
    }
}

// ---------------------------------------------------------------------------
// v1: 直接渲染 conformance
// ---------------------------------------------------------------------------

fn v1(png_path: &str) -> bool {
    // 纹理：PNG -> RGBA -> GraphBuff（load_from_buff：display=w/h, offset=0）。
    let img = image::open(png_path).expect("读 testpix").to_rgba8();
    let (tw, th) = (img.width(), img.height());
    assert!((tw, th) == (200, 150), "testpix 必须是 200x150");
    let tex_raw: Vec<u8> = img.clone().into_raw();

    let mut motion = MotionManager::new();
    let mut g = GraphBuff::new();
    g.load_from_buff(tex_raw.clone(), tw, th)
        .expect("load_from_buff");
    // 读回显示参数（零假设：预测全部来自 getter）。
    let (gw, gh, gu, gv, gox, goy) = (
        g.get_width() as f32,
        g.get_height() as f32,
        g.get_u() as f32,
        g.get_v() as f32,
        g.get_offset_x() as f32,
        g.get_offset_y() as f32,
    );
    println!(
        "v1 graph: {}x{} u={} v={} off=({},{})",
        gw, gh, gu, gv, gox, goy
    );
    // textures 预置 4097 空槽：Loaded graph 必须放进固定槽位（GraphLoad 语义），不能 push。
    motion.graphs_mut()[0] = g;
    println!(
        "v1 graphs={} tex_some={} w={}",
        motion.graphs().len(),
        motion.graphs()[0].get_texture().is_some(),
        motion.graphs()[0].get_width()
    );

    // prim 101：普通分支 + OP pivot + 90°（状态直设，attr 显式）。
    // prim 102：z分支 + 图 pivot（attr 无 0x02）+ RS2 非等比 + alpha 200。
    // prim 103：102 的子，普通分支。
    {
        let pm: &mut PrimManager = motion.prim_manager_mut();
        pm.set_prim_group_in(0, 18);
        pm.set_prim_group_in(18, 100);
        for (id, parent) in [(101i32, 18i32), (102, 18), (103, 100)] {
            pm.prim_init_with_type(id as i16, PrimType::PrimTypeSprt);
            pm.set_prim_group_in(parent, id);
            pm.prim_set_texture_id(id, 0);
            pm.prim_set_alpha(id, if id == 102 { 200 } else { 255 });
            pm.prim_set_draw(id, 1);
        }
        pm.prim_set_pos(101, 100, 120);
        pm.prim_set_rotation(101, 900);
        pm.prim_set_scale(101, 1000, 1000);
        pm.prim_set_op(101, 40, 30);
        pm.prim_set_attr(101, 0x02 | 0x40);
        pm.prim_set_pos(102, 0, 0);
        pm.prim_set_z(102, 1100);
        pm.prim_set_rotation(102, 0);
        pm.prim_set_scale(102, 2000, 500);
        pm.prim_set_attr(102, 0x04 | 0x40);
        pm.prim_set_pos(103, 150, 0);
        pm.prim_set_pos(100, 400, 50);
        pm.prim_set_rotation(103, 0);
        pm.prim_set_scale(103, 1000, 1000);
        pm.prim_set_attr(103, 0x40);
    }
    motion.set_v3d(0, 0, 100);

    println!("v1 tree:\n{}", motion.prim_manager().debug_dump_tree(0, 40, 6));

    let mut rdr = SoftRenderer::new(VW, VH, PixelFormat::Rgba8).expect("SoftRenderer::new");
    let px: Vec<u8> = {
        let fb = rdr.render_frame(&motion).expect("render_frame");
        let p = fb.pixels().to_vec();
        for (qx, qy) in [(100u32, 120u32), (200, 200), (500, 100), (0, 0)] {
            let o = ((qy * VW + qx) * 4) as usize;
            println!("v1 px({},{})={:?}", qx, qy, &p[o..o + 4]);
        }
        p
    };
    image::save_buffer(
        "/tmp/conform_frame.png",
        &px,
        VW,
        VH,
        image::ColorType::Rgba8,
    )
    .expect("存 conform_frame.png");
    let st = rdr.stats();
    println!("v1 stats: quads={} draws={}", st.quad_count, st.draw_calls);
    assert_eq!(px.len(), (VW * VH * 4) as usize);

    // 读回全部状态 → core 预测（零假设）。
    let vp = Viewport {
        w: VW as f32,
        h: VH as f32,
    };
    let cam = Camera { x: 0, y: 0, z: 100 };
    let pm = motion.prim_manager();
    // 父链：103 的父是容器 100（读回实测值，零假设）。
    let p100 = pm.get_prim_immutable(100);
    let acc103 = (p100.get_x() as f32, p100.get_y() as f32);
    drop(p100);

    // 模型 + 纹理双采样期望：逆变换回本地坐标 → 近邻纹素（3x3 均匀才判，
    // 十字线/边缘的双线性差异不纳入）→ gamma 混合公式（round，与真机一致）。
    let mut quads: Vec<(i32, fvp_preview_core::affine::Affine2, [Pos2; 4], u8)> = Vec::new();
    for (id, acc, alpha) in [(101, (0.0f32, 0.0f32), 255u8), (102, (0.0, 0.0), 200), (103, acc103, 255)] {
        let p = pm.get_prim_immutable(id as i16);
        let params = PrimParams {
            x: p.get_x() as i32,
            y: p.get_y() as i32,
            z: p.get_z() as i32,
            angle: p.get_angle() as i32,
            factor_x: p.get_factor_x() as i32,
            factor_y: p.get_factor_y() as i32,
            attr: p.get_attr() as i32,
            opx: p.get_opx() as i32,
            opy: p.get_opy() as i32,
            graph_u: gu as i32,
            graph_v: gv as i32,
            off_x: gox,
            off_y: goy,
        };
        drop(p);
        let m = model::build_model(&params, acc.0, acc.1, &cam, &vp);
        let minv = m.inverse().expect("模型可逆");
        let q = model::quad_corners(&params, gw, gh, acc.0, acc.1, &cam, &vp);
        println!(
            "v1 prim#{} pred quad=({:.1},{:.1}) ({:.1},{:.1}) ({:.1},{:.1}) ({:.1},{:.1})",
            id, q[0].x, q[0].y, q[1].x, q[1].y, q[2].x, q[2].y, q[3].x, q[3].y
        );
        quads.push((id, minv, q, alpha));
    }

    // 纹理采样：近邻 + 3x3 均匀性。
    let texel = |lx: f32, ly: f32| -> Option<[u8; 4]> {
        let (ix, iy) = (lx.floor() as i32, ly.floor() as i32);
        if ix < 1 || iy < 1 || ix + 1 >= tw as i32 || iy + 1 >= th as i32 {
            return None;
        }
        let at = |x: i32, y: i32| -> [u8; 4] {
            let o = ((y as u32 * tw + x as u32) * 4) as usize;
            [tex_raw[o], tex_raw[o + 1], tex_raw[o + 2], tex_raw[o + 3]]
        };
        let c = at(ix, iy);
        for dy in -1..=1 {
            for dx in -1..=1 {
                if at(ix + dx, iy + dy) != c {
                    return None; // 十字线/边缘：双线性与近邻必有分歧，跳过
                }
            }
        }
        Some(c)
    };

    // 逐像素断言：quad 内（边距 2.5px）== 采样期望（容差±2）；
    // quad 外（边距 2px）== 黑。各 quad 无重叠，绘制序无关。
    let mut fails = 0;
    let mut checked_in = 0;
    let mut skipped_tex = 0;
    let mut fail_by_prim = [0, 0, 0];
    for y in 0..VH {
        for x in 0..VW {
            let o = ((y * VW + x) * 4) as usize;
            let got = [px[o], px[o + 1], px[o + 2], px[o + 3]];
            let pt = (x as f32 + 0.5, y as f32 + 0.5);
            let mut owner: Option<usize> = None;
            let mut outside_all = true;
            for (i, (_, _, q, _)) in quads.iter().enumerate() {
                let d = dist_to_quad(pt, q);
                if d < -2.5 {
                    owner = Some(i);
                    outside_all = false;
                    break;
                }
                if d < 2.0 {
                    outside_all = false; // 边缘带：不判
                }
            }
            if let Some(i) = owner {
                let (_, minv, _, alpha) = &quads[i];
                let lp = minv.transform_pos(Pos2::new(pt.0, pt.1));
                let Some(t) = texel(lp.x, lp.y) else {
                    skipped_tex += 1;
                    continue;
                };
                checked_in += 1;
                // gamma 混合（黑底）+ round：与真机 blend_pixel 同式。
                let a = *alpha as f32 / 255.0;
                let want = [
                    (t[0] as f32 * a).round() as u8,
                    (t[1] as f32 * a).round() as u8,
                    (t[2] as f32 * a).round() as u8,
                    255u8,
                ];
                let close = got
                    .iter()
                    .zip(want.iter())
                    .all(|(a, b)| (*a as i32 - *b as i32).abs() <= 2);
                if !close {
                    if fails < 8 {
                        println!(
                            "v1 FAIL pixel ({},{}) prim#{} got={:?} want={:?} local=({:.1},{:.1})",
                            x, y, quads[i].0, got, want, lp.x, lp.y
                        );
                    }
                    fails += 1;
                    fail_by_prim[i] += 1;
                }
            } else if outside_all && got != [0, 0, 0, 255] {
                if fails < 8 {
                    println!("v1 FAIL bg pixel ({},{}) got={:?} want=black", x, y, got);
                }
                fails += 1;
            }
        }
    }
    println!(
        "v1 interior_pixels_checked={} skipped_nonuniform={} fails={}",
        checked_in, skipped_tex, fails
    );
    println!("v1 fail_by_prim={:?}", fail_by_prim);
    if fails > 0 {
        println!("V1-FAIL");
        return false;
    }
    println!("V1-PASS");
    true
}

// ---------------------------------------------------------------------------
// v2: 真机 VM syscall 状态 conformance（无资源：脚本不含 GraphLoad）
// ---------------------------------------------------------------------------

struct Expect {
    id: i16,
    x: i16,
    y: i16,
    z: i16,
    angle: i16,
    fx: i16,
    fy: i16,
    attr: u32,
    opx: i16,
    opy: i16,
    w: i16,
    h: i16,
    alpha: u8,
    draw: bool,
    parent: i16,
    tex: i16,
    sprt: i16,
}

/// 可比对的 prim 状态快照（16 元组超 Rust trait 上限，改 struct）。
#[derive(Debug, PartialEq)]
struct StateSnap {
    x: i16,
    y: i16,
    z: i16,
    angle: i16,
    fx: i16,
    fy: i16,
    attr: u32,
    opx: i16,
    opy: i16,
    w: i16,
    h: i16,
    alpha: u8,
    draw: bool,
    parent: i16,
    tex: i16,
    sprt: i16,
}

fn v2(hcb_path: &str) -> bool {
    let bytes = std::fs::read(hcb_path).expect("读 state.hcb");
    let mut parser = Parser::from_bytes(bytes, Nls::ShiftJIS).expect("parse HCB");
    let entry = parser.get_entry_point();
    println!("v2 entry_point={}", entry);

    let mut game = GameData::default();
    let mut tm = ThreadManager::new();
    tm.start_main(entry);
    let mut vm = VmRunner::new(tm);
    for _ in 0..60 {
        vm.tick(&mut game, &mut parser, 16).expect("vm tick");
    }

    let mm = game.motion_manager_ref();
    let pm = mm.prim_manager();
    let expects = vec![
        Expect { id: 100, x: 400, y: 50, z: 0, angle: 0, fx: 1000, fy: 1000,
                 attr: 0x40, opx: 0, opy: 0, w: 0, h: 0, alpha: 0, draw: true,
                 parent: 18, tex: 0, sprt: -1 },
        Expect { id: 101, x: 100, y: 120, z: 1000, angle: 900, fx: 1000, fy: 1000,
                 attr: 0x42, opx: 40, opy: 30, w: 0, h: 0, alpha: 255, draw: true,
                 parent: 18, tex: 101, sprt: -1 },
        Expect { id: 102, x: 0, y: 0, z: 1100, angle: 0, fx: 2000, fy: 500,
                 attr: 0x44, opx: 0, opy: 0, w: 0, h: 0, alpha: 200, draw: true,
                 parent: 18, tex: 102, sprt: -1 },
        Expect { id: 103, x: 150, y: 0, z: 1000, angle: 0, fx: 1000, fy: 1000,
                 attr: 0x41, opx: 0, opy: 0, w: 200, h: 150, alpha: 255, draw: true,
                 parent: 100, tex: 103, sprt: -1 },
    ];
    let mut fails = 0;
    for e in &expects {
        let p = pm.get_prim_immutable(e.id);
        let got = StateSnap {
            x: p.get_x(), y: p.get_y(), z: p.get_z(), angle: p.get_angle(),
            fx: p.get_factor_x(), fy: p.get_factor_y(), attr: p.get_attr(),
            opx: p.get_opx(), opy: p.get_opy(), w: p.get_w(), h: p.get_h(),
            alpha: p.get_alpha(), draw: p.get_draw_flag(), parent: p.get_parent(),
            tex: p.get_texture_id(), sprt: p.get_sprt(),
        };
        let want = StateSnap {
            x: e.x, y: e.y, z: e.z, angle: e.angle, fx: e.fx, fy: e.fy,
            attr: e.attr, opx: e.opx, opy: e.opy, w: e.w, h: e.h, alpha: e.alpha,
            draw: e.draw, parent: e.parent, tex: e.tex, sprt: e.sprt,
        };
        drop(p);
        if got != want {
            println!("v2 FAIL prim#{}:\n  got ={:?}\n  want={:?}", e.id, got, want);
            fails += 1;
        } else {
            println!("v2 PASS prim#{}", e.id);
        }
    }
    let (vx, vy, vz) = (mm.get_v3d_x(), mm.get_v3d_y(), mm.get_v3d_z());
    if (vx, vy, vz) != (0, 0, 100) {
        println!("v2 FAIL v3d got=({},{},{}) want=(0,0,100)", vx, vy, vz);
        fails += 1;
    } else {
        println!("v2 PASS v3d=(0,0,100)");
    }
    if fails > 0 {
        println!("V2-FAIL({})", fails);
        return false;
    }
    println!("V2-PASS");
    true
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!("用法: {} <testpix.png> <state.hcb> [--v1|--v2|--both]", args[0]);
        std::process::exit(2);
    }
    let mode = args.get(3).cloned().unwrap_or_else(|| "--both".to_string());
    let mut ok = true;
    if mode == "--v1" || mode == "--both" {
        ok &= v1(&args[1]);
    }
    if mode == "--v2" || mode == "--both" {
        ok &= v2(&args[2]);
    }
    if !ok {
        std::process::exit(1);
    }
    println!("CONFORM-PASS");
}
