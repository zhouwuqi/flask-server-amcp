"""AMCP 示例服务的入口。本地开发:python main.py;部署:./run.sh(gunicorn + gevent)。"""
import logging
import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from views import blueprint


def create_app():
    app = Flask(__name__)
    # 放在 nginx / caddy 后面时,让 request.url_root 用对外的协议、域名和路径前缀
    # (manifest 拿它补全工具的 url)。也可以直接配 AMCP_PUBLIC_URL。
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_prefix=1)
    app.register_blueprint(blueprint)
    return app


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# gunicorn 用的应用实例
app = create_app()

if __name__ == "__main__":
    # 调试模式默认关:Werkzeug 调试器能在网页里执行任意代码,绝不能对外开。
    # 本地要用就 FLASK_DEBUG=1 python main.py。
    app.run(host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "3400")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
