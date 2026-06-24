from flask import Blueprint, request, jsonify
import os
import requests
import time
import random
import string
import json
import threading

blueprint = Blueprint('tools', __name__)


# ----------------------------------------------------------------
# AgentPlus 回调基地址 —— 所有回调(create/update async message、write temp/struct file)
# 都拼在这个 base 上。默认走官方云端,可用环境变量 AGENTPLUS_BASE_URL 覆盖:
#   - 自建/私有部署: AGENTPLUS_BASE_URL=https://your.agentplus.host
#   - 本地联调:      AGENTPLUS_BASE_URL=http://127.0.0.1:1000
# 支持从同目录 .env 读取(需 python-dotenv);没装 dotenv 也不影响,纯靠进程环境变量即可。
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

AGENTPLUS_BASE_URL = (os.environ.get("AGENTPLUS_BASE_URL") or "https://server.agentplus.cloud").rstrip("/")


# ----------------------------------------------------------------
# 暴露 amcp_config_sample/ 下的所有 JSON 配置,让 agentplus 端能"按 URL 自动获取描述",
# 不再需要用户手动复制粘贴 JSON。
#
# 返回结构:
#   { "tools": [ { "id": "<filename>", "config": {...} }, ... ] }
#
# CORS:开放 *,因为前端浏览器会直接来拉(避免再走 agentplus 服务端代理)。
@blueprint.route('/amcp/manifest', methods=['GET', 'OPTIONS'])
def sample_manifest():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        resp.headers.add("Access-Control-Allow-Origin", "*")
        resp.headers.add("Access-Control-Allow-Methods", "GET, OPTIONS")
        resp.headers.add("Access-Control-Allow-Headers", "Content-Type")
        return resp

    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amcp_config_sample")
    tools = []
    try:
        if os.path.isdir(config_dir):
            for filename in sorted(os.listdir(config_dir)):
                if not filename.endswith(".json"):
                    continue
                tool_id = filename[:-5]
                try:
                    with open(os.path.join(config_dir, filename), "r", encoding="utf-8") as f:
                        config = json.load(f)
                    tools.append({"id": tool_id, "config": config})
                except Exception:
                    # 单个坏文件不影响其他工具被列出
                    continue
        resp = jsonify({"statusCode": 1, "tools": tools})
    except Exception as e:
        resp = jsonify({"statusCode": 0, "message": f"Error: {str(e)}"})

    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp


# 向特定服务器发送json
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



# ----------------------------------------------------------------
# 接受来自agentplus的文件传输的sample
@blueprint.route('/amcp/receive_file', methods=['POST'])
def sample_receive_file():
    data = request.get_json()
    params = data["params"] # 在agentplus中设置的固定输入值
    args = data["args"] # 来自agentplus的会话信息
    input = data["input"] # agent的输入

    # -------------------
    # 接受一个文件的fileid
    file_url = input["file_url"]
    print(file_url)
    # 你的逻辑代码,读取文件,进行操作等....
    # .....
    # -------------------

    try:
        # 返回成功响应和文件名
        return jsonify({
            "statusCode": 1, 
            "returnData":{
                "params":params,
                "input":input,
                "args":args
            }
        })
    except Exception as e:
        return jsonify({"statusCode": 0, "message": f"Error: {str(e)}"}), 500






# ----------------------------------------------------------------
# 接受来自agentplus的普通调用
@blueprint.route('/amcp/sync', methods=['POST'])
def sample_sync():

    data = request.get_json()
    params = data["params"] # 在agentplus中设置的固定输入值
    args = data["args"] # 来自agentplus的会话信息
    input = data["input"] # agent的输入

    # -------------------
    # 你的代码...

    # -------------------

    try:
        # 返回成功响应和文件名
        return jsonify({
            "statusCode": 1, 
            "returnData":{
                "params":params,
                "input":input,
                "args":args
            }
        })
    except Exception as e:
        return jsonify({"statusCode": 0, "message": f"Error: {str(e)}"}), 500





