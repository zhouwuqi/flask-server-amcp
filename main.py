from flask import Flask
from views import blueprint
import os


def create_app():
    app = Flask(__name__)
    
    # 注册蓝图
    app.register_blueprint(blueprint)
    return app

# 创建应用实例，供 gunicorn 使用
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3400, debug=True)