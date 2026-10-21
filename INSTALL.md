# 安装指南

需要的东西只有一样：**Python 3.10+**。不需要 Node.js（前端已预构建）。

## 先选一种方式

| 你的情况 | 用哪个 | 需要网络 |
|---|---|---|
| Windows，能上网 | [A. 一键安装](#a-一键安装windows推荐) | 是（约 60MB 依赖） |
| Windows，内网 / 网络差 / 完全离线 | [B. 离线安装](#b-离线安装windows) | **否** |
| Linux / macOS | [C. 手动安装](#c-手动安装linux--macos) | 是 |
| 想自己控制 Python 版本 | [C. 手动安装](#c-手动安装linux--macos) | 是 |

---

## A. 一键安装（Windows，推荐）

1. 解压发布包，得到 `zenith-v2` 目录
2. 双击 **`install.bat`**
   - 它会依次：校验 Python ≥ 3.10 → 建 `.venv` → 装 44 个依赖 → 从模板生成 `config/config.yaml`
   - 首次约 2–5 分钟，看网速
3. 双击 **`zenith.bat`** 启动，浏览器自动打开 <http://127.0.0.1:8766>
4. 在「设置」页填入你的 API Key（DeepSeek / 任意 OpenAI 兼容接口）

> PyPI 卡住时脚本会**自动回退阿里云镜像**，不用手动干预。
> `install.bat` 是幂等的，装一半失败可以直接重跑。

---

## B. 离线安装（Windows）

适用于内网机器、代理不稳、PyPI 长期超时，或者你就是不想让它联网。

### 1. 下载两个文件

到 [Releases](https://github.com/whathelio/zenith-v2/releases/latest) 页面：

| 文件 | 大小 | 是什么 |
|---|---|---|
| `zenith-v2-<版本>.zip` | 约 2 MB | 程序本体 |
| `zenith-v2-deps-win64-py313.zip` | 约 60 MB | 44 个依赖的 wheel 包 |

### 2. 摆放

把依赖包里的 `wheels` 目录解压到项目根，和 `install.bat` 并排：

```
zenith-v2\
├── install.bat
├── requirements.txt
├── wheels\                          ← 解压到这里
│   ├── numpy-2.5.1-cp313-cp313-win_amd64.whl
│   ├── pandas-3.0.3-cp313-cp313-win_amd64.whl
│   └── ...（共 44 个）
└── ...
```

### 3. 双击 `install.bat`

脚本检测到 `wheels\*.whl` 会自动切到离线模式：

```
pip install --no-index --find-links wheels -r requirements.txt
```

全程零网络请求。

### 适用条件（重要，先看一眼）

这 44 个 wheel 里有 10 个是**平台专属**的：

| 包 | wheel 标签 | 意味着 |
|---|---|---|
| numpy、pandas、pydantic_core、cffi、pyyaml、websockets、charset_normalizer | `cp313` | **必须是 Python 3.13** |
| curl_cffi、protobuf | `cp310-abi3` | Python 3.10+ 均可 |
| imageio_ffmpeg | `win_amd64` | **必须是 64 位 Windows** |

其余 **34 个是纯 Python（`py3-none-any`），合起来只有 4.8 MB**，任何平台都能用。

所以这个离线包限定：**Windows x64 + Python 3.13**。

**不是这个组合怎么办？** 两条路：

1. 删掉 `wheels\` 目录，重跑 `install.bat`，它会从 PyPI 装
2. 自己生成一份匹配你环境的：

   ```bat
   .venv\Scripts\python.exe -m pip download -r requirements.txt -d wheels
   ```

   然后重跑 `install.bat`。

### 校验完整性（可选）

依赖包附带 `zenith-v2-deps-win64-py313.sha256`：

```bat
certutil -hashfile zenith-v2-deps-win64-py313.zip SHA256
```

对比 Release 页面的 SHA256 值。

---

## C. 手动安装（Linux / macOS）

```bash
unzip zenith-v2-<版本>.zip && cd zenith-v2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python start.py 8766
```

或者用脚本（自动做完上面这些）：

```bash
bash install.sh
bash zenith.sh
```

国内网络加速：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

---

## 依赖到底装了什么

44 个包，装完约 **272 MB**，全部在 `zenith-v2/.venv/` 里。按用途分类：

| 类别 | 代表包 | 作用 |
|---|---|---|
| Web 服务 | fastapi、uvicorn、starlette、pydantic | HTTP 服务与数据校验 |
| 数据层 | peewee、pandas、numpy | SQLite ORM、行情与统计计算 |
| 网络抓取 | httpx、requests、curl_cffi、beautifulsoup4、yfinance | 网页抓取与行情数据源 |
| 配置与工具 | PyYAML、python-dateutil、pytz、tzdata、rich | 配置解析、时区、终端输出 |

**体积大头只有三个**：`imageio-ffmpeg` 29.8 MB（内含 ffmpeg 二进制）、`numpy` 11.9 MB、`pandas` 9.3 MB —— 合计占整个依赖的 84%。

依赖只装在项目内的 `.venv/`，**不碰系统 Python，不写注册表，删目录即卸载**。

---

## 常见问题

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `Python 3.10+ not found` | 没装 Python，或安装时没勾 PATH | 重装 Python，勾选「Add python.exe to PATH」，然后重开命令行窗口 |
| `No module named xxx` | 依赖没装完 | 重跑 `install.bat`；反复失败就删掉 `.venv` 再来一次 |
| `Offline install failed` | wheel 与当前 Python 版本不匹配 | 删掉 `wheels\`，改从 PyPI 装 |
| 启动后页面一片空白 | `frontend/dist` 缺失或损坏 | 确认 `frontend/dist/index.html` 存在；缺了重新解压发布包 |
| 端口 8766 被占用 | 已经有一个实例在运行 | `zenith.bat --status` 查看，`zenith.bat --stop` 停止 |
| 知识库页面显示离线 | RAG 网关不在本包内（可选组件） | 属预期行为，对话/记忆/笔记/日程全部不受影响，见 README 第 5 节 |
| 首次启动有「金十 Token」WARNING | 未配置财经数据 Token | 无害，只是财经日历同步跳过，不影响其他功能 |
| `install.bat` 窗口一闪就没了 | 可能被杀软拦截 | 右键 →「以管理员身份运行」，或用命令行执行看报错 |

---

## 数据在哪 / 怎么备份 / 怎么彻底卸载

| 内容 | 位置 |
|---|---|
| 数据库（对话、记忆、笔记、日程、目标） | `zenith-v2/data/zenith.db` |
| 配置（API Key、模型、外观） | `zenith-v2/config/config.yaml` 与 `.env` |
| 依赖 | `zenith-v2/.venv/` |
| 上传的背景图 | `zenith-v2/data/backgrounds/` |

- **备份**：复制 `data/zenith.db` 和 `config/` 就够了
- **升级**：保留 `data/` 与 `config/`，用新版覆盖其余文件
- **彻底卸载**：删掉整个 `zenith-v2` 目录。系统里不留任何残留（不含服务、不写注册表）

> ⚠️ 删库前先备份 `data/zenith.db`。那里存的是你全部的对话和记忆。

---

## 装完之后

- 使用说明与功能清单：[README.md](README.md)
- 安全边界（代码执行不是沙箱）：[SECURITY.md](SECURITY.md)
- 遇到问题：到 [Issues](https://github.com/whathelio/zenith-v2/issues) 提，附上 `zenith.log`
