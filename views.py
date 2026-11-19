"""示例 AMCP 端点。每个端点对应 AgentPlus 里的一个工具,配置在 amcp_config_sample/ 下。

建议按这个顺序读:sync → receive_file → async → write_temp_file → write_struct_file
→ async_generate_to_struct → return_image。协议说明见 readme.md,跟 AgentPlus
打交道的细节都在 agentplus.py。
"""
import hashlib
import json
import logging
import math
import os
import struct
import threading
import time
import zlib

import requests
from flask import Blueprint, jsonify, request

import agentplus as ap

log = logging.getLogger("amcp")
blueprint = Blueprint("amcp", __name__)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amcp_config_sample")

# 示例里「耗时的活」用 sleep 模拟,测试时改成 0
SIMULATED_WORK_SECONDS = 3


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------
# manifest:把 amcp_config_sample/ 下的工具配置一次性交给 AgentPlus。
# 用户在 AgentPlus 的「设置 → A-MCP」里填本服务的地址,点拉取,就能选工具,
# 不用手动复制粘贴 JSON。
#
# - 是浏览器直接来拉,所以要开 CORS。AgentPlus 用 https 打开时,这里也必须是 https,
#   否则浏览器会按「混合内容」拦掉。
# - 配置里写相对路径的 url(如 "/amcp/sync"),这里会补成本服务的对外地址;
#   写完整 URL 的保持原样。
# - 返回 {"statusCode": 1, "tools": [{"id": "<文件名>", "config": {...}}]}
@blueprint.route("/amcp/manifest", methods=["GET", "OPTIONS"])
def manifest():
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    else:
        base = ap.public_base_url()
        tools = []
        for filename in sorted(os.listdir(CONFIG_DIR)) if os.path.isdir(CONFIG_DIR) else []:
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(CONFIG_DIR, filename), encoding="utf-8") as f:
                    config = json.load(f)
            except (OSError, ValueError):
                log.warning("manifest: skipping unreadable config %s", filename)
                continue  # 一个坏文件不影响别的工具
            url = config.get("url")
            if isinstance(url, str) and url.startswith("/"):
                config["url"] = base + url
            tools.append({"id": filename[:-len(".json")], "config": config})
        resp = jsonify({"statusCode": 1, "tools": tools})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ----------------------------------------------------------------
# 同步调用:收参数、算、直接把结果返回给模型。
#
# - 平台最多等 30 秒,超时算失败。更久的活走下面的 /amcp/async。
# - returnData 里的东西就是模型看到的全部。别把 args / params 原样回显:
#   args 里有用户邮箱和一次性 token,params 里可能有密钥,回显就进了对话记录。
@blueprint.route("/amcp/sync", methods=["POST"])
@ap.amcp_tool
def sync(params, args, tool_input):
    sample_id = tool_input.get("sample_id")
    sample_name = str(tool_input.get("sample_name") or "")

    # 你的业务逻辑 ...

    return ap.ok({
        "sample_id": sample_id,
        "sample_name_upper": sample_name.upper(),
        "server_time": _now(),
    })


# ----------------------------------------------------------------
# 接收用户的文件。
#
# 模型传 fileid(聊天消息里 <FILES_ATTACHMENT> 块给出的那个),平台转发前把它换成
# input.file_url。只有调用用户自己的文件才会换;别人的 fileid 平台直接拒,不会调到这里。
# file_url 是能直接下载的地址。
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


@blueprint.route("/amcp/receive_file", methods=["POST"])
@ap.amcp_tool
def receive_file(params, args, tool_input):
    file_url = tool_input.get("file_url")
    if not file_url:
        return ap.fail("no file_url: pass the fileid shown in the chat's <FILES_ATTACHMENT> block")

    # 示例:下载下来,报大小、类型和 sha256。真实服务在这里做你的处理。
    digest, size = hashlib.sha256(), 0
    with requests.get(file_url, stream=True, timeout=30) as r:
        r.raise_for_status()
        content_type = r.headers.get("Content-Type")
        for chunk in r.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                return ap.fail(f"file is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)}MB")
            digest.update(chunk)

    return ap.ok({"size": size, "content_type": content_type, "sha256": digest.hexdigest()})


# ----------------------------------------------------------------
# 异步调用:先在聊天里建一张「进行中」卡片,马上返回;后台干完再把卡片改成 done / error。
#
# - 卡片只给用户看,不进模型上下文。模型这一轮拿到的只有下面 ok(...) 里那句话。
# - update token 只能用一次,卡片只能改一次,所以那一次就写终态。
# - token 24 小时后过期。
# - 后台线程跑在 worker 进程里,进程重启时没干完的活会丢,卡片会一直停在「进行中」。
#   正式服务请换成任务队列(RQ、Celery 等)。
DEMO_IMAGE = ("https://upload.wikimedia.org/wikipedia/commons/thumb/d/db/"
              "Alpaca_%2831562329701%29.jpg/1200px-Alpaca_%2831562329701%29.jpg")


