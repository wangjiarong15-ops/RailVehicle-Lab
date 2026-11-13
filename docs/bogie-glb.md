# Blender 建模与 GLB 装配约定

在 Windows 或其他系统使用 Blender 4.5 LTS 运行以下命令。PowerShell 中，若 Blender 不在 PATH，可将 `blender` 换成 `& 'C:/路径/blender.exe'`。

```powershell
python scripts/export_bogie_webgl.py
blender --background --factory-startup --python-exit-code 1 --python scripts/build_bogie_blender.py -- --blend rail-bogie.blend
python scripts/validate_bogie_glb.py
```

默认输出到 `rail_vehicle/assets/bogie_viewer/bogie.glb`，也可用 `--output` 指定路径。`--blend` 生成可编辑的 Blender 场景，可省略。建模脚本和零件参数可用于重新生成资产。应用和 GLB 结构校验均无需安装 Blender。GLB 内嵌几何和金属度、粗糙度材质，不使用外置纹理、压缩解码器或内置动画。

尺寸以米为单位。Blender、Python 几何和工作台采用 X 纵向、Y 横向、Z 向上。导出时写入 glTF 标准的 Y 向上坐标，浏览器加载后还原到工作台坐标。模型的轴距为 2.60 m，左右车轮中心间距为 1.76 m，踏面名义半径为 0.420 m，轮缘最大半径为 0.456 m，轴箱中心间距为 2.40 m，构架总长为 3.72 m。这些是通用示意尺寸，不代表轨距、许可间隙或任何具体车辆。

完整的 54 项清单在 `rail_vehicle/assets/bogie_viewer/model_contract.json`，由 `rail_vehicle/bogie_model_spec.py` 生成。后缀 `a`、`b` 分别表示 X 为 −1.30 m 和 +1.30 m 的轮对。`l`、`r` 分别表示 Y 负侧和正侧。

| 根节点下的层 | 零件 | 播放时的行为 |
| --- | --- | --- |
| `rail_frame` | 左右侧梁、横梁、上弹簧座、空气弹簧、电机 | 随构架横移、升沉和摇头 |
| `rail_wheel` | 两根车轴、四个车轮、四个轴箱、下弹簧座、齿轮箱、制动盘和卡钳 | 随走行机构运动；只有车轴、车轮、制动盘绕各自轴心的 Y 轴转动 |
| `rail_links` | 一系螺旋弹簧、减振器、导向件、传动连接和卡钳支撑 | 按 Python 定义的两个连接点更新姿态和长度；拆解时按层移动 |
| `rail_fixed` | 轨道参照 | 保持固定 |

更换网格时保留 `rail_bogie` 根节点、四个父层、各部件的稳定名称和自定义属性、原点及静置坐标。在 Blender 导出面板中使用 GLB、+Y Up、Custom Properties 和法线，关闭动画和 Draco 压缩。校验脚本会检查结构、坐标、内嵌资源和网格预算。前端通过 [Three.js GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html) 加载资产并检查装配层级和零件原点。

检测到软件 WebGL 时，播放过程中按构架、走行机构、轮轴及悬挂连杆合批绘制，使用简化光照并降低绘制分辨率。单件拾取和隐藏仍使用原装配节点。暂停后恢复完整分辨率、阴影和 PBR。硬件 WebGL 保留 PBR 播放。

在视窗工具栏打开「零件清单」，可按中文名称、类别或稳定编号查找零件。选中后可聚焦、隐藏、恢复所选，或使用「仅看所选」观察被其他构件遮挡的部位。退出隔离会恢复隔离前的类别和单件隐藏设置。类别关闭的零件仍可搜索，重新显示时请启用对应类别。

在暂停且未拆解时，点击「两点测距」，再依次点击两个可见表面。视窗会标记测点，显示直线距离和从第一点到第二点的有符号 X、Y、Z 差值，单位均为米。可清除测点，也可点击第三点开始下一次测量。播放、改变模型姿态或可见零件会清空测点。这些读数用于比较通用示意模型中的距离。

点击「剖切视图」，选择 X 纵向、Y 横向或 Z 垂向，拖动滑块设置切面在模型坐标中的米制位置。「翻转保留侧」可查看切面的另一侧，关闭剖切可恢复完整外形。剖切时可以选中、聚焦和测量仍可见的表面。移动或关闭切面会清除原测点。剖切显示现有表面，切口不生成实体截面或填充。

暂停后点击「导出检查图」可在浏览器下载 PNG。图片包含当前模型画面、所选零件、启用的剖切参数和已完成的测距读数，适合随检查记录留档。GLB 和程序化几何均可在本地导出。
