//! prim 参数 -> 屏幕矩阵，等价于 rfvp
//! `soft_render/renderer.rs:648 build_draw_model`。
//!
//! 参数单位（来自 rfvp syscall 层实锤）：
//!
//! - `x`, `y` : 整数像素（`graph.rs:740 prim_set_xy`；Nil = 保持）。
//! - `angle` : 十分之一度，`% 3600` 归一（`graph.rs:262 prim_set_rs`）。
//! - `factor_x/y` : 千分比，1000 = 100%（`RS2` 支持 x/y 独立）。
//! - `z` : 只在 `attr & 0x04 != 0` 时进入透视公式；**z 不做排序**
//!   （`graph.rs:878 prim_set_z` 及同文件注释）。
//! - pivot 双规则（`soft_render/renderer.rs:388`）：
//!   `attr & 0x02 != 0` 时取 `PrimSetOP` 的 `(opx, opy)`，
//!   否则取贴图自身的 `(u, v)` ——**默认不是图片中心**。
//!
//! 注意 z 分支与普通分支的一个不对称（原样保留）：
//! z 分支是 `T(center)·S·T(pos−cam)·R·T(local)`，
//! **不**把 pivot 加到 pos 上；普通分支是 `T(pos+pivot)·S·R·T(local)`。

use std::f32::consts::TAU;

use crate::affine::{Affine2, Pos2, Vec2};

/// 视口（rfvp `virtual_size`；FVP 系常用 800x600，预览窗可配）。
#[derive(Clone, Copy, Debug)]
pub struct Viewport {
    pub w: f32,
    pub h: f32,
}

/// V3D 相机（`v3d_x/v3d_y/v3d_z`，`build_draw_model` 的最后三个参数）。
#[derive(Clone, Copy, Debug, Default)]
pub struct Camera {
    pub x: i32,
    pub y: i32,
    pub z: i32,
}

/// 单个 prim 的结构化快照（A 轨 JSON 协议的 Rust 侧形态）。
#[derive(Clone, Copy, Debug)]
pub struct PrimParams {
    pub x: i32,
    pub y: i32,
    pub z: i32,
    /// 十分之一度（如 900 = 90°）。
    pub angle: i32,
    /// 千分比（1000 = 100%）。
    pub factor_x: i32,
    pub factor_y: i32,
    /// rfvp prim attr 位域：0x02 = OP-pivot，0x04 = z 透视分支。
    pub attr: i32,
    /// PrimSetOP 的旋转支点（仅 attr & 0x02 时生效）。
    pub opx: i32,
    pub opy: i32,
    /// 贴图自身偏移（attr & 0x02 未置位时的默认 pivot）。
    pub graph_u: i32,
    pub graph_v: i32,
    /// 贴图像素偏移（HZC 头 x/y 偏移 + 裁剪修正，rfvp `off_x/off_y`）。
    pub off_x: f32,
    pub off_y: f32,
}

impl Default for PrimParams {
    fn default() -> Self {
        Self {
            x: 0,
            y: 0,
            z: 0,
            angle: 0,
            factor_x: 1000,
            factor_y: 1000,
            attr: 0,
            opx: 0,
            opy: 0,
            graph_u: 0,
            graph_v: 0,
            off_x: 0.0,
            off_y: 0.0,
        }
    }
}

/// pivot 双规则（`renderer.rs:388`）。
pub fn resolve_pivot(p: &PrimParams) -> (f32, f32) {
    if (p.attr & 2) != 0 {
        (p.opx as f32, p.opy as f32)
    } else {
        (p.graph_u as f32, p.graph_v as f32)
    }
}

