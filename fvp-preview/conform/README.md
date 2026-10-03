# conform — rfvp 语义对照（B 轨地基）

证明 `fvp-preview-core` 的坐标数学与真 rfvp **零漂移**：
- **v1 直接渲染**：经公开（+3 个 conform-patch accessor）API 直搭
  `MotionManager`，真 `SoftRenderer::render_frame` 出像素，与 core 预测逐像素比对
  （逆变换＋纹理近邻＋3x3 均匀过滤＋gamma 混合同式，边距外不判）。
- **v2 真机 VM**：`GameData::default + Parser + ThreadManager + VmRunner`
  跑无资源测试 HCB，真 syscall 全字段读回比对（含 attr 位、钳制、Nil 保持、
  `%3600`、父子链、V3D）。

## 目录布局要求

```
<Code>/
├── fvp-work/            # 本仓
└── rfvp-upstream/       # 上游稀疏检出（见 setup-rfvp.sh；或同名 symlink）
```

`harness/Cargo.toml` 用相对路径同时引用两者；`conform/` 自带独立 workspace，
**不**挂进 `fvp-preview` 主 workspace（没配好上游时主仓照常构建）。

## 上手

```bash
# 1. bootstrap 上游（clone/稀疏检出/patch/字体桩/工具链构建）
bash fvp-work/fvp-preview/conform/setup-rfvp.sh   # 在 <Code>/ 下运行

# 2.（可选）重建测试场景；仓内已附带可直接用的 state.hcb + testpix.png
python3 scenes/gen_state_scene.py --out-dir /tmp/scene
<rfvp-upstream>/target/debug/assembler --project-dir /tmp/scene \
  --output /tmp/scene/state.hcb --nls sjis

# 3. 跑对照
cd fvp-work/fvp-preview/conform
cargo run -p fvp-conform -- scenes/testpix.png scenes/state.hcb --both
# 期望尾行：CONFORM-PASS
```

## 判定口径

- v1：内点（边距 2.5px、均匀纹理区）零失败，容差 ±2（舍入）；边缘/十字线跳过计数可见。
- v2：4 prim 全 16 字段＋V3D 逐字节一致。
- 任何 FAIL 都是真漂移（或测试场景写错），先看 harness 打印的预测 quad 与树 dump。

## 抓到的真机语义（已验证，回写到 core 文档与 HCB 编辑器注意事项）

1. **Group 化清 x/y**：`prim_init_with_type(Group)` 置零 x/y —— 容器偏移必须在最后一次
   Group 化之后设置（harness 曾两次踩中，已修正场景顺序）。
2. **PrimSetSprt 重置一切**：pos/z/rot/scale/uv/size/alpha/blend 回初值（含 z→1000），
   attr 清零；sprt 字段本身保持 -1（纹理走 texture_id）。
3. **textures 预置 4097 空槽**：`MotionManager::new()` 自带空 GraphBuff，纹理必须按槽位放，
   不能 push（GraphLoad 语义即按槽位）。
4. 默认 z=0（Sprt 会设成 1000）；默认 factor=1000；`build_draw_model` 私有，对照只能端到端像素。
