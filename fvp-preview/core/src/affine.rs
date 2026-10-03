//! 2D 仿射数学，搬运自 rfvp `rendering/render_tree.rs:8-91`。
//!
//! 坐标系：屏幕空间，y-down（与 rfvp framebuffer 一致；
//! 最终 present 时的 y 翻转由显示层负责，不在本层处理）。

use std::ops::Mul;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Vec2 {
    pub x: f32,
    pub y: f32,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Pos2 {
    pub x: f32,
    pub y: f32,
}

impl Vec2 {
    pub fn new(x: f32, y: f32) -> Self {
        Self { x, y }
    }

    pub fn splat(v: f32) -> Self {
        Self { x: v, y: v }
    }

    pub const ZERO: Self = Self { x: 0.0, y: 0.0 };
}

impl Pos2 {
    pub fn new(x: f32, y: f32) -> Self {
        Self { x, y }
    }

    pub const ZERO: Self = Self { x: 0.0, y: 0.0 };
}

#[derive(Clone, Copy, Debug)]
pub struct Transform2D {
    pub position: Vec2, // screen-space, px
    pub scale: Vec2,    // per-axis scale
    pub rotation: f32,  // radians, clockwise (screen y-down)
    pub pivot: Vec2,    // pivot in local px coordinates
}

impl Transform2D {
    pub fn new(position: Vec2) -> Self {
        Self {
            position,
            scale: Vec2::splat(1.0),
            rotation: 0.0,
            pivot: Vec2::ZERO,
        }
    }

    pub fn to_affine(&self) -> Affine2 {
        // Affine = T(pos) * T(pivot) * R(rot) * S(scale) * T(-pivot)
        Affine2::translate(self.position)
            * Affine2::translate(self.pivot)
            * Affine2::rotate(self.rotation)
            * Affine2::scale(self.scale)
            * Affine2::translate(Vec2::new(-self.pivot.x, -self.pivot.y))
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Affine2 {
    // 2x3 affine matrix: [ m11 m12 | tx ]
    //                    [ m21 m22 | ty ]
    pub m11: f32,
    pub m12: f32,
    pub m21: f32,
    pub m22: f32,
    pub tx: f32,
    pub ty: f32,
}

impl Affine2 {
    pub fn identity() -> Self {
        Self {
            m11: 1.0,
            m12: 0.0,
            m21: 0.0,
            m22: 1.0,
            tx: 0.0,
            ty: 0.0,
        }
    }

    pub fn translate(v: Vec2) -> Self {
        Self {
            tx: v.x,
            ty: v.y,
            ..Self::identity()
        }
    }

    pub fn scale(v: Vec2) -> Self {
        Self {
            m11: v.x,
            m22: v.y,
            ..Self::identity()
        }
    }

    pub fn rotate(rad: f32) -> Self {
        // screen y-down → clockwise rotation has positive angle
        let c = rad.cos();
        let s = rad.sin();
        // standard 2D rotation matrix (assuming y-up) is [c -s; s c]
        // For y-down screen space, this still gives the desired visual CW rotation.
        Self {
            m11: c,
            m12: -s,
            m21: s,
            m22: c,
            ..Self::identity()
        }
    }

    pub fn transform_pos(&self, p: Pos2) -> Pos2 {
        Pos2::new(
            self.m11 * p.x + self.m12 * p.y + self.tx,
            self.m21 * p.x + self.m22 * p.y + self.ty,
        )
    }

    /// 逆矩阵（行列式为 0 时返回 None；预览窗把屏幕点映回本地坐标用）。
    pub fn inverse(&self) -> Option<Self> {
        let det = self.m11 * self.m22 - self.m12 * self.m21;
        if det.abs() < 1e-12 {
            return None;
        }
        let inv = 1.0 / det;
        let m11 = self.m22 * inv;
        let m12 = -self.m12 * inv;
        let m21 = -self.m21 * inv;
        let m22 = self.m11 * inv;
        let tx = -(m11 * self.tx + m12 * self.ty);
        let ty = -(m21 * self.tx + m22 * self.ty);
        Some(Self {
            m11,
            m12,
            m21,
            m22,
            tx,
            ty,
        })
    }
}

impl Mul for Affine2 {
    type Output = Self;

    /// Compose: `(a * b).transform(p) == a.transform(b.transform(p))`
    /// (rightmost applies first; matches glam `Mat4` mul semantics used by rfvp).
    fn mul(self, rhs: Self) -> Self::Output {
        Self {
            m11: self.m11 * rhs.m11 + self.m12 * rhs.m21,
            m12: self.m11 * rhs.m12 + self.m12 * rhs.m22,
            m21: self.m21 * rhs.m11 + self.m22 * rhs.m21,
            m22: self.m21 * rhs.m12 + self.m22 * rhs.m22,
            tx: self.m11 * rhs.tx + self.m12 * rhs.ty + self.tx,
            ty: self.m21 * rhs.tx + self.m22 * rhs.ty + self.ty,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: Pos2, b: Pos2) -> bool {
        (a.x - b.x).abs() < 1e-4 && (a.y - b.y).abs() < 1e-4
    }

    #[test]
    fn mul_applies_rightmost_first() {
        let t = Affine2::translate(Vec2::new(10.0, 20.0));
        let s = Affine2::scale(Vec2::new(2.0, 3.0));
        let p = Pos2::new(1.0, 1.0);
        // (T * S)(p) == T(S(p)) == (12, 23)
        assert!(approx(
            (t * s).transform_pos(p),
            Pos2::new(12.0, 23.0)
        ));
        // (S * T)(p) == S(T(p)) == (22, 63)
        assert!(approx(
            (s * t).transform_pos(p),
            Pos2::new(22.0, 63.0)
        ));
    }

    #[test]
    fn inverse_roundtrips() {
        let m = Affine2::translate(Vec2::new(100.0, -50.0))
            * Affine2::rotate(0.7)
            * Affine2::scale(Vec2::new(2.0, 0.5));
        let inv = m.inverse().expect("可逆");
        let p = Pos2::new(13.0, 77.0);
        assert!(approx(inv.transform_pos(m.transform_pos(p)), p));
        // 退化矩阵不可逆
        assert!(Affine2::scale(Vec2::new(0.0, 1.0)).inverse().is_none());
    }

    #[test]
    fn rotate_90deg_maps_right_to_down_in_y_down() {
        // +90° in screen y-down space: (10,0) -> (0,10) visually downwards.
        let r = Affine2::rotate(std::f32::consts::FRAC_PI_2);
        assert!(approx(r.transform_pos(Pos2::new(10.0, 0.0)), Pos2::new(0.0, 10.0)));
    }
}