/// `build_draw_model` 的等价体（返回 2D 仿射部分；原函数返回的 `Mat4`
/// 在 z 行/列上恒为单位阵，故无信息损失）。
pub fn build_model(
    p: &PrimParams,
    parent_x: f32,
    parent_y: f32,
    cam: &Camera,
    viewport: &Viewport,
) -> Affine2 {
    let theta = -(p.angle as f32) * TAU / 3600.0;
    let pos_x = parent_x + p.x as f32;
    let pos_y = parent_y + p.y as f32;
    let (pivot_x, pivot_y) = resolve_pivot(p);
    let local = Vec2::new(p.off_x - pivot_x, p.off_y - pivot_y);

    if (p.attr & 4) != 0 {
        let center = Vec2::new(viewport.w * 0.5, viewport.h * 0.5);
        let mut fx = p.factor_x as f32;
        let mut fy = p.factor_y as f32;
        if fx.abs() < 1e-6 {
            fx = 1.0;
        }
        if fy.abs() < 1e-6 {
            fy = 1.0;
        }
        let mut depth = p.z as f32 - cam.z as f32;
        if depth.abs() < 1e-3 {
            depth = 1.0;
        }
        let s = Vec2::new(fx / depth, fy / depth);
        let cam_shift = Vec2::new(1000.0 * cam.x as f32 / fx, 1000.0 * cam.y as f32 / fy);
        Affine2::translate(center)
            * Affine2::scale(s)
            * Affine2::translate(Vec2::new(pos_x - cam_shift.x, pos_y - cam_shift.y))
            * Affine2::rotate(theta)
            * Affine2::translate(local)
    } else {
        let s = Vec2::new(p.factor_x as f32 / 1000.0, p.factor_y as f32 / 1000.0);
        Affine2::translate(Vec2::new(pos_x + pivot_x, pos_y + pivot_y))
            * Affine2::scale(s)
            * Affine2::rotate(theta)
            * Affine2::translate(local)
    }
}

