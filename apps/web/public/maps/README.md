# 观山湖 POC 本地底图

- 文件：`guanshanhu.pmtiles`
- 版本：`1.0.0`
- 覆盖范围：`106.52,26.53,106.74,26.75`
- 默认中心：`106.6282,26.6467`
- 缩放级别：10—13
- 数据来源：项目内确定性生成的 POC 示意底图，不包含外部地图数据
- 许可：仅供本 POC 项目内部演示使用，未经权利人书面许可不得对外分发
- 生成日期：2026-09-03

该地图包用于验证 MapLibre 在无公网、无独立瓦片服务进程条件下直接读取 PMTiles。道路、水体和中心点均为示意内容，不得作为导航、执法或监管依据。

重新生成：

1. 运行 `python3 tools/generate_demo_map.py /tmp/guanshanhu.mbtiles`。
2. 使用 Protomaps 官方 `pmtiles convert` 将 MBTiles 转为 `guanshanhu.pmtiles`。
