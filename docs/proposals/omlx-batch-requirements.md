# omlx Batch Processing Requirements — from Prism v2

## 背景

Prism v2 新增了 entity_link pipeline，需要每天凌晨用本地 27B 模型处理 50-100 条信号的实体提取任务。当前 omlx 的 chat completions API 能工作，但缺少批量处理优化，导致需要 Prism 自己管理任务队列和模型调度。

## 需求清单

### P0: 必须有

**1. Batch Queue API**
Prism 每天凌晨 2-4am 需要提交 50-100 个 completion 请求批量处理。当前只能逐个发 HTTP 请求，无法提交后离开。

```
POST /v1/batch/submit
Body: {"requests": [{completion_request}, ...], "model": "Qwen3.5-27B-..."}
Response: {"batch_id": "xxx", "total": 100, "status": "queued"}

GET /v1/batch/{batch_id}/status
Response: {"status": "running", "completed": 45, "total": 100}

GET /v1/batch/{batch_id}/results
Response: {"results": [{completion_response}, ...]}
```

用例：Prism 提交一晚上的实体提取任务，不需要轮询，早上取结果即可。

**2. Auto-load on Request**
当前请求未加载的模型会返回 500 Internal Server Error（MiMo-V2-Flash 加载失败就是这个问题）。希望支持自动加载：

```
配置项: auto_load_policy: manual | auto_load | auto_load_and_evict
```

- `auto_load`: 如果内存够，自动加载请求的模型
- `auto_load_and_evict`: 自动加载，如果内存不够则卸载最久未用的模型

### P1: 强烈需要

**3. Priority Levels**
Prism 的批量任务是后台低优先级，不应阻塞 Claude Code 的交互式对话。

```
Header: X-Priority: background | normal | urgent
```

- `background`: Prism batch 任务，可以被 urgent 请求抢占
- `urgent`: Claude Code 对话，立即处理
- 当 urgent 请求到来时，background 任务暂停让路，完成后继续

**4. 小模型并发解码扩展**
当前 max 2 concurrent decodes 对 27B 模型太保守。27B 在 512GB 机器上只占 21GB，完全可以并发 4 个。

```
配置项: max_concurrent_decodes:
  default: 2
  per_model_override:
    "Qwen3.5-27B-*": 4
    "MiniMax-M2.5-*": 1  # 255GB, 保持独占
```

### P2: 可选

**5. Scheduled Model Swap**
指定时间段加载特定模型：凌晨 2-4am 保证 27B 可用，白天切回 MiniMax for Claude Code。

**6. Health Endpoint**
`GET /v1/health` → 返回 loaded models, queue depth, memory usage, decode slots。方便 Prism 在 sync 前检查模型是否就绪。

**7. Batch Webhook**
batch 完成时 POST 到指定 URL，避免轮询。

## 降级方案

如果以上功能短期内无法实现，Prism v2 会自建简单 FIFO 队列：
- asyncio.Semaphore(2) 控制并发
- 逐个发 chat completions 请求
- 50 signals × ~5s/each = ~4 min，性能可接受
- 但无法做 priority（Claude Code 和 Prism 可能冲突）

## 预期收益

实现 P0+P1 后：
- Prism 批量处理吞吐提升 2-4x
- Claude Code 白天使用零干扰
- 模型切换自动化，无需手动操作
- 512GB Mac Studio 的 GPU 利用率从 <10% 提升到 30-40%（凌晨批量 + 白天交互）
