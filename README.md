# 爱弥斯 · 飞书聊天机器人

一个跑在自己电脑上的飞书机器人。单文件主程序，连你的飞书账号和 DeepSeek，
用 WebSocket 长连接，不需要服务器、不需要公网 IP、不需要域名。

她叫爱弥斯——不是因为她会回答「你好，我是 AI 助手」，
而是因为她会因为你打字慢半拍，会说半句就笑场，会在凌晨一点到早八点之间彻底消失。

> 这个仓库的重点不是「接了个大模型」。
> 是 [`docs/像人的细节.md`](docs/像人的细节.md)：延迟、遗忘、沉默、稀缺——怎么让一个 LLM 不像是 LLM。

---

## 它能做什么

- **私聊**：只有你一个人能跟她聊天，别人的私聊只会收到一句固定身份话术。
- **群聊**：被 @ 才说话，没被 @ 就只记着。被叫到之后五分钟内会接话。
- **记忆**：跨会话记住你。三层压缩（原始对话 → 记忆摘要 → 摘要再压缩），不会忘也不会爆上下文。
- **主动找你**：安静满四小时之后，一天最多主动开口两次。你没回复就等 12 小时。
- **发图**：图库里攒着你给她的图，每张标了「适合什么时候发」。她自己挑，40 分钟最多一张。
- **看网**：内置 `web_search` / `web_fetch`，只在真需要的时候用。
- **记事**：你说「下周三体检」，她记下来，到点会提。

## 它不做什么

- 不托管、不联网部署。就跑在你自己电脑上，关机即下线。
- 不收集任何数据。所有记忆都在本地的 `memory.json` 里，除了 DeepSeek 和飞书，不发给第四方。
- 不带图片素材。`images/` 已被 `.gitignore` 排除——你自己往里放图。

---

## 快速开始

### 0. 准备

