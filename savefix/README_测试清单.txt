savefix 测试清单 (中文用户名存档重定向)
================================================
部署: 把 savefix.dll + SaveFixLauncher.exe 拷到游戏目录
      (与 Sakura.exe 同目录), 双击 SaveFixLauncher.exe 启动.
      两个文件删掉即回滚, 原 exe/注册表零改动.

一、冒烟 (5 分钟, 任一机器)
[ ] 双击启动器: 无报错框, 游戏窗口出现
[ ] 游戏目录下出现 userdata\FAVORITE\さくら、もゆ。\save\
[ ] Documents 下不再产生新文件 (证明流量已重定向)
[ ] 随便进游戏存一个档, userdata\...\save\ 下出现 sNNN.bin
[ ] 退游戏重进, 读档正常, 缩略图正常
[ ] 删两个文件, 双击 Sakura.exe 回原行为 (回滚验证)

二、中文用户名 (目标机器, 转区/非转区各一遍)
[ ] 同上全套通过, 且 Documents\FAVORITE 下无残留新文件
[ ] 老档迁移: 备份一份老 save/ 到 Documents, 删 userdata,
    启动 -> 老档自动出现在 userdata 下, 且 Documents 原档还在
    (永不删源), 游戏内读档正常
[ ] userdata 已存在时启动: 不覆盖已有文件

三、边界 (预期行为, 非 bug)
[ ] 游戏装在中文路径下启动: 应弹框提示换英文目录,
    点确定后回原行为 (不静默崩)
[ ] 存档槽删档: userdata 下对应文件消失, 游戏正常
[ ] 全 CG/系统 save.bin: 读写正常

四、已知不测项
[ ] 不测多人联机/云同步 (OneDrive 托管 Documents 的用户,
    迁移后存档改存本地 userdata, 属预期变更, 需在发布说明里写一句)
[ ] 不测非 Sakura.exe 宿主 (DLL 自带宿主名校验, 误加载=静默不挂钩)

故障上报模板 (贴过来即修):
  Windows 版本 / 用户名语言 / 转区与否 / 启动器报了什么框原文 /
  userdata 下有什么 / Documents\FAVORITE 下有什么
