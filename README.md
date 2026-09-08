# Async OJ

程序设计训练（Python）在线评测系统。后端使用 FastAPI，所有 API 路由均采用
`async def`；前端使用 Streamlit，并仅通过 REST API 与后端交互。

## 本地开发

要求 Python 3.10 或更高版本。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

另开一个终端启动前端：

```bash
source .venv/bin/activate
streamlit run streamlit_app.py
```

前端默认访问 `http://127.0.0.1:8000`，也可在页面侧边栏修改后端地址，
或在启动前设置 `OJ_API_URL`。前端 Session 按 Streamlit 用户会话隔离，
服务端 Cookie 会在页面重运行间保留。

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

密码经 bcrypt-SHA256 处理后存储，Session 由服务端数据库持久化并设置过期时间。
角色变更与审计日志在同一事务中完成，封禁用户时会立即撤销其已有 Session。

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

提交管理接口：

- `GET /api/submissions/{submission_id}`：提交者或管理员查询评测详情；
- `GET /api/submissions/`：按用户、题目和状态组合筛选，支持可选分页；
- `PUT /api/submissions/{submission_id}/rejudge`：管理员使用原提交 ID 发起重判。

评测日志与审计接口：

- `GET /api/submissions/{submission_id}/log`：查询总分和逐测试点评测日志；
- `GET /api/problems/{problem_id}/log_visibility`：管理员查询题目日志可见性；
- `PUT /api/problems/{problem_id}/log_visibility`：管理员配置题目测试点日志是否公开；
- `GET /api/logs/access/`：管理员按用户、题目或分页查询日志访问审计记录。

私有题目下，普通用户只能查看自己的总分，管理员可查看逐测试点详情；
开启公开后，所有已登录用户可查看该题的测试点日志。成功和被拒绝的日志访问均会记录审计状态。

Streamlit 前端页面：

- 注册、登录、登出和个人信息；
- 题目列表、详情、新建、编辑，以及管理员删除和日志可见性设置；
- 代码提交、提交列表、状态刷新、编译/运行/错误信息和评测日志；
- 管理员用户列表、角色修改、管理员创建、提交重判和访问审计。

系统启动时会注册 Python 和 C++14。后台评测使用 `asyncio` 子进程，逐测试点记录
AC、WA、RE、TLE、MLE 或 CE，限制运行时间、内存和输出长度。生产验收环境应为
Linux，并提供 `python3` 和支持 C++14 的 `g++`。服务重启时会自动恢复数据库中未完成的
`pending` 任务。

## 测试与静态检查

```bash
pytest
ruff check .
```

运行数据库、评测临时文件、虚拟环境和密钥配置均已加入 `.gitignore`，不得提交到仓库。