/// 把本地矩形 `(0,0)-(w,h)` 四角打到屏幕上（对应 rfvp `transformed_quad`）。
pub fn quad_corners(
    p: &PrimParams,
    w: f32,
    h: f32,
    parent_x: f32,
    parent_y: f32,
    cam: &Camera,
    viewport: &Viewport,
) -> [Pos2; 4] {
    let m = build_model(p, parent_x, parent_y, cam, viewport);
    [
        m.transform_pos(Pos2::new(0.0, 0.0)),
        m.transform_pos(Pos2::new(w, 0.0)),
        m.transform_pos(Pos2::new(w, h)),
        m.transform_pos(Pos2::new(0.0, h)),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    const VP: Viewport = Viewport { w: 800.0, h: 600.0 };
    const CAM0: Camera = Camera { x: 0, y: 0, z: 0 };

    fn approx(a: Pos2, b: Pos2) -> bool {
        (a.x - b.x).abs() < 1e-3 && (a.y - b.y).abs() < 1e-3
    }

    fn base() -> PrimParams {
        PrimParams::default()
    }

    #[test]
    fn plain_branch_identity_maps_corner_to_pos() {
        // x=100,y=200, 无旋转, 100%, pivot/off 全 0: (10,20) -> (110,220)
        let mut p = base();
        p.x = 100;
        p.y = 200;
        let m = build_model(&p, 0.0, 0.0, &CAM0, &VP);
        assert!(approx(
            m.transform_pos(Pos2::new(10.0, 20.0)),
            Pos2::new(110.0, 220.0)
        ));
    }

    #[test]
    fn rotation_sign_matches_rfvp_negated_theta() {
        // angle=900 (90°), theta=-π/2: framebuffer 内 (10,0) -> (0,-10)。
        // （rfvp 用 glam y-up 约定再经 present 翻转，视觉上为屏幕顺时针。）
        let mut p = base();
        p.angle = 900;
        let m = build_model(&p, 0.0, 0.0, &CAM0, &VP);
        assert!(approx(
            m.transform_pos(Pos2::new(10.0, 0.0)),
            Pos2::new(0.0, -10.0)
        ));
    }

    #[test]
    fn pivot_rule_op_vs_graph_uv() {
        // 默认 pivot = (u,v)
        let mut p = base();
        p.graph_u = 7;
        p.graph_v = 9;
        assert_eq!(resolve_pivot(&p), (7.0, 9.0));
        // attr&0x02 -> pivot = (opx,opy)
        p.attr = 2;
        p.opx = 50;
        p.opy = 60;
        assert_eq!(resolve_pivot(&p), (50.0, 60.0));
    }

    #[test]
    fn pivot_shifts_local_origin_to_pos() {
        // pos=(100,100), pivot=(50,50), off=0: 本地 (0,0) -> (100,100)
        let mut p = base();
        p.x = 100;
        p.y = 100;
        p.attr = 2;
        p.opx = 50;
        p.opy = 50;
        let m = build_model(&p, 0.0, 0.0, &CAM0, &VP);
        assert!(approx(
            m.transform_pos(Pos2::ZERO),
            Pos2::new(100.0, 100.0)
        ));
    }

    #[test]
    fn z_branch_basic_projection() {
        // z=1100, cam.z=100 -> depth=1000, f=1000 -> s=1;
        // pos=(100,50) + center(400,300): (0,0) -> (500,350)
        let mut p = base();
        p.x = 100;
        p.y = 50;
        p.z = 1100;
        p.attr = 4;
        let cam = Camera { x: 0, y: 0, z: 100 };
        let m = build_model(&p, 0.0, 0.0, &cam, &VP);
        assert!(approx(
            m.transform_pos(Pos2::ZERO),
            Pos2::new(500.0, 350.0)
        ));
    }

    #[test]
    fn z_branch_camera_shift() {
        // cam.x=200, fx=1000 -> shift=200: (100,50)->(-100,50)->+(400,300)=(300,350)
        let mut p = base();
        p.x = 100;
        p.y = 50;
        p.z = 1100;
        p.attr = 4;
        let cam = Camera { x: 200, y: 0, z: 100 };
        let m = build_model(&p, 0.0, 0.0, &cam, &VP);
        assert!(approx(
            m.transform_pos(Pos2::ZERO),
            Pos2::new(300.0, 350.0)
        ));
    }

    #[test]
    fn z_branch_does_not_add_pivot_to_pos() {
        // 与普通分支的不对称（原样保留）：
        // z 分支 pivot=(50,50): local=(0,0)-(50,50)=(-50,-50),
        // T(pos=(100,50)) -> (50,0) -> +center = (450,300)
        let mut p = base();
        p.x = 100;
        p.y = 50;
        p.z = 1100;
        p.attr = 4 | 2;
        p.opx = 50;
        p.opy = 50;
        let cam = Camera { x: 0, y: 0, z: 100 };
        let m = build_model(&p, 0.0, 0.0, &cam, &VP);
        assert!(approx(
            m.transform_pos(Pos2::ZERO),
            Pos2::new(450.0, 300.0)
        ));
        // 同参数普通分支 (attr=2): T(pos+pivot=(150,100)) 作用于 (-50,-50) = (100,50)
        p.attr = 2;
        let m2 = build_model(&p, 0.0, 0.0, &CAM0, &VP);
        assert!(approx(
            m2.transform_pos(Pos2::ZERO),
            Pos2::new(100.0, 50.0)
        ));
    }

    #[test]
    fn z_branch_depth_guard() {
        // z == cam.z -> depth 按 1.0 算：s=1000, (1,0)->(1000,0)->+(400,300)
        let mut p = base();
        p.z = 500;
        p.attr = 4;
        let cam = Camera { x: 0, y: 0, z: 500 };
        let m = build_model(&p, 0.0, 0.0, &cam, &VP);
        assert!(approx(
            m.transform_pos(Pos2::new(1.0, 0.0)),
            Pos2::new(1400.0, 300.0)
        ));
    }

    #[test]
    fn z_branch_zero_factor_guard() {
        // fx=0 -> 按 1.0: s=1/1000, (1000,0)->(1,0)->+(400,300)=(401,300)
        let mut p = base();
        p.z = 1000;
        p.factor_x = 0;
        p.factor_y = 1000;
        p.attr = 4;
        let m = build_model(&p, 0.0, 0.0, &CAM0, &VP);
        assert!(approx(
            m.transform_pos(Pos2::new(1000.0, 0.0)),
            Pos2::new(401.0, 300.0)
        ));
    }

    #[test]
    fn parent_chain_accumulates() {
        // parent=(30,40) + prim(100,200): (0,0) -> (130,240)
        let mut p = base();
        p.x = 100;
        p.y = 200;
        let m = build_model(&p, 30.0, 40.0, &CAM0, &VP);
        assert!(approx(
            m.transform_pos(Pos2::ZERO),
            Pos2::new(130.0, 240.0)
        ));
    }

    #[test]
    fn plain_branch_scale_per_mille_and_rs2_asymmetry() {
        // fx=2000 -> x2; fy=500 -> x0.5
        let mut p = base();
        p.factor_x = 2000;
        p.factor_y = 500;
        let m = build_model(&p, 0.0, 0.0, &CAM0, &VP);
        assert!(approx(
            m.transform_pos(Pos2::new(10.0, 10.0)),
            Pos2::new(20.0, 5.0)
        ));
    }

    #[test]
    fn quad_corners_smoke() {
        // 100x50 矩形在 (100,200) 处：四角 = 偏移后的矩形
        let mut p = base();
        p.x = 100;
        p.y = 200;
        let c = quad_corners(&p, 100.0, 50.0, 0.0, 0.0, &CAM0, &VP);
        assert!(approx(c[0], Pos2::new(100.0, 200.0)));
        assert!(approx(c[1], Pos2::new(200.0, 200.0)));
        assert!(approx(c[2], Pos2::new(200.0, 250.0)));
        assert!(approx(c[3], Pos2::new(100.0, 250.0)));
    }
}
