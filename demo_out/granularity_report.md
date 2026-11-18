# 更新粒度实验：节点脏标记 vs 绘制区域传播

视口 1120x460 （515200 px），三个更新器消费同一份 `build_display_list` 输出。
每一步都与独立整帧重绘 `render_full_reference()` 逐字节比对，
重绘面积与像素差异由代码复算，原始数据见 granularity_results.json。

## 退回全量绘制的显式条件

- 合并后脏区面积 >= 视口的 60%
- 脏矩形数量 >= 12 个（碎片化）
- 节点脏标记的依赖扩展集 >= 显示列表条目的 50%
- 缩放（scale 改变）使所有 (id,version,scale) 缓存键失效

## 场景汇总（累计工作量）

| 场景 | 策略 | 脏区面积(px) | 占视口/步 | 实际绘制px | 重放条目 | 全量退回 | 像素不符 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| big_node_move | 节点脏标记(含依赖扩展) | 6545214 | 79.40% | 10262254 | 1116 | 10 | 0 |
| big_node_move | 朴素节点脏标记(负对照) | 1695334 | 20.57% | 2111845 | 37 | 0 | 3516286 |
| big_node_move | 绘制区域传播 | 1796534 | 21.79% | 3466552 | 392 | 2 | 0 |
| tiny_primitive_edit | 节点脏标记(含依赖扩展) | 4303306 | 55.68% | 8674249 | 888 | 3 | 0 |
| tiny_primitive_edit | 朴素节点脏标记(负对照) | 1022101 | 13.23% | 1279757 | 42 | 0 | 1947845 |
| tiny_primitive_edit | 绘制区域传播 | 1022101 | 13.23% | 2632240 | 303 | 0 | 0 |
| translucent_overlap | 节点脏标记(含依赖扩展) | 2076190 | 80.60% | 1632046 | 25 | 4 | 0 |
| translucent_overlap | 朴素节点脏标记(负对照) | 176370 | 6.85% | 645308 | 7 | 0 | 178110 |
| translucent_overlap | 绘制区域传播 | 176370 | 6.85% | 1230304 | 20 | 0 | 0 |
| isolated_opaque | 节点脏标记(含依赖扩展) | 45584 | 2.21% | 173148 | 4 | 0 | 0 |
| isolated_opaque | 朴素节点脏标记(负对照) | 45584 | 2.21% | 173148 | 4 | 0 | 0 |
| isolated_opaque | 绘制区域传播 | 45584 | 2.21% | 173148 | 4 | 0 | 0 |

## 逐步明细（脏区面积 / 实际绘制px / 像素差异）

### big_node_move —— large node + connected edges moved inside a dense grid