@blueprint.route("/amcp/async", methods=["POST"])
@ap.amcp_tool
def async_task(params, args, tool_input):
    started = _now()
    messageid = ap.create_card(args, title="Generating an image (example)",
                               content="this takes a few seconds ...",
                               tags={"started_time": started})

    def work():
        try:
            time.sleep(SIMULATED_WORK_SECONDS)  # 你的耗时活
            ap.finish_card(args, messageid, status="done", title="Image generated (example)",
                           image_urls=[DEMO_IMAGE],
                           tags={"started_time": started, "processed_time": _now()})
        except Exception as e:
            log.exception("async task failed")
            _finish_with_error(args, messageid, "Image generation failed (example)", str(e))

    threading.Thread(target=work, daemon=True).start()
    return ap.ok({"message": "started; the result will appear as a card in the chat"})


def _finish_with_error(args, messageid, title, error, tags=None):
    """后台出错时把卡片翻成 error。如果之前那次 finish_card 已经送到平台,
    token 就用掉了,这次会被拒;只记日志,别让后台线程再抛。"""
    try:
        ap.finish_card(args, messageid, status="error", title=title,
                       error_message=error, tags=tags)
    except Exception:
        log.exception("could not mark card %s as error", messageid)


# ----------------------------------------------------------------
# 写一个文件进调用用户的 temp,落在 amcp/outer/<path>,用户在 /cms/temp 能看到、下载。
# 如果 agent 的沙盒开了 mount_user_temp,沙盒里 /cache/amcp/outer/... 也能读到。
#
# - path 由你决定,可以带子目录;平台逐段校验,".." 之类会被拒。
# - 不用传 userid / chatid:平台从 token 反查用户,你也就写不到别人那里。
@blueprint.route("/amcp/write_temp_file", methods=["POST"])
@ap.amcp_tool
def write_temp_file(params, args, tool_input):
    path = tool_input.get("path") or "amcp_demo.txt"
    content = tool_input.get("content") or "hello from external amcp"

    saved = ap.write_temp_file(args, path, content.encode("utf-8"))
    return ap.ok({"saved": saved, "where": "the user's temp files (/cms/temp)"})


# ----------------------------------------------------------------
# 写一个文件进调用用户的笔记本,作为一个文件块。
#
# 跟 write_temp_file 的区别:这个 token 是按需发的。只有发起调用的 agent 此刻能往笔记本里
# 加块(开了 accessStructs 且允许新增块)时,args 里才有 write_struct_file_token;
# 而且它被锁在那本笔记本的可写范围里,越界的 pageid 一律 403。
@blueprint.route("/amcp/write_struct_file", methods=["POST"])
@ap.amcp_tool
def write_struct_file(params, args, tool_input):
    if not ap.has_struct_write(args):
        return ap.fail(ap.NO_STRUCT_WRITE)

    filename = tool_input.get("filename") or "amcp_demo.txt"
    content = tool_input.get("content") or "hello from external amcp (notebook file)"
    saved = ap.write_struct_file(args, filename, content.encode("utf-8"),
                                 pageid=tool_input.get("pageid"))
    return ap.ok({"saved": saved})


