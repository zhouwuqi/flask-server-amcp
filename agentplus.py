"""跟 AgentPlus 打交道的小工具：读调用信封、校验共享密钥、调回调接口、回图。

views.py 里的示例端点都用它。自己写 AMCP 服务时，可以把整个文件拷过去用。
每个回调函数都只发一次 POST，请求体原样写在函数里，读函数就能看到协议的样子。
"""
import base64
import functools
import hmac
import logging
import os

import requests
from flask import jsonify, request

try:  # 可选:从同目录 .env 读配置;没装 python-dotenv 就只认进程环境变量
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("amcp")

# 回调基地址。建卡片、改卡片、写 temp、写笔记本都发到这里。
# 默认是官方云端;自建部署或本地联调时改 env。
AGENTPLUS_BASE_URL = (os.environ.get("AGENTPLUS_BASE_URL") or "https://server.agentplus.cloud").rstrip("/")

# 共享密钥(可选)。AgentPlus 调你的服务时不带任何认证头,知道 URL 的人都能调。
# 配了它,每次调用的 params.amcp_secret 必须和它一致。params 是用户在 AgentPlus 里
# 给这个工具填的固定值,只发给你的服务器,模型看不到。不配 = 不校验(本地联调方便)。
AMCP_SHARED_SECRET = os.environ.get("AMCP_SHARED_SECRET") or ""
SECRET_PARAM = "amcp_secret"

# 本服务对外的地址,manifest 用它把工具配置里的相对 url 补全。
# 不配就用请求进来时的地址(放在反代后面时,反代要转发 X-Forwarded-Proto / X-Forwarded-Host)。
AMCP_PUBLIC_URL = (os.environ.get("AMCP_PUBLIC_URL") or "").rstrip("/")


# ─── 返回给 AgentPlus ────────────────────────────────────────────────────────

def ok(data):
    """成功。returnData 原样交给模型,dict 会被转成 JSON 文本。
    平台不看 statusCode,模型看到的只有 returnData。"""
    return jsonify({"statusCode": 1, "returnData": data})


def fail(message, http_status=200):
    """失败。

    http_status=200(默认):模型在工具结果里看到 {"error": message},能自己改参数重试,
        或者跟用户解释。适合参数不对、没权限这种模型能处理的错。
    非 2xx:平台还会在聊天里给用户显示一条红色报错(模型也能看到响应内容)。
        适合配置错、服务端故障这种得让人来处理的错。
    """
    return jsonify({"statusCode": 0, "returnData": {"error": message}}), http_status


def _secret_ok(params):
    if not AMCP_SHARED_SECRET:
        return True
    got = str(params.get(SECRET_PARAM) or "")
    return hmac.compare_digest(got.encode("utf-8"), AMCP_SHARED_SECRET.encode("utf-8"))


def amcp_tool(fn):
    """把 fn(params, args, tool_input) 变成一个 AMCP 端点。

    AgentPlus 每次调用都 POST 同一种信封:
        {"params": {...},   用户在 AgentPlus 里给这个工具填的固定值
         "args":   {...},   平台给的会话信息和一次性 token
         "input":  {...}}   模型按 function.parameters 填的参数
    这里负责拆信封、校验共享密钥、兜住异常;fn 只管业务。
    """
    @functools.wraps(fn)
    def endpoint():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return fail("request body must be a JSON object: {params, args, input}", 400)
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        tool_input = data.get("input") if isinstance(data.get("input"), dict) else {}
        if not _secret_ok(params):
            return fail(f"wrong or missing params.{SECRET_PARAM}: set it in this tool's "
                        f"config in AgentPlus (Settings → A-MCP)", 401)
        try:
            return fn(params, args, tool_input)
        except AgentPlusError as e:
            # AgentPlus 拒了回调(token 用过 / 越权 / 文件太大…):把原因告诉模型
            log.warning("%s: %s", fn.__name__, e)
            return fail(str(e))
        except Exception as e:
            log.exception("%s failed", fn.__name__)
            # 示例里把异常原文回出去方便调试;正式服务可以只回一句「内部错误」
            return fail(f"{type(e).__name__}: {e}", 500)
    return endpoint


def public_base_url():
    """本服务对外的根地址(不带末尾 /)。"""
    return AMCP_PUBLIC_URL or request.url_root.rstrip("/")


# ─── 调 AgentPlus 的回调接口 ─────────────────────────────────────────────────

class AgentPlusError(Exception):
    """AgentPlus 回调接口拒了这次请求。

    status 是平台给的 statusCode:8 = token 无效、用过或过期 / 400 = 参数错 /
    403 = 越出作用域 / 413 = 文件太大或超配额。"""

    def __init__(self, path, status, detail):
        super().__init__(f"AgentPlus {path} rejected the request (statusCode={status}): {detail}")
        self.path = path
        self.status = status
        self.detail = detail


