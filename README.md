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
管理员可调用 `POST /api/reset/` 恢复自动测试所需的初始环境。该操作会取消后台任务，
清空用户、题目、提交、日志、AI 任务和自定义语言，恢复默认语言与初始管理员，并注销所有会话。

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
原子替换，运行时文件不会出现半写入状态。每道题都有 `code_length_limit`、
`time_limit` 和 `memory_limit` 三项资源限制，默认分别为 200000 字符、3 秒和
128 MB。只有管理员（老师）可显式设置这些字段；普通用户提交这些字段会被拒绝，
编辑其他题目内容时会保留原限制。代码长度在接收提交和实际评测前都会校验，
运行时间和内存限制由评测进程按题目配置执行。

评测控制接口：

- `GET /api/languages/`：查询已注册语言；
- `POST /api/languages/`：注册经过安全模板校验的新语言；
- `POST /api/submissions/`：创建评测任务并立即返回 `pending`。

动态语言命令只接受 `OJ_ALLOWED_LANGUAGE_EXECUTABLES` 中配置的可执行文件名，并禁止
shell 控制符、外部路径、内联代码、编译器插件和命令包装器。`{src}`、`{exe}` 必须作为
独立参数使用；执行前还会重新校验数据库中的命令，避免旧配置绕过注册检查。若验收环境安装了
其他编译器，可在 `.env` 中以逗号分隔追加允许的可执行文件名。

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
- AI 智能命题、生成进度、任务中断、历史任务和题目导入。

AI 智能命题接口：

- `GET` / `PUT /api/ai/model-config`：查询脱敏配置、更新 OpenAI 兼容模型配置；
- `POST /api/ai/problem-tasks/`：创建异步命题或已有题目改进任务；
- `GET /api/ai/problem-tasks/`：查询本人最近的命题任务，管理员可查看全部；
- `GET /api/ai/problem-tasks/{task_id}`：查询实时进度、结果和 Token/费用；
- `GET /api/ai/problem-tasks/{task_id}/revisions/`：查看每轮反馈和版本链；
- `GET /api/ai/problem-tasks/{task_id}/revisions/{revision}`：读取指定的已验证版本；
- `POST /api/ai/problem-tasks/{task_id}/refinements/`：基于指定版本追加反馈并生成下一版；
- `PUT /api/ai/problem-tasks/{task_id}/cancel`：真正取消模型调用和后续验证。

模型配置按用户隔离，API Key 只保存在后端进程内存中，既不写入数据库，也不会通过
查询响应、日志或错误信息返回；服务重启后需重新配置。Provider URL 可填写兼容 OpenAI
Chat Completions 的 API 根地址或完整的 `/chat/completions` 地址。Token 用量优先采用模型
响应中的实际值；提供商不返回用量时，系统会以字符数估算，并在页面明确标注。

生成过程会要求模型给出完整题目、Python 3 标准解、典型错误解和逐测试点覆盖目的。后端
严格校验 JSON 结构和代码安全规则，在受资源限制的子进程中执行标准解，重新计算所有样例
和测试点输出，并用测试点运行典型错误解。未通过结构、运行或区分度检查的草稿会携带验证
反馈自动修复一次。用户可在同一任务中追加多轮修改要求，每个通过验证的版本都保留父版本、
反馈、完整结果和累计 Token/费用，也可从任意历史版本分支继续修改。新版本生成失败或被取消时，
上一个已验证版本仍可查看、导出和继续修改；通过后可将任意版本导入题目页面人工审阅。

系统启动时会注册 Python 和 C++14。后台评测使用 `asyncio` 子进程，逐测试点记录
AC、WA、RE、TLE、MLE 或 CE，限制运行时间、内存和输出长度。生产验收环境应为
Linux，并提供 `python3` 和支持 C++14 的 `g++`。服务重启时会自动恢复数据库中未完成的
`pending` 任务。

## 测试与静态检查

```bash
pytest
ruff check .
```

## Linux 验收

最终验收环境需要 Linux、Python 3.10+、GCC 9+ 和 C++14 支持。在 Ubuntu/Debian 上可执行：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv g++
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
./scripts/linux_acceptance.sh
```

验收脚本会检查操作系统及编译器版本，执行 Ruff 和完整测试集，然后真正启动 Uvicorn，
通过 HTTP 完成健康检查、用户登录、题目创建、Python/C++14 评测和系统重置。仓库中的
`Linux acceptance` GitHub Actions 工作流会在 Ubuntu 24.04 的 Python 3.10 与 3.13 上执行
同一套流程，也可在 GitHub Actions 页面手动运行。

运行数据库、评测临时文件、虚拟环境和密钥配置均已加入 `.gitignore`，不得提交到仓库。
