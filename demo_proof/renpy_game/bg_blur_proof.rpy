# bg_blur_proof.rpy — Ren'Py 引擎复现脚本
#
# 证明目标 (对应 Sakura_dump.txt:function_1064_ / Sakura.lua:f_0001063E):
#  1) 直接调用对话不会导致背景模糊 (f_0004CEFD / function_4809_ 不碰 prim 186/187)
#  2) 模糊由外部函数显式切出 (f_00052882: PrimSetAlpha(187), f_00051202: PrimGroupIn)
#  3) V3D 放大不等于模糊 (V3DMotion/V3DSet 只是相机, 真模糊是独立贴图 graph_bg/*b)
#
# 取材 (共通线, function_1064_ 后紧跟的两句, Sakura_dump.txt:314125-314132):
#  あさひ「さて、今夜はシチューだ。たくさん作ったから、いっぱい食べてくれたらうれしいな」
#  地文「台所からエプロンを外しながらあさひさんがやって来た。」
# 背景: BG001_020 (prim 186 本体) / BG001_020b (prim 187 模糊差分)
#
# 安装:
#  1. 新建 Ren'Py 工程 (1280x720), 把本文件拷到 game/ 目录, 改名为 bg_blur_proof.rpy
#  2. 把 demo_proof/assets/*.png 拷到 game/images/ (或 game/bg_demo/), 并按需改下面 image 路径
#  3. Ren'Py Launcher -> Launch Project -> 从主菜单选「背景模糊验证」
#     或在任意 label 里 `call bg_blur_proof`

init offset = 60

# ---------------------------------------------------------------- FVP 映射
# f_0005074C(function_4854_): GraphLoad(186)+PrimSetSprt(186)+PrimSetAlpha(186,255)
image bg_fvp_normal = "images/BG001_020_186_720.png"
# f_00050859(function_4855_): GraphLoad(187)+PrimSetSprt(187)+PrimSetAlpha(187,0)
image bg_fvp_blur = "images/BG001_020b_187_720.png"

define asa = Character("あさひ", color="#ffb3c6")
define nar = Character(None)

# V3D zoom 模拟: 只对本体做 zoom, 不换贴图 (反驳"放大自动变模糊")
transform v3d_zoom_only:
    subpixel True
    anchor (0.5, 0.5) pos (640, 360)
    zoom 1.0
    linear 1.5 zoom 1.2

transform center_full:
    anchor (0.0, 0.0) pos (0, 0)

label bg_blur_proof:
    # --- Act 1: 预加载对应 f_0001063E 尾部: 本体可见, 模糊层已装但 Alpha 0 ---
    scene bg_fvp_normal with dissolve
    nar "【Act 1】本体显示中 (prim 186, Alpha 255 / prim 187 已 GraphLoad 但 Alpha 0)"
    asa "さて、今夜はシチューだ。たくさん作ったから、いっぱい食べてくれたらうれしいな"
    nar "台所からエプロンを外しながらあさひさんがやって来た。"
    nar "注意: 两句对话都走 f_0004CEFD, 背景依然锐利——对话本身不切模糊。"

    # --- Act 2: 外部函数切模糊, 对应 f_00052882 + f_00051202 ---
    nar "【Act 2】调用外部函数: PrimSetAlpha(187, 0→255) + PrimGroupIn(187)"
    show bg_fvp_blur with Dissolve(0.5)
    nar "已切到模糊差分 (prim 187, BG001_020b)。同一段对话, 背景变了——切换来自外部函数。"
    asa "さて、今夜はシチューだ。たくさん作ったから、いっぱい食べてくれたらうれしいな"

    # --- Act 3: V3D 放大对照, 只 zoom 本体, 证明不等于 b 图 ---
    nar "【Act 3】对照: V3DMotion 只放大本体 (zoom 1.0→1.2), 不换贴图。"
    scene bg_fvp_normal at v3d_zoom_only with dissolve
    nar "放大后的本体依然锐利, 和 BG001_020b 的光学模糊完全不同——V3D 不自动产生模糊。"
    show bg_fvp_blur with Dissolve(0.5)
    nar "再切回真 b 图, 对比成立。结论: 模糊 = 独立资源 + 显式 Prim 切换。"

    nar "验证结束: 1)对话不致模糊 2)外部函数致模糊 3)V3D放大≠模糊。"
    return