def call_agentplus(path, body, timeout=60):
    """POST 一个 AgentPlus 回调接口,成功时返回 returnData。

    平台拒绝时 HTTP 状态码仍是 200,原因写在 body 的 statusCode / returnData 里,
    所以只看 HTTP 状态码不够。"""
    resp = requests.post(AGENTPLUS_BASE_URL + path, json=body, timeout=timeout)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        raise AgentPlusError(path, None, f"non-JSON response: {resp.text[:200]}")
    if not isinstance(data, dict):  # 平台内部出错时回的是字符串 "0"
        raise AgentPlusError(path, None, data)
    if data.get("statusCode") != 1:
        raise AgentPlusError(path, data.get("statusCode"), data.get("returnData"))
    return data.get("returnData")


def _token(args, name):
    token = args.get(name)
    if not token:
        raise ValueError(f"args.{name} is missing (this endpoint expects to be called by AgentPlus)")
    return token


# 聊天里的异步卡片。卡片只给用户看,不进模型上下文。

def create_card(args, title, content="", image_urls=None, tags=None):
    """在发起调用的那个聊天里建一张「进行中」卡片,返回 messageid。token 只能用一次。"""
    returned = call_agentplus("/back/api/create_async_message", {
        "chatid": args["chatid"],
        "token": _token(args, "create_async_message_token"),
        "params": {
            "title": title,
            "content": content,
            "status": "running",
            "image_urls": image_urls or [],
            "additional_tags": tags or {},
        },
    })
    return returned["messageid"]


def finish_card(args, messageid, status, title, content="", image_urls=None,
                error_message=None, tags=None):
    """把卡片改成终态,status 是 "done" 或 "error"。

    - 必须带 chatid:平台要核对 messageid 确实是这个聊天里的卡片,不带直接拒(403)。
    - update token 只能用一次:一次调用只能改一次卡片,所以直接写终态。
    - params 会整个替换卡片内容,不是合并;要保留的字段每次都写全。
    """
    card = {
        "title": title,
        "content": content,
        "status": status,
        "image_urls": image_urls or [],
        "additional_tags": tags or {},
    }
    if error_message:
        card["error_message"] = error_message  # status=error 时卡片上单独显示
    call_agentplus("/back/api/update_async_message", {
        "chatid": args["chatid"],
        "messageid": messageid,
        "token": _token(args, "update_async_message_token"),
        "params": card,
    })


# 写文件。base64 提交,单个文件不超过 100MB;token 只能用一次,所以一次调用只能写一个文件。

def write_temp_file(args, path, data, timeout=300):
    """把 data(bytes)写进调用用户的 temp,落在 amcp/outer/<path>,用户在 /cms/temp 能看到。
    path 可以带子目录;不能超过用户 temp 的总配额。
    返回 {"relpath": "amcp/outer/<path>", "size": N}。"""
    return call_agentplus("/back/api/write_temp_file", {
        "token": _token(args, "write_temp_file_token"),
        "path": path,
        "content_base64": base64.b64encode(data).decode("ascii"),
    }, timeout=timeout)


def has_struct_write(args):
    """发起调用的 agent 此刻能不能往笔记本里加块。不能的话,平台根本不发这个 token。"""
    return bool(args.get("write_struct_file_token"))


NO_STRUCT_WRITE = ("This tool writes into the user's notebook, but the agent calling it has no "
                   "notebook write access here. Ask the user to enable accessStructs "
                   "(allow adding blocks) for this agent.")


def precheck_struct_page(args, pageid=None):
    """只问一句「pageid 在不在可写范围里」,不消费 token,可以反复问。
    不在范围里抛 AgentPlusError(status=403)。不传 pageid = 问笔记本根页。"""
    body = {"token": _token(args, "write_struct_file_token")}
    if pageid is not None:
        body["pageid"] = pageid
    return call_agentplus("/back/api/write_struct_file/precheck", body)


def write_struct_file(args, filename, data, pageid=None, block_order_index=None, timeout=300):
    """把 data(bytes)作为一个文件块写进调用用户的笔记本。
    pageid 不传 = 笔记本根页;越出授权范围会被拒(403)。block_order_index 不传 = 追加到页尾。
    返回 {"contentid", "pageid", "file_url", "filename"},file_url 是公开地址。"""
    body = {
        "token": _token(args, "write_struct_file_token"),
        "filename": filename,
        "content_base64": base64.b64encode(data).decode("ascii"),
    }
    if pageid is not None:
        body["pageid"] = pageid
    if block_order_index is not None:
        body["block_order_index"] = block_order_index
    return call_agentplus("/back/api/write_struct_file", body, timeout=timeout)


# ─── 把图片传回聊天(同步返回里的 __images__) ─────────────────────────────────

def can_return_images(args):
    """平台认不认 returnData.__images__。旧版不认,会把整段 base64 当文本塞给模型,
    上下文当场爆掉。所以先看这个,再决定回不回图。"""
    return "return_images" in (args.get("capabilities") or [])


def image_item(data, name=None, caption=None):
    """__images__ 里的一项。data 是图片字节(PNG / JPEG / GIF / WebP)。"""
    item = {"data": base64.b64encode(data).decode("ascii")}
    if name:
        item["name"] = name
    if caption:
        item["caption"] = caption  # 模型在工具结果里看得到
    return item
