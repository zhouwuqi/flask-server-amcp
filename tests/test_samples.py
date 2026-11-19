"""示例端点的冒烟测试,不需要真的 AgentPlus,也不联网。

FakeAgentPlus 按真平台的规矩回应回调:token 只能用一次、改卡片必须带 chatid 且卡片要在
这个聊天里、写笔记本只认作用域里的页、预检不消费 token。

    python -m unittest discover -s tests -v
"""
import base64
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agentplus as ap  # noqa: E402
import views  # noqa: E402
from main import create_app  # noqa: E402

CHATID = 501
IN_SCOPE_PAGES = {10, 11}


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.text = str(data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeAgentPlus:
    def __init__(self):
        self.used = set()
        self.cards = {}  # messageid -> {"chatid", "params"}
        self.temp_files = {}
        self.struct_files = []
        self.calls = []

    def args(self, struct=True):
        args = {
            "userid": 7, "chatid": CHATID,
            "user_info": {"email": "someone@example.com"},
            "create_async_message_token": "tok-create",
            "update_async_message_token": "tok-update",
            "write_temp_file_token": "tok-temp",
            "capabilities": ["return_images"],
            "vision_enabled": True,
        }
        if struct:
            args["write_struct_file_token"] = "tok-struct"
        return args

    def _use(self, token):
        if not token or token in self.used:
            return False
        self.used.add(token)
        return True

    def post(self, url, json=None, timeout=None, **_):
        path = url[len(ap.AGENTPLUS_BASE_URL):]
        self.calls.append(path)
        body = json or {}
        handler = {
            "/back/api/create_async_message": self._create,
            "/back/api/update_async_message": self._update,
            "/back/api/write_temp_file": self._write_temp,
            "/back/api/write_struct_file": self._write_struct,
            "/back/api/write_struct_file/precheck": self._precheck,
        }[path]
        return FakeResponse(handler(body))

    def _create(self, b):
        if not (b.get("token") == "tok-create" and self._use(b["token"])):
            return {"statusCode": 403, "returnData": 0}
        mid = 9000 + len(self.cards)
        self.cards[mid] = {"chatid": b.get("chatid"), "params": b.get("params")}
        return {"statusCode": 1, "returnData": {"messageid": mid}}

    def _update(self, b):
        ok = b.get("token") == "tok-update" and self._use(b["token"])
        card = self.cards.get(b.get("messageid"))
        if not (ok and b.get("chatid") is not None and card and card["chatid"] == b["chatid"]):
            return {"statusCode": 403, "returnData": 0}
        card["params"] = b["params"]
        return {"statusCode": 1}

    def _write_temp(self, b):
        if not (b.get("token") == "tok-temp" and self._use(b["token"])):
            return {"statusCode": 8, "returnData": "invalid or used token"}
        data = base64.b64decode(b["content_base64"])
        self.temp_files[b["path"]] = data
        return {"statusCode": 1, "returnData": {"relpath": "amcp/outer/" + b["path"], "size": len(data)}}

    def _precheck(self, b):
        if b.get("token") != "tok-struct" or b["token"] in self.used:
            return {"statusCode": 8, "returnData": "invalid or used token"}
        if b.get("pageid", 10) not in IN_SCOPE_PAGES:
            return {"statusCode": 403, "returnData": "page is outside the granted notebook"}
        return {"statusCode": 1, "returnData": {"in_scope": True, "root_page": 10, "pageid": b.get("pageid", 10)}}

    def _write_struct(self, b):
        if b.get("token") != "tok-struct" or b["token"] in self.used:
            return {"statusCode": 8, "returnData": "invalid or used token"}
        pageid = b.get("pageid", 10)
        if pageid not in IN_SCOPE_PAGES:
            return {"statusCode": 403, "returnData": "page is outside the granted notebook"}
        self._use(b["token"])
        self.struct_files.append((pageid, b["filename"], base64.b64decode(b["content_base64"])))
        return {"statusCode": 1, "returnData": {"contentid": 77, "pageid": pageid,
                                                "file_url": "https://files.example/x", "filename": b["filename"]}}


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.ap = FakeAgentPlus()
        patches = [
            mock.patch.object(ap.requests, "post", self.ap.post),
            mock.patch.object(views, "SIMULATED_WORK_SECONDS", 0),
            mock.patch.object(ap, "AMCP_SHARED_SECRET", ""),
            mock.patch.object(ap, "AMCP_PUBLIC_URL", ""),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = create_app().test_client()

    def call(self, path, tool_input=None, args=None, params=None):
        resp = self.client.post(path, json={
            "params": params or {}, "args": self.ap.args() if args is None else args,
            "input": tool_input or {}})
        return resp.status_code, resp.get_json()

    def wait_card(self, status, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            done = [c for c in self.ap.cards.values() if c["params"].get("status") == status]
            if done:
                return done[0]
            time.sleep(0.02)
        self.fail(f"no card reached status={status}: {self.ap.cards}")

    def test_manifest_lists_every_tool_with_absolute_urls(self):
        resp = self.client.get("/amcp/manifest", base_url="https://amcp.example.com")
        data = resp.get_json()
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")
        ids = {t["id"] for t in data["tools"]}
        self.assertEqual(ids, {f[:-5] for f in os.listdir(views.CONFIG_DIR) if f.endswith(".json")})
        self.assertIn("async", ids)
        for tool in data["tools"]:
            self.assertTrue(tool["config"]["url"].startswith("https://amcp.example.com/amcp/"), tool)
            self.assertIn("amcp_secret", tool["config"]["params"])

    def test_manifest_behind_proxy_and_public_url(self):
        resp = self.client.get("/amcp/manifest", headers={
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": "tools.example.org"})
        self.assertTrue(resp.get_json()["tools"][0]["config"]["url"].startswith("https://tools.example.org/amcp/"))
        with mock.patch.object(ap, "AMCP_PUBLIC_URL", "https://pub.example.net/x"):
            resp = self.client.get("/amcp/manifest")
        self.assertTrue(resp.get_json()["tools"][0]["config"]["url"].startswith("https://pub.example.net/x/amcp/"))

    def test_sync_does_not_echo_args_or_params(self):
        status, data = self.call("/amcp/sync", {"sample_id": 3, "sample_name": "abc"},
                                 params={"amcp_secret": "s3cret"})
        self.assertEqual(status, 200)
        self.assertEqual(data["returnData"]["sample_name_upper"], "ABC")
        raw = str(data)
        for leaked in ("tok-", "someone@example.com", "s3cret"):
            self.assertNotIn(leaked, raw)

    def test_shared_secret(self):
        with mock.patch.object(ap, "AMCP_SHARED_SECRET", "s3cret"):
            status, _ = self.call("/amcp/sync", {"sample_name": "a"})
            self.assertEqual(status, 401)
            status, _ = self.call("/amcp/sync", {"sample_name": "a"}, params={"amcp_secret": "nope"})
            self.assertEqual(status, 401)
            status, _ = self.call("/amcp/sync", {"sample_name": "a"}, params={"amcp_secret": "s3cret"})
            self.assertEqual(status, 200)

    def test_bad_envelope(self):
        resp = self.client.post("/amcp/sync", data="not json", content_type="text/plain")
        self.assertEqual(resp.status_code, 400)

    def test_receive_file(self):
        class Download:
            headers = {"Content-Type": "text/plain"}
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def raise_for_status(self): pass
            def iter_content(self, n): yield b"hello"
        with mock.patch.object(views.requests, "get", return_value=Download()):
            status, data = self.call("/amcp/receive_file", {"fileid": "1", "file_url": "https://f/x"})
        self.assertEqual(status, 200)
        self.assertEqual(data["returnData"]["size"], 5)
        status, data = self.call("/amcp/receive_file", {"fileid": "1"})
        self.assertEqual(status, 200)
        self.assertIn("error", data["returnData"])

    def test_async_card_reaches_done(self):
        status, data = self.call("/amcp/async")
        self.assertEqual(status, 200)
        card = self.wait_card("done")
        self.assertTrue(card["params"]["image_urls"])

    def test_write_temp_file_and_token_reuse(self):
        args = self.ap.args()
        status, data = self.call("/amcp/write_temp_file", {"path": "r/a.txt", "content": "hi"}, args=args)
        self.assertEqual(status, 200)
        self.assertEqual(data["returnData"]["saved"]["relpath"], "amcp/outer/r/a.txt")
        self.assertEqual(self.ap.temp_files["r/a.txt"], b"hi")
        # 同一个 token 第二次:平台拒,模型看到原因(HTTP 200 + error)
        status, data = self.call("/amcp/write_temp_file", {"path": "r/b.txt", "content": "hi"}, args=args)
        self.assertEqual(status, 200)
        self.assertIn("statusCode=8", data["returnData"]["error"])

    def test_write_struct_file(self):
        status, data = self.call("/amcp/write_struct_file", {"filename": "a.txt", "content": "x"},
                                 args=self.ap.args(struct=False))
        self.assertEqual(status, 200)
        self.assertIn("accessStructs", data["returnData"]["error"])
        status, data = self.call("/amcp/write_struct_file", {"filename": "a.txt", "content": "x", "pageid": 11})
        self.assertEqual(data["returnData"]["saved"]["pageid"], 11)

    def test_async_to_struct_out_of_scope_fails_fast(self):
        status, data = self.call("/amcp/async_generate_to_struct", {"pageid": 99})
        self.assertEqual(status, 200)
        self.assertIn("statusCode=403", data["returnData"]["error"])
        self.assertEqual(self.ap.cards, {})  # 没建卡片、没干活
        self.assertNotIn("tok-struct", self.ap.used)  # 预检不消费 token

    def test_async_to_struct_files_into_page(self):
        status, _ = self.call("/amcp/async_generate_to_struct", {"pageid": 11, "filename": "r.txt"})
        self.assertEqual(status, 200)
        card = self.wait_card("done")
        self.assertEqual(card["params"]["additional_tags"]["pageid"], 11)
        self.assertEqual(self.ap.struct_files[0][:2], (11, "r.txt"))

    def test_return_image(self):
        status, data = self.call("/amcp/return_image", {"values": [3, -1, 5]})
        images = data["returnData"]["__images__"]
        self.assertTrue(base64.b64decode(images[0]["data"]).startswith(b"\x89PNG"))
        self.assertIn("3, 0, 5", images[0]["caption"])
        args = self.ap.args()
        args["capabilities"] = []
        status, data = self.call("/amcp/return_image", {"values": [1]}, args=args)
        self.assertNotIn("__images__", data["returnData"])
        status, data = self.call("/amcp/return_image", {"values": ["x"]})
        self.assertIn("error", data["returnData"])


if __name__ == "__main__":
    unittest.main()
