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
@blueprint.route('/amcp/sync', methods=['POST'])
def sample_sync():
    # 异步调用sample用法
    data = request.get_json()
    params = data["params"] # 在agentplus中设置的固定输入值
    args = data["args"] # 来自agentplus的会话信息
    input = data["input"] # llm的输入值 -> Dict

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
@blueprint.route('/amcp/async', methods=['POST'])
def sample_async():
    # 异步调用sample用法

    data = request.get_json()
    params = data["params"] # 在agentplus中设置的固定输入值
    args = data["args"] # 来自agentplus的会话信息
    input = data["input"] # block的输入值


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



    
    started_time = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        # -----------------------------------------
        # post a async message
        url = "https://server.agentplus.cloud/back/api/create_async_message"
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
            update_url = "https://server.agentplus.cloud/back/api/update_async_message"
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