| 步 | 操作 | 策略 | 脏区px | 占视口 | 绘制px | 退回 | 像素差异 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | move#80 | node_dirty | 515200 | 100.00% | 728700 | 是:dirty area >= 60% of viewport | 0 |
| 0 | move#80 | naive_node_dirty | 165640 | 32.15% | 245563 | 否 | 83942 |
| 0 | move#80 | region_propagation | 165640 | 32.15% | 488843 | 否 | 0 |
| 1 | move#80 | node_dirty | 515200 | 100.00% | 727723 | 是:dependency set 44/81 >= 50% of display list | 0 |
| 1 | move#80 | naive_node_dirty | 186140 | 36.13% | 265086 | 否 | 98300 |
| 1 | move#80 | region_propagation | 186140 | 36.13% | 535790 | 否 | 0 |
| 2 | move#80 | node_dirty | 515200 | 100.00% | 680448 | 是:dirty area >= 60% of viewport | 0 |
| 2 | move#80 | naive_node_dirty | 153340 | 29.76% | 185011 | 否 | 111005 |
| 2 | move#80 | region_propagation | 153340 | 29.76% | 407019 | 否 | 0 |
| 3 | move#1 | node_dirty | 515200 | 100.00% | 675463 | 是:dependency set 81/81 >= 50% of display list | 0 |
| 3 | move#1 | naive_node_dirty | 464600 | 90.18% | 495156 | 否 | 262524 |
| 3 | move#1 | region_propagation | 515200 | 100.00% | 675463 | 是:dirty area >= 60% of viewport | 0 |
| 4 | move#2 | node_dirty | 515200 | 100.00% | 675689 | 是:dirty area >= 60% of viewport | 0 |
| 4 | move#2 | naive_node_dirty | 38220 | 7.42% | 58511 | 否 | 258578 |
| 4 | move#2 | region_propagation | 38220 | 7.42% | 98250 | 否 | 0 |
| 5 | move#3 | node_dirty | 515200 | 100.00% | 675591 | 是:dirty area >= 60% of viewport | 0 |
| 5 | move#3 | naive_node_dirty | 36696 | 7.12% | 45537 | 否 | 257709 |
| 5 | move#3 | region_propagation | 36696 | 7.12% | 80471 | 否 | 0 |
| 6 | move#4 | node_dirty | 228245 | 44.30% | 562763 | 否 | 0 |
| 6 | move#4 | naive_node_dirty | 10492 | 2.04% | 22335 | 否 | 251868 |
| 6 | move#4 | region_propagation | 10492 | 2.04% | 22335 | 否 | 0 |
| 7 | move#5 | node_dirty | 227290 | 44.12% | 561580 | 否 | 0 |
| 7 | move#5 | naive_node_dirty | 15204 | 2.95% | 39560 | 否 | 240823 |
| 7 | move#5 | region_propagation | 15204 | 2.95% | 49563 | 否 | 0 |
| 8 | move#6 | node_dirty | 227290 | 44.12% | 561488 | 否 | 0 |
| 8 | move#6 | naive_node_dirty | 19610 | 3.81% | 44917 | 否 | 235446 |
| 8 | move#6 | region_propagation | 19610 | 3.81% | 60988 | 否 | 0 |
| 9 | move#7 | node_dirty | 235144 | 45.64% | 576770 | 否 | 0 |
| 9 | move#7 | naive_node_dirty | 24381 | 4.73% | 33388 | 否 | 236476 |
| 9 | move#7 | region_propagation | 24381 | 4.73% | 66986 | 否 | 0 |
| 10 | move#8 | node_dirty | 247000 | 47.94% | 587692 | 否 | 0 |
| 10 | move#8 | naive_node_dirty | 17139 | 3.33% | 24814 | 否 | 236430 |
| 10 | move#8 | region_propagation | 17139 | 3.33% | 34173 | 否 | 0 |
| 11 | move#9 | node_dirty | 515200 | 100.00% | 672021 | 是:dirty area >= 60% of viewport | 0 |
| 11 | move#9 | naive_node_dirty | 21614 | 4.20% | 39669 | 否 | 230852 |
| 11 | move#9 | region_propagation | 21614 | 4.20% | 66115 | 否 | 0 |
| 12 | set_text#1 | node_dirty | 515200 | 100.00% | 671585 | 是:dependency set 81/81 >= 50% of display list | 0 |
| 12 | set_text#1 | naive_node_dirty | 464600 | 90.18% | 494726 | 否 | 255507 |
| 12 | set_text#1 | region_propagation | 515200 | 100.00% | 671585 | 是:dirty area >= 60% of viewport | 0 |
| 13 | set_text#2 | node_dirty | 515200 | 100.00% | 671257 | 是:dirty area >= 60% of viewport | 0 |
| 13 | set_text#2 | naive_node_dirty | 40600 | 7.88% | 60556 | 否 | 255103 |
| 13 | set_text#2 | region_propagation | 40600 | 7.88% | 110177 | 否 | 0 |
| 14 | set_text#3 | node_dirty | 515200 | 100.00% | 670887 | 是:dirty area >= 60% of viewport | 0 |
| 14 | set_text#3 | naive_node_dirty | 29468 | 5.72% | 37939 | 否 | 253800 |
| 14 | set_text#3 | region_propagation | 29468 | 5.72% | 79717 | 否 | 0 |
| 15 | set_text#4 | node_dirty | 228245 | 44.30% | 562597 | 否 | 0 |
| 15 | set_text#4 | naive_node_dirty | 7590 | 1.47% | 19077 | 否 | 247923 |
| 15 | set_text#4 | region_propagation | 7590 | 1.47% | 19077 | 否 | 0 |

### tiny_primitive_edit —— small label/geometry edits on fine-grained primitives

