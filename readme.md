
# AMCP (AgentPlus Model Context Protocol) 构建指南

AMCP 是一套用于与 AgentPlus 平台通信的标准协议，允许开发者将自定义服务接入平台并实现同步、异步以及文件处理等功能。本文档旨在指导用户如何基于 Flask 框架构建符合 AMCP 规范的服务接口，并通过示例说明其工作原理及应用场景。

## 一、核心组件概述

本示例代码展示了三种典型的 AMCP 使用场景：
1. **接收文件传输请求** (`/amcp/receive_file`)
2. **处理同步调用请求** (`/amcp/sync`)
3. **支持异步任务处理** (`/amcp/async`)

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