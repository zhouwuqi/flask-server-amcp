
# AMCP (AgentPlus Model Context Protocol) 构建指南

AMCP 是一套用于与 AgentPlus 平台通信的标准协议，允许开发者将自定义服务接入平台并实现同步、异步以及文件处理等功能。本文档旨在指导用户如何基于 Flask 框架构建符合 AMCP 规范的服务接口，并通过示例说明其工作原理及应用场景。

## 一、核心组件概述

本示例代码展示了五种典型的 AMCP 使用场景：
1. **接收文件传输请求** (`/amcp/receive_file`)
2. **处理同步调用请求** (`/amcp/sync`)
3. **支持异步任务处理** (`/amcp/async`)
4. **写文件进用户 temp 缓存区** (`/amcp/write_temp_file`)
5. **写文件进用户笔记本(struct)** (`/amcp/write_struct_file`)

并额外提供一个工具自描述端点：

6. **manifest 自描述** (`GET /amcp/manifest`) — 把 `amcp_config_sample/` 下的所有 JSON 一次性吐出，让 AgentPlus 端可以按服务器 URL 自动拉取工具描述，不必再让用户手动粘贴 JSON。

所有请求均遵循统一的数据格式封装：
```json
{
  "params": {}, // 固定参数配置
  "args": {},   // 来自会话的信息
  "input": {}   // 输入数据
}
```

---

## 二、构建流程详解

### 步骤1：初始化 Flask 蓝图

首先创建一个 Flask 的蓝图对象来组织路由逻辑：
```python
from flask import Blueprint
blueprint = Blueprint('tools', __name__)
```

该蓝图将被注册到主应用中以启用相关功能。

#### 配置回调地址（AGENTPLUS_BASE_URL）

所有回调（`create_async_message` / `update_async_message` / `write_temp_file` / `write_struct_file`）都拼在一个**基地址**上。默认走官方云端 `https://server.agentplus.cloud`，可用环境变量 `AGENTPLUS_BASE_URL` 覆盖——自建/私有部署或本地联调时改这一个变量即可，不必逐个改源码：

```python
import os
try:
    from dotenv import load_dotenv
    load_dotenv()              # 可选:从同目录 .env 读取;没装 dotenv 也能用进程环境变量
except Exception:
    pass

AGENTPLUS_BASE_URL = (os.environ.get("AGENTPLUS_BASE_URL") or "https://server.agentplus.cloud").rstrip("/")
# 之后回调统一写成 f"{AGENTPLUS_BASE_URL}/back/api/xxx"
```

设置方式（任选其一）：

```bash
# 1) 进程环境变量
export AGENTPLUS_BASE_URL=http://127.0.0.1:1000

# 2) 同目录 .env 文件(复制 .env.example 改之)
echo 'AGENTPLUS_BASE_URL=https://your.agentplus.host' > .env
```

> 注意：基地址只是**回调目的地**(你的服务往 AgentPlus 发 token 的地方)，和你这台 AMCP 服务自身监听的端口(`run.sh` 里的 3400)无关。

---

### 步骤2：通用辅助函数

为了简化 HTTP 请求操作，我们提供了一个 `post_json` 函数用于向指定 URL 发送 JSON 数据包：
```python
def post_json(url, info, headers=None, timeout=30):
    try:
        default_headers = {"Content-Type": "application/json"}
        if headers:
            default_headers.update(headers)
        response = requests.post(url, json=info, headers=default_headers, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise Exception(f"POST请求失败: {str(e)}")
```
此方法可复用于后续的回调通知或状态更新。

---

### 步骤3：实现 `/amcp/receive_file` 接口

该端点负责接收由 AgentPlus 发起的文件上传请求。解析出文件链接后可根据业务需求进一步处理内容。
```python
@blueprint.route('/amcp/receive_file', methods=['POST'])
def sample_receive_file():
    data = request.get_json()
    file_url = data["input"]["file_url"]
    # 处理文件下载与分析逻辑...
    
    return jsonify({
        "statusCode": 1,
        "returnData": {...}
    })
```

#### 流程图解：
```
[AgentPlus] -- 发送含文件URL的JSON --> [Your Server]
               ↑ 解析 input.file_url 获取资源地址
               ↓ 执行本地处理逻辑
           <-- 返回执行结果 --
```