# ----------------------------------------------------------------
# 把一个文件一次性写进【调用用户】的 temp 缓存区(落在 amcp/outer/ 下)。
#
# 用 args 里自带的一次性 token: write_temp_file_token
#   - 文件最终路径: 用户文件夹/amcp/outer/<你决定的 path>
#     path 可含多级子目录(如 "reports/2026/out.json"),后端逐段做白名单校验,
#     '..' 之类的穿越会被拒。
#   - 用户能在 /cms/temp 页面看到 / 下载; 若该 agent 开了沙盒 mount_user_temp,
#     沙盒里 /cache/amcp/outer/... 也能读到。
#   - 内容用 base64 编码提交。限制: 单文件 <=100MB,且不超用户 temp 总配额(1GB)。
#   - 一次性: 该 token 用一次即失效,一次出站只能写一个文件。
#   - 安全: 不要传 userid / chatid —— 后端从 token 反查,防止越权写到别人的 temp。
@blueprint.route('/amcp/write_temp_file', methods=['POST'])
def sample_write_temp_file():
    import base64

    data = request.get_json()
    params = data["params"]   # 在agentplus中设置的固定输入值
    args = data["args"]       # 来自agentplus的会话信息(含一次性 token)
    input = data["input"]     # agent的输入

    try:
        token = args.get("write_temp_file_token")
        if not token:
            return jsonify({"statusCode": 0, "message": "missing write_temp_file_token"}), 400

        # LLM 决定的相对路径 + 内容(实际可换成你服务生成的任意文件字节)
        path = input.get("path") or "amcp_demo.txt"
        content = input.get("content") or "hello from external amcp"
        data_bytes = content.encode("utf-8")

        # 提交到 agentplus: 文件会落在 用户文件夹/amcp/outer/<path>
        url = f"{AGENTPLUS_BASE_URL}/back/api/write_temp_file"
        info = {
            "token": token,
            "path": path,                                              # 由外部 AMCP 决定
            "content_base64": base64.b64encode(data_bytes).decode("ascii"),
        }
        # resp 形如 {"statusCode":1,"returnData":{"relpath":"amcp/outer/<path>","size":N}}
        # 失败 statusCode: 8=token无效或已用 / 400=参数错 / 413=超配额或文件过大
        resp = post_json(url, info)

        return jsonify({
            "statusCode": 1,
            "returnData": {
                "write_result": resp,
                "input": input
            }
        })
    except Exception as e:
        return jsonify({"statusCode": 0, "message": f"Error: {str(e)}"}), 500


