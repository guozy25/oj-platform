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

题目管理接口：

- `GET /api/problems/`、`GET /api/problems/{problem_id}`：题目列表与详情；
- `POST /api/problems/`、`PUT /api/problems/{problem_id}`：新增与完整更新题目；
- `DELETE /api/problems/{problem_id}`：管理员删除题目。

题目配置保存在 `problems/` 下，每题一个 UTF-8 JSON 文件。写入采用临时文件和
原子替换，运行时文件不会出现半写入状态。

评测控制接口：

- `GET /api/languages/`：查询已注册语言；
- `POST /api/languages/`：注册经过安全模板校验的新语言；
- `POST /api/submissions/`：创建评测任务并立即返回 `pending`。

系统启动时会注册 Python 和 C++14。后台评测使用 `asyncio` 子进程，逐测试点记录
AC、WA、RE、TLE、MLE 或 CE，限制运行时间、内存和输出长度。生产验收环境应为
Linux，并提供 `python3` 和支持 C++14 的 `g++`。

## 测试与静态检查

```bash
pytest
ruff check .
```

运行数据库、评测临时文件、虚拟环境和密钥配置均已加入 `.gitignore`，不得提交到仓库。