---

### 步骤4：实现 `/amcp/sync` 接口

这是一个标准的同步请求处理器，适用于即时响应型任务。客户端发送指令后等待直到收到返回结果为止。
```python
@blueprint.route('/amcp/sync', methods=['POST'])
def sample_sync():
    data = request.get_json()
    # 根据 input 内容进行相应计算
    
    return jsonify({
        "statusCode": 1,
        "returnData": {...}
    })
```

#### 特点：
- 快速反馈；
- 不适合耗时较长的操作；

---

### 步骤5：实现 `/amcp/async` 接口

对于需要较长时间才能完成的任务，推荐使用异步模式。它包括两个阶段：
1. 初始确认并生成消息标识符（messageid）
2. 后台线程持续运行并在完成后主动上报状态

关键步骤如下所示：

#### 第一步：初始化异步消息
```python
url = "https://server.agentplus.cloud/back/api/create_async_message"
info = {
    "chatid": args["chatid"],
    "token": args["create_async_message_token"],
    "params": { ... }  # 初始状态描述
}
result = post_json(url, info)
messageid = result["returnData"]["messageid"]
```

#### 第二步：启动后台处理线程
```python
threading.Thread(target=async_task, args=(messageid, args, input)).start()
```

#### 第三步：异步任务主体逻辑
```python
def async_task(messageid, args, input):
    try:
        time.sleep(3)  # 模拟耗时操作
        
        update_info = {
            "messageid": messageid,
            "token": args["update_async_message_token"],
            "params": {
                "status": "done",
                "image_urls": [...],
                ...
            }
        }
        post_json("https://server.agentplus.cloud/back/api/update_async_message", update_info)
        
    except Exception as e:
        error_info = {
            "messageid": messageid,
            "token": args["update_async_message_token"],
            "params": {
                "status": "error",
                "content": f"error message: {str(e)}"
            }
        }
        post_json(..., error_info)
```

#### 异步处理流程图：
```
[AgentPlus] -- 发起异步请求 --> [Your Server]
              ↓ 创建初始消息 (create_async_message)
              ↓ 启动后台线程处理任务
              ↓ 完成后调用 update_async_message 更新状态
          <-- 返回初步接受信号 --
```

---

### 步骤6：实现 `/amcp/write_temp_file` 接口（写文件进用户 temp）

用 `args` 里自带的一次性 token `write_temp_file_token`，把一个文件写进**调用用户**的
temp 缓存区。文件最终落在 `用户文件夹/amcp/outer/<你决定的 path>` 下，用户能在
`/cms/temp` 页面查看 / 下载；若该 agent 开启了沙盒 `mount_user_temp`，沙盒里
`/cache/amcp/outer/...` 也能读到。

```python
@blueprint.route('/amcp/write_temp_file', methods=['POST'])
def sample_write_temp_file():
    import base64
    data = request.get_json()
    args = data["args"]; input = data["input"]

    token = args["write_temp_file_token"]          # 一次性,用完即焚
    path = input.get("path", "result.txt")          # 由你决定,可含子目录
    data_bytes = input.get("content", "").encode("utf-8")

    info = {
        "token": token,
        "path": path,                               # 落在 amcp/outer/<path>
        "content_base64": base64.b64encode(data_bytes).decode("ascii"),
    }
    resp = post_json("https://server.agentplus.cloud/back/api/write_temp_file", info)
    return jsonify({"statusCode": 1, "returnData": {"write_result": resp}})
```

请求体（你发给 agentplus 的）：

```json
{
    "token": "<write_temp_file_token>",
    "path": "reports/result.txt",
    "content_base64": "..."
}
```

返回：`{ "statusCode": 1, "returnData": { "relpath": "amcp/outer/reports/result.txt", "size": 123 } }`

要点：
- **path 由你决定**，可含多级子目录;后端逐段白名单校验，`..` 穿越会被拒。
- **不要传 userid / chatid** —— 后端从 token 反查，防止越权写到别人的 temp。
- 限制：单文件 ≤100MB，不超用户 temp 总配额（1GB）。
- 一次性：token 用一次即失效，**一次出站只能写一个文件**。
- 失败 `statusCode`：8=token 无效或已用 / 400=参数错 / 413=超配额或文件过大。