# ----------------------------------------------------------------
# 把一个文件一次性写进【调用用户】的结构化笔记本(struct)指定页,作为一个 file block 落地。
#
# 用 args 里【按需下发】的一次性 token: write_struct_file_token
#   —— 与 write_temp_file_token 的关键差异: 这是【细粒度授权】。
#      该 token 只在「发起本次调用的那个 LLM block 此刻确实有 struct 写权限」
#      (在 agentplus 里勾了 accessStructs 且允许新增 block)时才会出现在 args 里。
#      所以代码里必须先判断它在不在 —— 不在就说明本块没被授予笔记本写权限。
#
#   - 文件落地形态: 笔记本某页上新建一个 file block,字节上传云存储后出公开 URL。
#   - 作用域被冻结: 后端只允许写进该 block 被授权的笔记本范围(root_page + editable_path)。
#     不传 pageid → 默认写进该笔记本根页; 传了别的笔记本 / 越界的 pageid 一律被拒(403)。
#   - 内容用 base64 编码提交。限制: 单文件 <=100MB。
#   - 一次性: 该 token 用一次即失效,一次出站只能写一个文件。
#   - 安全: 不要传 userid / chatid —— 后端从 token 反查,防止越权写到别人的笔记本。
@blueprint.route('/amcp/write_struct_file', methods=['POST'])
def sample_write_struct_file():
    import base64

    data = request.get_json()
    params = data["params"]   # 在agentplus中设置的固定输入值
    args = data["args"]       # 来自agentplus的会话信息(struct 写 token 按需下发)
    input = data["input"]     # agent的输入

    try:
        # 按需下发: 发起块没勾 accessStructs 写权限时,args 里【不会】有这个 token
        token = args.get("write_struct_file_token")
        if not token:
            return jsonify({
                "statusCode": 0,
                "message": "no write_struct_file_token in args: the calling block has no notebook(struct) write permission. "
                           "Enable 'accessStructs' (allow add block) on this block in agentplus."
            }), 400

        # LLM 决定的文件名 + 内容(实际可换成你服务生成的任意文件字节)
        filename = input.get("filename") or "amcp_demo.txt"
        content = input.get("content") or "hello from external amcp (struct file)"
        data_bytes = content.encode("utf-8")

        # 提交到 agentplus: 文件作为 file block 写进笔记本页
        url = f"{AGENTPLUS_BASE_URL}/back/api/write_struct_file"
        info = {
            "token": token,
            "filename": filename,
            "content_base64": base64.b64encode(data_bytes).decode("ascii"),
            # 可选: 不传则默认写进该笔记本根页; 传了必须落在 token 冻结的作用域内,否则 403
            # "pageid": input.get("pageid"),
            # 可选: block 在页内的顺序; 不传则追加到页尾
            # "block_order_index": input.get("block_order_index"),
        }
        # resp 形如 {"statusCode":1,"returnData":{"contentid":N,"pageid":N,"file_url":"...","filename":"..."}}
        # 失败 statusCode: 8=token无效或已用 / 400=参数错 / 403=目标页越出作用域 / 413=文件过大
        resp = post_json(url, info)

        return jsonify({
            "statusCode": 1,
            "returnData": {
                "write_result": resp,
                "input": input
            }
        })
    except Exception as e:
        return jsonify({"statusCode": 0, "message": f"Error: {str(e)}"}), 500