# ----------------------------------------------------------------
# 进阶:异步生成,写进笔记本里指定的那一页。ComfyUI 异步出图就是这个流程。
#
#   (1) 预检 pageid:不消费 token,越界马上返回,不白跑后面的耗时活
#   (2) 建一张「进行中」卡片
#   (3) 后台干活,生成文件
#   (4) 写进那一页,token 这时才被消费
#   (5) 卡片终态看写入结果。预检之后那一页被删或移走的话,写入会被拒;
#       卡片翻成 error,并把已生成的东西说清楚,别让成果悄悄丢了。
#
# 为什么要预检:写笔记本的 token 只能用一次,没法「先试写一下」。
# 预检把「查范围」和「那一次写」分开,查范围只读,可以反复问。
@blueprint.route("/amcp/async_generate_to_struct", methods=["POST"])
@ap.amcp_tool
def async_generate_to_struct(params, args, tool_input):
    if not ap.has_struct_write(args):
        return ap.fail(ap.NO_STRUCT_WRITE)
    pageid = tool_input.get("pageid")
    if pageid is None:
        return ap.fail("missing pageid: use the accessStructs tool to find or create the target page")

    ap.precheck_struct_page(args, pageid)  # (1) 越界会抛 AgentPlusError,模型会看到原因

    started = _now()
    messageid = ap.create_card(args, title="Generating, then filing into the notebook ...",  # (2)
                               content=f"target page: {pageid}",
                               tags={"started_time": started, "pageid": pageid})
    filename = tool_input.get("filename") or "amcp_demo.txt"
    content = tool_input.get("content") or "hello from external amcp (async → notebook page)"

    def work():
        try:
            time.sleep(SIMULATED_WORK_SECONDS)  # (3) 你的耗时活
            data = content.encode("utf-8")
            try:
                saved = ap.write_struct_file(args, filename, data, pageid=pageid)  # (4)
            except ap.AgentPlusError as e:
                _finish_with_error(args, messageid, "Generated, but not filed into the notebook",
                                   f"{filename} ({len(data)} bytes) was generated, but the write "
                                   f"was rejected: {e.detail}", tags={"pageid": pageid})
                return
            ap.finish_card(args, messageid, status="done", title="Done: filed into the notebook",  # (5)
                           content=saved.get("filename") or filename,
                           tags={"started_time": started, "processed_time": _now(),
                                 "pageid": saved.get("pageid"), "contentid": saved.get("contentid")})
        except Exception as e:
            log.exception("async_generate_to_struct failed")
            _finish_with_error(args, messageid, "Failed", str(e), tags={"pageid": pageid})

    threading.Thread(target=work, daemon=True).start()
    return ap.ok({"message": "started; the file will be added to the chosen notebook page when done"})


# ----------------------------------------------------------------
# 把图片传回聊天:同步返回的 returnData 里放 __images__。
#
#   returnData = {...你的字段..., "__images__": [{"data": "<base64>", "name": "...", "caption": "..."}]}
#
# 平台收到后:
# - 校验格式(只收 PNG / JPEG / GIF / WebP)和大小,统一转成各家模型都收的尺寸;
# - 存进这个聊天,聊天里出一条图片消息,用户看得到;
# - 调用方的模型能看图的话,图进上下文,模型这一轮就能看到;
# - __images__ 本身会从模型看到的结果里拿掉,换成一段摘要。
# 限制:一次最多 8 张,单张 10MB 以内,合计 20MB 以内。
#
# 一定先看 ap.can_return_images(args)。旧版 AgentPlus 不认 __images__,
# 会把几 MB 的 base64 当文本塞给模型。
@blueprint.route("/amcp/return_image", methods=["POST"])
@ap.amcp_tool
def return_image(params, args, tool_input):
    values = tool_input.get("values") or [3, 7, 2, 9, 4]
    if isinstance(values, str):
        values = values.replace(",", " ").split()
    try:
        numbers = [float(x) for x in values]
    except (TypeError, ValueError):
        return ap.fail("values must be a list of numbers, e.g. [3, 7, 2]")
    # 跟图保持一致:负数画成 0,inf / nan 跳过,最多 24 根柱子;说明文字也按这一份数写
    values = [max(0.0, v) for v in numbers if math.isfinite(v)][:24]

    result = {
        "message": f"bar chart of {len(values)} values generated",
        # 调用方模型此刻能不能看图。看不见时图照样显示给用户;只有「取图就是为了给模型看」
        # 的场景,才值得据此省掉生成或下载。
        "vision_enabled": args.get("vision_enabled"),
    }
    if not ap.can_return_images(args):
        result["note"] = "this AgentPlus does not accept returned images, so the chart was not sent"
        return ap.ok(result)

    result["__images__"] = [ap.image_item(
        _bar_chart_png(values), name="bar_chart.png",
        caption="bar chart of: " + ", ".join(f"{v:g}" for v in values))]
    return ap.ok(result)


def _bar_chart_png(values, width=480, height=240):
    """只用标准库画一张柱状图(不带文字),代表「服务端生成的图」。"""
    values = values or [1.0]
    top = max(values) or 1.0
    n, pad = len(values), 16
    slot = (width - 2 * pad) / n
    gap = max(2, int(slot * 0.2))
    heights = [int((height - 2 * pad) * v / top) for v in values]
    bg, bar, axis = b"\xff\xff\xff", b"\x25\x63\xeb", b"\xa0\xa0\xa0"
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")  # 每行开头的 0 = PNG filter type None
        for x in range(width):
            color = bg
            if y == height - pad and pad <= x < width - pad:
                color = axis
            elif pad <= x < width - pad and y < height - pad:
                i = int((x - pad) // slot)
                if i < n and (x - pad) - i * slot < slot - gap and y >= height - pad - heights[i]:
                    color = bar
            row += color
        rows.append(bytes(row))

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
