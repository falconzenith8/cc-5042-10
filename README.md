# flowkern —— 自制保留式流程图渲染内核

一个从零实现的流程图渲染内核：自己的节点/连线/文字显示列表、自己的纯
Python RGBA 软件栅格器、显式的脏区与裁剪管理、对象身份与渲染缓存分离。
**不依赖任何现成流程图组件**（无 Pillow / cairo / Qt / Tk 绘图依赖），
仅标准库；Tk 视图为可选外壳，在无显示服务器的机器上可用无头编辑窗口。

## 1. 保留式绘制的图形入口

所有图形能力都从下列保留式对象进入，命令行只是它们的辅助驱动。

| 入口 | 位置 | 作用 |
| --- | --- | --- |
| `Scene.add_node / add_edge / move_node / remove_* / set_*` | `flowkern/scene.py` | 保留场景图：稳定 `id` + 每对象 `version` |
| `build_display_list(scene, transform)` | `flowkern/displaylist.py` | 编译成带设备坐标与明确绘制顺序的 `PaintCommand` 显示列表 |
| `RenderKernel.render_incremental()` | `flowkern/renderer.py` | 保留式局部重绘：算脏区、清旧像素、按 clip 重放命令 |
| `RenderKernel.render_full()` | `flowkern/renderer.py` | 整帧参考路径，用于逐像素核对 |
| `Editor`（`begin_drag/drag_to/end_drag/add_*/delete/pan/zoom/verify_against_full_frame/compare_update_strategies`） | `flowkern/editor.py` | 后端无关的保留式编辑控制器 |
| 更新粒度原型（节点脏标记 / 朴素负对照 / 区域传播） | `flowkern/updaters.py` | 同一显示列表上的两套失效策略与显式全量回退 |
| 更新粒度实验 | `flowkern/granularity_experiment.py` | 大节点移动、细小图元、透明重叠的面积+像素复算 |
| `HeadlessWindow` | `flowkern/headless_window.py` | 无显示环境的矢量编辑窗口（REPL / 脚本，含 `vdrag/verify/granularity`） |
| `TkWindow` | `flowkern/tk_window.py` | 桌面端保留式矢量窗口（懒加载 Tk，可选） |
| 命令行 | `python3 -m flowkern.cli ...` | 仅辅助：渲染、编辑回放、压力对比 |

## 2. 内核如何决定重绘区域

- **可见范围包含全部外扩像素**。`displaylist.build_node_entry` 的包围盒合并了
  正文（含描边半宽外扩）、阴影（`shadow_blur` 模糊垫 + 偏移）和文字包围盒；
  文字宽度按字形前进量 + 末列外溢测量，避免文字越界被裁掉。因此脏区不会
  只取节点中心矩形。
- **旧位置与新位置同时失效**。`Editor` 在移动/删除前保存受影响对象（含相连
  连线）的旧设备包围盒，与新包围盒一起并入脏区；删除对象后其缓存条目被丢弃，
  旧像素由脏区背景清除，杜绝残影。
- **脏区合并保持分离**。`geometry.merge_rects` 只联合真正相交的矩形，小修改
  不会被放大成整帧；平移走 `Surface.scroll` 的像素滚动，仅暴露条带失效。
- **缩放整帧失效并重建缓存**。缓存键为 `(id, version, scale)`，缩放清空全部
  对象层；平移复用层，仅重绘暴露区。

## 3. 绘制顺序、透明与裁剪

- 绘制顺序只由 `(z, 插入 id)` 决定（`Scene.display_order`），**从不按颜色
  批量重排**，因此遮挡关系稳定。
- 增量路径在**同一个主表面**上把脏矩形先填背景、再以该矩形为 clip，按全局
  绘制顺序直接重放所有相交条目的命令。源上 alpha 混合（`raster.blend`）因此
  与整帧路径满足相同结合律——阴影叠半透明填充、半透明节点互相覆盖都逐像素
  等价于整帧参考。早期“先渲染到透明对象层再整体 blit”的方案会在阴影 + 半透明
  填充同时出现时差一个色阶（alpha 不满足结合律），故合成不再走对象层；
  `(id,version,scale)` 对象层仍会构建并保留，用于平移/滚动复用、身份与版本
  校验，以及在 Tk/无头视图中检查缓存命中。
- 裁剪用显式 clip 栈（`Surface.push_clip/pop_clip`），所有图元在扫描线/跨度
  层与 clip 求交。