# ----------------------------------------------------------------
# 【进阶案例】异步生成 + 写进笔记本【指定页】,并演示「不消费 token 的 pageid 预检」。
#
# 这是把前面几块拼起来的真实范式(ComfyUI 异步出图就是这么做的):
#   (1) pageid 预检(fail-fast): 在做任何昂贵动作【之前】,先用一次【不消费 token】的
#       调用确认目标 pageid 落在 token 冻结的笔记本作用域内 —— 越权立刻返回,绝不白跑
#       后面的耗时任务。预检走 /back/api/write_struct_file/precheck,它【不会】消耗那个
#       一次性的 write_struct_file_token(只读、可反复查)。
#   (2) create_async_message: 先在聊天里建一张 running 卡片。
#   (3) 后台线程做耗时活(这里 sleep 模拟),生成文件字节。
#   (4) write_struct_file: 把文件写进【那一页】(带 pageid)。token 在这一步【才被消费】。
#   (5) 卡片终态由写入结果决定: 成功→done(用 AP 回的公开 file_url); 若该页在预检之后
#       被删/移走导致越权(403, TOCTOU)→ error,且仍把已生成的产物信息给出,避免成果丢失。
#
# 为什么要预检? write_struct_file_token 是【一次性写】(写一次即焚),不能"先试写一下看行
# 不行"。预检端点把「查作用域」和「那一次写」拆开 —— 查 scope 是只读、幂等的,不该消耗那次
# 写。任何外部 AMCP 在跑昂贵任务前都能复用这个预检来 fail-fast。
@blueprint.route('/amcp/async_generate_to_struct', methods=['POST'])
def sample_async_generate_to_struct():
    import base64

    data = request.get_json()
    params = data["params"]   # 在agentplus中设置的固定输入值
    args = data["args"]       # 来自agentplus的会话信息(struct 写 token 按需下发)
    input = data["input"]     # agent的输入

    started_time = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        # 按需下发的一次性写 token; 不在 = 本块没被授予笔记本写权限
        struct_token = args.get("write_struct_file_token")
        if not struct_token:
            return jsonify({
                "statusCode": 0,
                "message": "no write_struct_file_token in args: the calling block has no notebook(struct) write permission. "
                           "Enable 'accessStructs' (allow add block) on this block in agentplus."
            }), 400

        # 本案例写进【指定页】,所以要求 agent 传 pageid(用 accessStructs 工具浏览拿到)
        pageid = input.get("pageid")
        if pageid is None:
            return jsonify({
                "statusCode": 0,
                "message": "missing input.pageid: use the accessStructs tool to pick a target page id."
            }), 400

        # ---- (1) pageid 预检: 不消费 token,越权 fail-fast,不白跑后面的耗时活 ----
        precheck_url = f"{AGENTPLUS_BASE_URL}/back/api/write_struct_file/precheck"
        # 预检请求体只要 {token, pageid}; 不传 pageid 则按"默认根页"判定。
        # 返回 {"statusCode":1,"returnData":{"in_scope":true,...}} = 在范围内;
        #      越权 {"statusCode":403,"returnData":"<原因>"} / token 失效 {"statusCode":8}
        pre = post_json(precheck_url, {"token": struct_token, "pageid": pageid})
        if pre.get("statusCode") != 1:
            return jsonify({
                "statusCode": 0,
                "message": f"pageid precheck failed: {pre.get('returnData')}",
                "returnData": pre,
            }), 400

        # ---- (2) 建一张 running 卡片 ----
        create_url = f"{AGENTPLUS_BASE_URL}/back/api/create_async_message"
        created = post_json(create_url, {
            "chatid": args["chatid"],
            "token": args["create_async_message_token"],
            "params": {
                "title": "Generating + filing into notebook...",
                "content": f"target pageid: {pageid}",
                "status": "running",
                "image_urls": [],
                "additional_tags": {"started_time": started_time, "pageid": pageid},
            },
        })
        messageid = created["returnData"]["messageid"]

        # ---- (3)+(4)+(5) 后台: 耗时活 → 写指定页 → 卡片终态由写入结果决定 ----
        def worker(messageid, args, input, struct_token, pageid):
            update_url = f"{AGENTPLUS_BASE_URL}/back/api/update_async_message"
            try:
                # 你的耗时活(出图/渲染/转码...)。这里用 sleep + 一段文本模拟产物。
                time.sleep(3)
                filename = input.get("filename") or "amcp_demo.txt"
                content = input.get("content") or "hello from external amcp (async -> struct)"
                file_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")

                # 写进【那一页】。token 在这一步【才被消费】(一次性)。
                write_url = f"{AGENTPLUS_BASE_URL}/back/api/write_struct_file"
                wr = post_json(write_url, {
                    "token": struct_token,
                    "filename": filename,
                    "content_base64": file_b64,
                    "pageid": pageid,
                })
                rd = (wr or {}).get("returnData") or {}

                if wr.get("statusCode") == 1:
                    # 成功 → done,用 AP 回的公开 file_url 展示
                    post_json(update_url, {
                        "messageid": messageid,
                        "token": args["update_async_message_token"],
                        "params": {
                            "title": "Done - filed into notebook",
                            "content": "",
                            "status": "done",
                            "image_urls": [rd.get("file_url")] if rd.get("file_url") else [],
                            "additional_tags": {
                                "started_time": started_time,
                                "processed_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "pageid": rd.get("pageid"),
                                "contentid": rd.get("contentid"),
                            },
                        },
                    })
                else:
                    # 极少数 TOCTOU: 预检通过后该页被删/移走 → 写时才 403(或 token 被抢用=8)。
                    # 卡片翻 error,但仍把产物信息给出,避免成果丢失。
                    post_json(update_url, {
                        "messageid": messageid,
                        "token": args["update_async_message_token"],
                        "params": {
                            "title": "Generated, but NOT filed (pageid rejected)",
                            "content": f"write_struct_file rejected: statusCode={wr.get('statusCode')}, {wr.get('returnData')}",
                            "status": "error",
                            "additional_tags": {"pageid": pageid, "error": str(wr.get("returnData"))},
                        },
                    })
            except Exception as e:
                post_json(update_url, {
                    "messageid": messageid,
                    "token": args["update_async_message_token"],
                    "params": {"title": "Failed", "status": "error", "content": f"error: {str(e)}"},
                })

        t = threading.Thread(target=worker, args=(messageid, args, input, struct_token, pageid))
        t.daemon = True   # 守护线程,主线程结束时自动结束
        t.start()

        # 立刻返回"已开始"回执(异步: 图/文件完成后由后台线程回写卡片)
        return jsonify({
            "statusCode": 1,
            "returnData": {"message": "started; will file the result into the chosen notebook page when done."}
        })
    except Exception as e:
        return jsonify({"statusCode": 0, "message": f"Error: {str(e)}"}), 500