#### 写文件流程图：
```
[AgentPlus] -- args.write_temp_file_token --> [Your Server]
              ↓ 你生成文件字节 + base64 编码
              ↓ POST /back/api/write_temp_file {token, path, content_base64}
          <-- {relpath, size} --  文件落在 用户文件夹/amcp/outer/<path>
                                  (用户在 /cms/temp 可见)
```

---

### 步骤7：实现 `/amcp/write_struct_file` 接口（写文件进用户笔记本 struct）

把一个文件写进**调用用户**的结构化笔记本(struct)某页，作为一个 **file block** 落地
(字节上传云存储后出公开 URL)。与 `write_temp_file` 的关键差异是它属于**细粒度授权**：

> ⚠️ 用的 token `write_struct_file_token` 是 **按需下发** 的——只有当「发起本次调用的那个
> LLM block 此刻确实有 struct 写权限」(在 agentplus 里勾了 `accessStructs` 且允许新增 block)
> 时，它才会出现在 `args` 里。所以代码必须先判断它在不在，不在就说明本块没被授予笔记本写权限。

```python
@blueprint.route('/amcp/write_struct_file', methods=['POST'])
def sample_write_struct_file():
    import base64
    data = request.get_json()
    args = data["args"]; input = data["input"]

    token = args.get("write_struct_file_token")   # 按需下发,不在=本块无笔记本写权限
    if not token:
        return jsonify({"statusCode": 0, "message": "no struct write permission on this block"}), 400

    filename = input.get("filename", "result.txt")
    data_bytes = input.get("content", "").encode("utf-8")

    info = {
        "token": token,
        "filename": filename,
        "content_base64": base64.b64encode(data_bytes).decode("ascii"),
        # "pageid": input.get("pageid"),          # 可选,不传=写进笔记本根页
        # "block_order_index": input.get("idx"),  # 可选,不传=追加到页尾
    }
    resp = post_json("https://server.agentplus.cloud/back/api/write_struct_file", info)
    return jsonify({"statusCode": 1, "returnData": {"write_result": resp}})
```

请求体（你发给 agentplus 的）：

```json
{
    "token": "<write_struct_file_token>",
    "filename": "result.txt",
    "content_base64": "..."
}
```

返回：`{ "statusCode": 1, "returnData": { "contentid": 123, "pageid": 456, "file_url": "https://...", "filename": "result.txt" } }`

要点：
- **token 按需下发**：`args` 里没有 `write_struct_file_token` 就代表本块没被授予笔记本写权限，直接报错给 LLM 让它知道要去开 `accessStructs`。
- **作用域被冻结**：后端只允许写进该 block 被授权的笔记本范围(`root_page` + `editable_path`)。不传 `pageid` → 默认写进该笔记本根页；传别的笔记本 / 越界的 `pageid` 一律 **403**。
- **不要传 userid / chatid** —— 后端从 token 反查，防止越权写到别人的笔记本。
- 限制：单文件 ≤100MB。
- 一次性：token 用一次即失效，**一次出站只能写一个文件**。
- 失败 `statusCode`：8=token 无效或已用 / 400=参数错 / **403=目标页越出作用域** / 413=文件过大。

#### 写笔记本文件流程图：
```
[AgentPlus] -- args.write_struct_file_token(按需下发) --> [Your Server]
              ↓ 你生成文件字节 + base64 编码
              ↓ POST /back/api/write_struct_file {token, filename, content_base64, pageid?}
          <-- {contentid, pageid, file_url} --  文件作为 file block 落在用户笔记本页
                                                (作用域被冻结在授权的 root_page/editable_path 内)
```

---

## 三、部署建议

确保您的服务满足以下要求以便顺利集成至 AgentPlus 生态系统：

| 项目 | 描述 |
|------|------|
| 协议支持 | HTTPS 必须开启 |
| 认证方式 | Token 验证机制 |
| 数据格式 | 统一封装为 JSON 结构体 |
| 错误处理 | 明确 statusCode 和 message 字段 |

此外还应考虑添加日志记录模块便于调试追踪问题。

---

以上就是完整的 AMCP 接口开发文档。希望这份材料能够帮助您快速搭建属于自己的智能代理服务！如果您有任何疑问欢迎随时咨询技术支持团队获取更多细节信息。