## 4. 对象身份与缓存分离

`Scene` 里的 `Node/Edge` 持有稳定身份与版本；`RenderKernel.cache` 以
`(kind, id) -> CacheEntry(key=(id,version,scale), bounds, layer)` 独立存放渲染
结果。移动一个节点只 bump 该节点和相连连线的版本，未触及对象的缓存层原样
复用；删除对象即删缓存条目，身份生命周期与像素缓存互不耦合。

## 5. 工作量度量（不靠“少刷新”自欺）

`raster.WorkStats` 记录真实工作量：`commands / fast_fill_px / blend_px /
text_px / blit_px / shadow_px / scroll_px / clip_pushes`。压力测试把每次局部
重绘后的帧与独立的整帧重绘做**逐字节比对**，只有像素完全一致才计入成功；
同时给出局部绘制占整帧重绘的像素比例。典型结果（12×14 节点 / 190 连线，
40 次快速移动）：局部绘制约为整帧重绘工作量的 **6.4%**，且 9 次抽样全部
像素一致。

## 6. 使用方式

```bash
# 单元测试（含脏区边界、删除清屏、绘制顺序、透明等价、平移滚动、性能对比）
python3 -m unittest discover -s tests -v

# 渲染一帧 + 脏区叠加图（PPM，任意图像查看器可开）
python3 -m flowkern.cli render --out demo_out/demo

# 回放保留编辑脚本（拖动/增删/平移/缩放，输出帧与脏区）
python3 -m flowkern.cli edit demo_out/edit_script.txt --out demo_out/edit

# 密集节点 + 跨屏连线 + 半透明重叠，对比整帧参考与实际工作量
python3 -m flowkern.cli stress --cols 12 --rows 14 --edits 40 --verify 8

# 更新粒度实验：节点脏标记 vs 绘制区域传播（面积与逐像素复算，结论见
# docs/granularity_experiment.md，数据写入 demo_out/granularity_*）
python3 -m flowkern.cli granularity

# 无头保留式矢量编辑窗口（交互式 REPL；help 查看命令）
python3 -m flowkern.headless_window

# 桌面 Tk 窗口（机器装有 tkinter 且有显示时）
python3 -m flowkern.tk_window
```

无头窗口命令：`add / edge / text / move / drag / vdrag / verify /
granularity / delete / pan / zoom / hit / list / dirty / stats / save /
overlay / reference / dense / reset`。`vdrag` 在真实拖动后自动逐字节对照
独立整帧，`granularity` 现场比较两套更新策略；所有非法操作抛出的异常都被
界面捕获并以 `[retained-draw error] ...` 提示，不会中断会话。

## 7. 模块索引

```
flowkern/
  geometry.py      Rect / Transform / 脏区合并（向外取整）
  color.py         RGBA 与源上 alpha 混合
  font.py          内置 5x7 位图字体（含 tofu 回退，零字体依赖）
  raster.py        纯 Python RGBA 表面：裁剪栈、粗线、多边形/椭圆/圆角矩形、
                   位图文字、alpha 遮罩盒式模糊阴影、滚动、工作量统计、PPM
  scene.py         保留场景图：稳定 id + 版本 + z 序
  displaylist.py   显示列表编译与 PaintCommand 记录
  renderer.py      RenderKernel：缓存、脏区、增量/整帧两条路径
  updaters.py      更新粒度原型：节点脏标记(依赖扩展)、朴素负对照、区域传播
  granularity_experiment.py  更新粒度对照实验（面积+逐像素，输出报告/JSON/PPM）
  editor.py        保留式编辑控制器（命中、拖拽、增删改、导航、整帧核对、异常回传）
  scene_factory.py 演示流程图与密集压力场景
  headless_window.py  无头保留式矢量编辑窗口（vdrag/verify/granularity）
  tk_window.py     可选 Tk 视图（显示列表/脏区/工作量面板、像素核对按钮）
  cli.py           命令行辅助入口（render/edit/stress/granularity）
tests/test_kernel.py  21 项正确性与性能测试
tests/test_updaters.py 更新粒度、依赖扩展、全量回退与实验复算测试
```

## 8. 已知边界

内置位图字体覆盖 ASCII（大小写折叠为大写）、数字与常见标点；非 ASCII 字符
渲染为可测量的 tofu 占位，完整 Unicode 需接入 TTF 后端（内核的 `Surface`
绘制接口已为此预留）。输出格式为通用 P6 PPM，无需第三方图像库即可查看。