| 步 | 操作 | 策略 | 脏区px | 占视口 | 绘制px | 退回 | 像素差异 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | set_text#1 | node_dirty | 515200 | 100.00% | 558983 | 是:dependency set 56/72 >= 50% of display list | 0 |
| 0 | set_text#1 | naive_node_dirty | 237600 | 46.12% | 260473 | 否 | 142313 |
| 0 | set_text#1 | region_propagation | 237600 | 46.12% | 607156 | 否 | 0 |
| 1 | set_text#2 | node_dirty | 237600 | 46.12% | 606868 | 否 | 0 |
| 1 | set_text#2 | naive_node_dirty | 15151 | 2.94% | 30260 | 否 | 137145 |
| 1 | set_text#2 | region_propagation | 15151 | 2.94% | 40739 | 否 | 0 |
| 2 | set_text#3 | node_dirty | 228448 | 44.34% | 578696 | 否 | 0 |
| 2 | set_text#3 | naive_node_dirty | 12120 | 2.35% | 20978 | 否 | 132478 |
| 2 | set_text#3 | region_propagation | 12120 | 2.35% | 28246 | 否 | 0 |
| 3 | set_text#4 | node_dirty | 228448 | 44.34% | 578380 | 否 | 0 |
| 3 | set_text#4 | naive_node_dirty | 36750 | 7.13% | 51164 | 否 | 129501 |
| 3 | set_text#4 | region_propagation | 36750 | 7.13% | 98706 | 否 | 0 |
| 4 | set_text#5 | node_dirty | 228448 | 44.34% | 578064 | 否 | 0 |
| 4 | set_text#5 | naive_node_dirty | 34510 | 6.70% | 59102 | 否 | 123784 |
| 4 | set_text#5 | region_propagation | 34510 | 6.70% | 101767 | 否 | 0 |
| 5 | set_text#6 | node_dirty | 221840 | 43.06% | 569436 | 否 | 0 |
| 5 | set_text#6 | naive_node_dirty | 34056 | 6.61% | 52103 | 否 | 122539 |
| 5 | set_text#6 | region_propagation | 34056 | 6.61% | 91301 | 否 | 0 |
| 6 | set_text#7 | node_dirty | 221840 | 43.06% | 569054 | 否 | 0 |
| 6 | set_text#7 | naive_node_dirty | 21680 | 4.21% | 33801 | 否 | 120051 |
| 6 | set_text#7 | region_propagation | 21680 | 4.21% | 54424 | 否 | 0 |
| 7 | set_text#8 | node_dirty | 237840 | 46.16% | 605607 | 否 | 0 |
| 7 | set_text#8 | naive_node_dirty | 30624 | 5.94% | 48055 | 否 | 116584 |
| 7 | set_text#8 | region_propagation | 30624 | 5.94% | 82536 | 否 | 0 |
| 8 | set_text#9 | node_dirty | 237840 | 46.16% | 605273 | 否 | 0 |
| 8 | set_text#9 | naive_node_dirty | 23256 | 4.51% | 40638 | 否 | 118592 |
| 8 | set_text#9 | region_propagation | 23256 | 4.51% | 66835 | 否 | 0 |
| 9 | set_text#10 | node_dirty | 217800 | 42.27% | 559469 | 否 | 0 |
| 9 | set_text#10 | naive_node_dirty | 14308 | 2.78% | 37068 | 否 | 109385 |
| 9 | set_text#10 | region_propagation | 14308 | 2.78% | 43793 | 否 | 0 |
| 10 | set_text#1 | node_dirty | 515200 | 100.00% | 556241 | 是:dependency set 56/72 >= 50% of display list | 0 |
| 10 | set_text#1 | naive_node_dirty | 237600 | 46.12% | 260361 | 否 | 144060 |
| 10 | set_text#1 | region_propagation | 237600 | 46.12% | 604466 | 否 | 0 |
| 11 | move#1 | node_dirty | 515200 | 100.00% | 556091 | 是:dependency set 56/72 >= 50% of display list | 0 |
| 11 | move#1 | naive_node_dirty | 237600 | 46.12% | 260211 | 否 | 143516 |
| 11 | move#1 | region_propagation | 237600 | 46.12% | 604316 | 否 | 0 |
| 12 | move#2 | node_dirty | 230670 | 44.77% | 595522 | 否 | 0 |
| 12 | move#2 | naive_node_dirty | 20016 | 3.89% | 35117 | 否 | 140030 |
| 12 | move#2 | region_propagation | 20016 | 3.89% | 59080 | 否 | 0 |
| 13 | move#3 | node_dirty | 225544 | 43.78% | 570306 | 否 | 0 |
| 13 | move#3 | naive_node_dirty | 19598 | 3.80% | 28564 | 否 | 135480 |
| 13 | move#3 | region_propagation | 19598 | 3.80% | 35990 | 否 | 0 |
| 14 | move#4 | node_dirty | 241388 | 46.85% | 586259 | 否 | 0 |
| 14 | move#4 | naive_node_dirty | 47232 | 9.17% | 61862 | 否 | 132387 |
| 14 | move#4 | region_propagation | 47232 | 9.17% | 112885 | 否 | 0 |