# ----------------------------------------------------------------
# 接受来自agentplus的异步调用sample用法
@blueprint.route('/amcp/async', methods=['POST'])
def sample_async():

    data = request.get_json()
    params = data["params"] # 在agentplus中设置的固定输入值
    args = data["args"] # 来自agentplus的会话信息
    input = data["input"] # agent的输入

    
    started_time = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        # -----------------------------------------
        # post a async message
        url = f"{AGENTPLUS_BASE_URL}/back/api/create_async_message"
        info = {
            "chatid":args["chatid"],
            "token":args["create_async_message_token"],
            "params":{
                "title":"Generating some images(for example)",
                "content":"description here ...",
                "status":"running",
                "image_urls":[],
                "additional_tags":{
                    "started_time": started_time
                    # any infomation you need ...
                }
            }
        }
        result = post_json(url, info)
        messageid = result["returnData"]["messageid"]
        # 获取messageid
        # 根据messageid修改刚才的信息
        # -------------------------------------------------------------------------

        def async_task(messageid, args, input):
            update_url = f"{AGENTPLUS_BASE_URL}/back/api/update_async_message"
            try:
                # 异步处理逻辑
                # ----------------------------
                # 一些逻辑代码...
                time.sleep(3)

                # ----------------------------
                update_info = {
                    "messageid": messageid,
                    "token": args["update_async_message_token"],
                    "params": {
                        "title":"Images generated!(for example)",
                        "content": "description here ...",
                        "status": "done",
                        "image_urls":["https://upload.wikimedia.org/wikipedia/commons/thumb/d/db/Alpaca_%2831562329701%29.jpg/1200px-Alpaca_%2831562329701%29.jpg"],
                        "additional_tags": {
                            "started_time": started_time,
                            "processed_time": time.strftime("%Y-%m-%d %H:%M:%S")
                            # any infomation you need ...
                        }
                    }
                }
                # 处理完成后，更新消息状态
                post_json(update_url, update_info)


            except Exception as e:
                # 处理失败时，更新消息状态为失败
                error_info = {
                    "messageid": messageid,
                    "token": args["update_async_message_token"],
                    "params": {
                        "title":"Images generation failed!(for example)",
                        "status": "error",
                        "content": f"error message: {str(e)}",
                        "additional_tags": {
                            "error": str(e)
                        }
                    }
                }
                post_json(update_url, error_info)

        # 启动异步任务
        thread = threading.Thread(target=async_task, args=(messageid, args, input))
        thread.daemon = True  # 设置为守护线程，主线程结束时会自动结束
        thread.start()
        # -------------------------------------------------------------------------

        # 返回成功响应,此处的信息将会直接传递给下一个block,
        return jsonify({
            "statusCode": 1, 
            "returnData":{
                "message":"Image generation started!(for example)"
            }
        })
    except Exception as e:
        return jsonify({"statusCode": 0, "message": f"Error: {str(e)}"}), 500













