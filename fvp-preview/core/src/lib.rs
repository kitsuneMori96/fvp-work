//! HCB 图片预览窗.
//!
//! 三块之一：坐标语义层.
//!
//! 本 crate 是 rfvp 渲染数学的提纯复刻（只做"位置/旋转/缩放一致"，
//! 像素级效果如抗锯齿/插值/NVSG tile 拼贴明确不在范围内）：
//!
//! - [`affine`] : `Transform2D` / `Affine2`，原样搬运自
//!   rfvp `crates/rfvp/src/rendering/render_tree.rs:8-91`。
//! - [`model`] : `build_model`，等价于 rfvp
//!   `crates/rfvp/src/soft_render/renderer.rs:648 build_draw_model`，
//!   即"HCB 指令参数 -> 屏幕坐标"的那一段（含 z 透视分支、pivot 双规则）。
//!
//! 约束：预览渲染与 rfvp 运行时共用**同一份**转换语义。
//! 任何与上游行为的偏离都必须先在单测里暴露出来（见 `model.rs` 末尾单测）。

pub mod affine;
pub mod model;