### translucent_overlap —— edits inside stacked translucent overlaps (dependency test)

| 步 | 操作 | 策略 | 脏区px | 占视口 | 绘制px | 退回 | 像素差异 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | set_fill#1 | node_dirty | 515200 | 100.00% | 393541 | 是:dependency set 5/6 >= 50% of display list | 0 |
| 0 | set_fill#1 | naive_node_dirty | 51570 | 10.01% | 198209 | 否 | 41100 |
| 0 | set_fill#1 | region_propagation | 51570 | 10.01% | 361901 | 否 | 0 |
| 1 | move#3 | node_dirty | 515200 | 100.00% | 393541 | 是:dependency set 5/6 >= 50% of display list | 0 |
| 1 | move#3 | naive_node_dirty | 35090 | 6.81% | 106840 | 否 | 36270 |
| 1 | move#3 | region_propagation | 35090 | 6.81% | 272543 | 否 | 0 |
| 2 | set_text#4 | node_dirty | 515200 | 100.00% | 393697 | 是:dependency set 4/6 >= 50% of display list | 0 |
| 2 | set_text#4 | naive_node_dirty | 22750 | 4.42% | 84480 | 否 | 21500 |
| 2 | set_text#4 | region_propagation | 22750 | 4.42% | 180245 | 否 | 0 |
| 3 | set_text#1 | node_dirty | 515200 | 100.00% | 393853 | 是:dependency set 5/6 >= 50% of display list | 0 |
| 3 | set_text#1 | naive_node_dirty | 51570 | 10.01% | 198365 | 否 | 39620 |
| 3 | set_text#1 | region_propagation | 51570 | 10.01% | 358201 | 否 | 0 |
| 4 | set_text#6 | node_dirty | 15390 | 2.99% | 57414 | 否 | 0 |
| 4 | set_text#6 | naive_node_dirty | 15390 | 2.99% | 57414 | 否 | 39620 |
| 4 | set_text#6 | region_propagation | 15390 | 2.99% | 57414 | 否 | 0 |

### isolated_opaque —— disjoint opaque nodes: naive per-object repaint must work

| 步 | 操作 | 策略 | 脏区px | 占视口 | 绘制px | 退回 | 像素差异 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | set_text#1 | node_dirty | 11396 | 2.21% | 43538 | 否 | 0 |
| 0 | set_text#1 | naive_node_dirty | 11396 | 2.21% | 43538 | 否 | 0 |
| 0 | set_text#1 | region_propagation | 11396 | 2.21% | 43538 | 否 | 0 |
| 1 | set_text#6 | node_dirty | 11396 | 2.21% | 42740 | 否 | 0 |
| 1 | set_text#6 | naive_node_dirty | 11396 | 2.21% | 42740 | 否 | 0 |
| 1 | set_text#6 | region_propagation | 11396 | 2.21% | 42740 | 否 | 0 |
| 2 | set_text#10 | node_dirty | 11396 | 2.21% | 43388 | 否 | 0 |
| 2 | set_text#10 | naive_node_dirty | 11396 | 2.21% | 43388 | 否 | 0 |
| 2 | set_text#10 | region_propagation | 11396 | 2.21% | 43388 | 否 | 0 |
| 3 | set_text#3 | node_dirty | 11396 | 2.21% | 43482 | 否 | 0 |
| 3 | set_text#3 | naive_node_dirty | 11396 | 2.21% | 43482 | 否 | 0 |
| 3 | set_text#3 | region_propagation | 11396 | 2.21% | 43482 | 否 | 0 |

## 实验结论

结论正文见 docs/granularity_experiment.md；本文件由 `python3 -m flowkern.granularity_experiment` 重新生成，
任何手工改数都会被下次运行覆盖。
