# see-through 拆分模块

2D 形象的首选拆分路径：调 see-through 的 Hugging Face Space（[shitagaki-lab/see-through](https://github.com/shitagaki-lab/see-through)，Apache-2.0）把单张立绘拆成 22 个语义层 PSD（含遮挡区域补全），产物供客户端 Puppet 渲染层消费。

## 模块结构

| 文件 | 职责 |
|------|------|
| `client.py` | Gradio 协议传输：upload → call → SSE 轮询 → PSD 下载；全部失败态收敛为 `SeeThroughError` |
| `pipeline.py` | 编排：拆分 → PSD 存资产库 → 产出 `spiritagent.2d.psd/1` 描述符 manifest |

## 关键约束

- **无 SLA、每日免费额度**：Space 是社区免费算力（ZeroGPU），失败/超时/限额一律抛 `SeeThroughError`，由 mesh2d pipeline 降级 CPU 切分链，不重试（重试会烧额度）。
- **协议坑**：参数必须传 Gradio FileData 对象（`{"path": ..., "meta": {"_type": "gradio.FileData"}}`），裸路径字符串被静默拒收（`event: error` + `data: null`）。
- **描述符契约**：manifest_json 为 `{"schema": "spiritagent.2d.psd/1", "kind": "psd", "psd": 裸路径}`；`layers` 恒为单条 `{"name": "psd", "url": 裸路径}`。客户端按 `kind` 分流到 Puppet 渲染层（解析 PSD 用 ag-psd，后端不解析、不依赖 psd-tools）。
- **配置门控**：`seethrough_enabled`（默认 false）+ `seethrough_space_base`（自托管时替换）。
