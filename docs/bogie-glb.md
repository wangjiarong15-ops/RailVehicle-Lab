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
