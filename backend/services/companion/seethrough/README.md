# see-through 拆分模块

2D 形象的唯一拆分路径：调 see-through 的在线 Space 把单张立绘拆成 22 个语义层 PSD（含遮挡区域补全），产物供客户端 Puppet 渲染层消费。主用 Hugging Face Space（[shitagaki-lab/see-through](https://github.com/shitagaki-lab/see-through)，Apache-2.0），魔搭社区 ModelScope 创空间为备用。

## 模块结构

| 文件 | 职责 |
|------|------|
| `client.py` | 双 provider Gradio 协议传输：upload → call → SSE 轮询 → PSD 下载；主用失败自动切备用；全部失败态收敛为 `SeeThroughError`（kind: quota / transport / space） |
| `pipeline.py` | 编排：拆分 → PSD 存资产库 → 产出 `spiritagent.2d.psd/1` 描述符 manifest |

## Provider 备用策略

- **主用**：`seethrough_space_base`（默认 HF ZeroGPU Space，每日免费额度有限）
- **备用**：`seethrough_fallback_base`（默认魔搭 API-Inference 专用域名 `https://studio-ljsabc-see-through.api-inference.modelscope.net/gradio_api`，空串禁用备用）+ `seethrough_fallback_token`（魔搭 SDK Token，备用请求以 Bearer 头携带；www 代理域名会 403 重定向到专用域名且强制要求 token）
- **切换条件**：主用任何失败（限额 / Space 休眠 / 网络超时）都自动切备用再试一次；单 provider 均不重试（重试会烧额度）
- **限额冷却**：主用确认为每日限额（SSE 错误文案子串或 HTTP 429/402）后进程内冷却 6 小时，期间请求直接走备用、不再白等主用排队；冷却只记录主用，备用限额不抑制主用
- **共享预算**：双 provider 合计 1740s 墙钟（单次 900s 超时；最坏 1800s 会撞 outfit 拆分 30 分钟清扫窗口）；剩余 <60s 时跳过备用直接报错

## 关键约束

- **无 SLA、社区免费算力**：失败/超时统一抛 `SeeThroughError`，由 mesh2d pipeline 落失败态（无 CPU 兜底链，客户端落 3D/蛋 + 设置页重试）
- **协议坑**：参数必须传 Gradio FileData 对象（`{"path": ..., "meta": {"_type": "gradio.FileData"}}`），裸路径字符串被静默拒收（`event: error` + `data: null`）
- **描述符契约**：manifest_json 为 `{"schema": "spiritagent.2d.psd/1", "kind": "psd", "psd": 裸路径}`；`layers` 恒为单条 `{"name": "psd", "url": 裸路径}`。客户端按 `kind` 分流到 Puppet 渲染层（解析 PSD 用 ag-psd，后端不解析、不依赖 psd-tools）
- **魔搭冷启动**：ModelScope 创空间休眠后首调含唤醒时间，900s 单次超时覆盖
- **自托管**：两个 base 均可替换为自托管实例；`seethrough_fallback_token` 置空即匿名调用（魔搭备用必须配 token 才可用）