- Windows（脚本是 .bat/.ps1/.vbs；Linux/macOS 见文末）
- Python 3.9+
- 一个飞书账号，能创建企业自建应用
- 一个 DeepSeek API key（[platform.deepseek.com](https://platform.deepseek.com)）

### 1. 装依赖

```bash
pip install -r requirements.txt
```

### 2. 创建飞书应用

1. 打开[飞书开放平台](https://open.feishu.cn/app)，创建「企业自建应用」。
2. **凭证与基础信息** → 抄下 `App ID` 和 `App Secret`。
3. **权限管理** → 开通：
   - `im:message`（获取与发送单聊、群组消息）
   - `im:message:send_as_bot`
   - `im:resource`（上传图片）
   - `contact:user.base:readonly`（读群成员昵称，可选）
4. **事件与回调** → 订阅方式选「**使用长连接接收事件**」→ 添加事件
   `接收消息 im.message.receive_v1`。
   （选长连接就不用配公网回调地址，这是这个项目能塞进一台家用电脑的前提。）
5. **版本管理与发布** → 创建版本并发布。企业内自己用的话，自己审批一下即可。

### 3. 填配置

```bash
copy config.example.py config.py     # Windows
cp config.example.py config.py       # macOS / Linux
```

打开 `config.py`，把三行「填入你的…」替换掉：

```python
APP_ID     = "cli_xxxxxxxxxxxx"
APP_SECRET = "xxxxxxxxxxxxxxxxxxxx"
DEEPSEEK_KEY = "sk-xxxxxxxxxxxx"
```

`config.py` 在 `.gitignore` 里，不会被提交。

### 4. 启动

```bash
python aemeath.py
```

看到 `正在连接飞书……` 和 `connected to wss` 就是连上了。
第一次启动是空的：没有记忆、没有图库，她把你当第一次见面的人。
日志在 `aemeath.log`。

### 5. 认领主人

**装好之后，自己先私聊机器人一句。**

第一个私聊她的会话会被认成「主人」，记进 `owner.json`。
以后只有这个会话能跟她聊天，其他人私聊只会收到 `ID_LINE` 那句身份话术。

> 想换主人：删掉 `owner.json`，然后重新私聊一次。
> 想手动指定：把 `OWNER_CHAT_ID` 填成你的 `oc_...`（群里 @ 她之后看日志能拿到）。

### 6. 后台运行（可选）

```bash
start_bot.bat
```

无窗口启动，20 秒左右连上。开着 `start_bot.bat` 的窗口按提示判断是否连上。

想开机自启：

```bash
install_task.bat      # 注册计划任务
disable_autostart.bat # 取消
```

---

## 改她的人格

人格词全在 [`persona.md`](persona.md)，**改完重启即可生效，不用动代码**。

`aemeath.py` 启动时读这个文件注入 system prompt。想完全换成另一个角色，
直接改写 `persona.md` 就行——代码里没有任何一句角色专属逻辑。

文件里的 `[系统时间]`、`[可发图库]`、`[我自己先开口]`、`[图:0037]`
是**程序注入的标记**，别删，人格词需要知道怎么理解它们。

几个容易踩的点：

- **别把人格词写太长**。它每轮都在 system prompt 里，越长越贵，而且
  超过一定长度之后模型对开头几句的注意力会下降。这个仓库自带的约 7000 字。
- **别在人格词里写「你要像真人一样」**。没用。写具体的：几条、多长、什么时候闭嘴。
- **示例只给调子**。人格词末尾那些「A → B」都是语气示范，
  必须明写「不是台词，别照背」，否则模型会逐字复读。

---

## 项目结构

```
aemeath.py         主程序。单文件，2000 行
persona.md         人格词。改她性格只改这个
config.example.py  配置模板 → 复制成 config.py
config.py          你的凭证（不进 git）
requirements.txt   依赖

start_bot.bat      无窗口启动
start_hidden.vbs   实际拉起进程的 VBS
stop_bot.bat       停止
install_task.bat   注册开机自启
disable_autostart.bat  取消自启
view_log.bat       打开日志

docs/像人的细节.md   设计笔记：延迟 / 遗忘 / 沉默 / 稀缺

运行时生成（都不进 git）：
memory.json  记忆      图库.json   图库索引
群记忆.json  群上下文  群昵称.json 群昵称缓存
主动.json    主动消息状态  _seen.json  去重表
owner.json   主人身份   aemeath.log 日志
images/库存/  图片库存
```

---

## 常见问题

**连不上，日志里没有 `connected to wss`**
检查三步：事件订阅方式是不是「长连接」、`im.message.receive_v1` 有没有添加、
应用版本有没有发布。改了权限必须重新发布版本才生效。

**她不理我**
`owner.json` 没认领成功。删掉它，重新私聊一次。
或者看日志里有没有 `[拦下] 陌生单聊` —— 说明这个会话不是主人。

**群里 @ 了没反应**
检查是否 @ 的是机器人本身（不是另一个同名成员），
以及 `im:message` 权限是否已生效（改权限后要重新发布）。

**回复很慢**
默认 `MODEL = "deepseek-flash"`。想快可以换更小的模型，想好可以换大的，
改 `aemeath.py` 顶部的 `MODEL` 常量。

**成本**
一轮对话 = system（人格 + 记忆）+ 最近几轮 + 你的消息。
人格词 7000 字约 3500–4000 token。靠 `build_static()` 把时间挪到末尾来命中 prompt cache，
命中的部分按缓存价计费。日常聊天一天几毛钱。

**能部署到服务器吗**
代码本身可以，但要改掉 `start_hidden.vbs` 和 `.bat` 那套 Windows 脚本，
用 `systemd` 或 `supervisor` 拉起 `python aemeath.py` 即可。
长连接模式不需要公网 IP，一台 1 核机器足够。

---

## 版权与声明

- **代码**：MIT，见 [LICENSE](LICENSE)。
- **人格词**：基于《鸣潮》的非官方同人创作，角色版权归**库洛游戏**所有。
  本项目与库洛游戏无关联、未获授权。详见 [NOTICE](NOTICE)。
- **图片素材**：不包含。
- 文中所有语气示例均为自行撰写，不是官方台词。

如果你要基于这个仓库做自己的角色，`persona.md` 请自己重写——
把别人的角色词直接拿去商用，风险在自己身上。

---

## 致谢

- [lark-oapi](https://github.com/larksuite/oapi-sdk-python)：飞书官方 Python SDK，长连接这块省了很多事。
- DeepSeek：便宜，够用，能扛住一天几千轮的 persona 对话。
