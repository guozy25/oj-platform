# Async OJ

程序设计训练（Python）在线评测系统。后端使用 FastAPI，所有 API 路由均采用
`async def`；前端将在后续阶段使用 Streamlit。

## 本地开发

要求 Python 3.10 或更高版本。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

服务启动时会自动创建运行目录、数据库表和课程要求的初始管理员账户。

```text
username: admin
password: admintestpassword
```

健康检查地址为 `GET /api/health`，交互式 API 文档地址为 `/docs`。

当前已实现用户阶段接口：

- `POST /api/users/`：注册普通用户；
- `POST /api/auth/login`、`POST /api/auth/logout`：服务端 Session 登录与登出；
- `GET /api/users/{user_id}`：查询本人信息，管理员可查询任意用户；
- `GET /api/users/`：管理员分页查询用户列表；
- `POST /api/users/admin`：管理员创建其他管理员；
- `PUT /api/users/{user_id}/role`：管理员修改角色并记录权限变更日志。

## 测试与静态检查

```bash
pytest
ruff check .
```

运行数据库、评测临时文件、虚拟环境和密钥配置均已加入 `.gitignore`，不得提交到仓库。
