# -*- coding: utf-8 -*-
"""
爱弥斯 · 飞书聊天机器人
接 DeepSeek，带记忆，分条发。

要改的地方只有 config.py：飞书 App ID / App Secret、DeepSeek API Key。
人格词在 persona.md，改完重启就生效。
"""
import os
import sys
import time

_BASE = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.path.join(_BASE, "aemeath.log")


def _stamp() -> str:
    return time.strftime("[%H:%M:%S] ")


class _Stamped:
    """给每一行前面加时间戳。

    日志里最常被问的就是「这是什么时候说的」。没有时间戳，只能靠前后
    的 Lark 行去猜。这里按整行加，不完整的行先攒着。
    """

    def __init__(self, stream):
        self.s = stream
        self.buf = ""

    def write(self, text):
        if not text:
            return 0
        self.buf += text
        cut = self.buf.rfind("\n")
        if cut < 0:
            return len(text)
        head, self.buf = self.buf[:cut + 1], self.buf[cut + 1:]
        for line in head.splitlines(keepends=True):
            try:
                # lark 自己的日志本来就带日期和时间，不再重复盖一个
                quiet = not line.strip() or line.startswith("[Lark]")
                self.s.write(("" if quiet else _stamp()) + line)
            except Exception:
                pass
        try:
            self.s.flush()
        except Exception:
            pass
        return len(text)

    def flush(self):
        if self.buf:
            try:
                self.s.write(_stamp() + self.buf)
                self.buf = ""
            except Exception:
                pass
        try:
            self.s.flush()
        except Exception:
            pass

    def isatty(self):
        return False


def _setup_log():
    """pythonw 下没有控制台，print 得落到文件里，不然出错了什么都看不见。"""
    try:
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > 3 * 1024 * 1024:
            os.replace(_LOG_PATH, _LOG_PATH + ".old")
    except Exception:
        pass
    try:
        f = open(_LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        return

    if sys.stdout is None:          # 没有控制台，直接写文件
        sys.stdout, sys.stderr = _Stamped(f), _Stamped(f)
        return

    class _Tee:
        def __init__(self, a, b):
            self.a, self.b = a, b

        def write(self, s):
            for x in (self.a, self.b):
                try:
                    x.write(s)
                except Exception:
                    pass
            try:
                self.b.flush()
            except Exception:
                pass

        def flush(self):
            for x in (self.a, self.b):
                try:
                    x.flush()
                except Exception:
                    pass

    sys.stdout = _Stamped(_Tee(sys.stdout, f))
    sys.stderr = _Stamped(_Tee(sys.stderr, f))


if __name__ == "__main__":
    _setup_log()

import base64
import json
import os
import random
import re
import threading
import time
from datetime import datetime

import requests
import lark_oapi as lark
from lark_oapi.api.im.v1 import *

# ================= 配置 =================
# 凭证不写在代码里，放在同目录的 config.py（已在 .gitignore 中，不会被提交）。
# 第一次运行前：把 config.example.py 复制成 config.py，填上你自己的值。
try:
    from config import APP_ID, APP_SECRET, DEEPSEEK_KEY
except ImportError:
    raise SystemExit(
        "缺少 config.py。\n"
        "请先复制模板再填写：\n"
        "    copy config.example.py config.py    (Windows)\n"
        "    cp config.example.py config.py      (macOS / Linux)\n"
        "然后填入你的飞书 App ID、App Secret 和 DeepSeek API Key。"
    )

API_URL = "https://api.deepseek.com/chat/completions"
MODEL   = "deepseek-flash"      # 换模型改这里
MAX_IMG = 6 * 1024 * 1024       # 超过 6MB 的图不送模型，太贵
# ========================================

# 忘了替换模板占位符的话，飞书那边只会丢一句看不懂的错误，不如在这里先说清楚。
for _k, _v in (("APP_ID", APP_ID), ("APP_SECRET", APP_SECRET),
               ("DEEPSEEK_KEY", DEEPSEEK_KEY)):
    if not _v or "填入" in str(_v):
        raise SystemExit(
            f"config.py 里的 {_k} 还没填。\n"
            "把「填入你的…」换成你自己的值，再重新启动。"
        )


# ================= 人格 =================
# 人格词在 persona.md 里。改完重启就生效，不用动代码。
_PERSONA_FILE = os.path.join(_BASE, "persona.md")


def _load_persona(path: str = _PERSONA_FILE) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        raise SystemExit(f"找不到人格文件：{path}")
    if not text:
        raise SystemExit(f"人格文件是空的：{path}")
    return text


SYSTEM = _load_persona()
# ========================================

client = lark.Client.builder() \
    .app_id(APP_ID) \
    .app_secret(APP_SECRET) \
    .build()

# 去重：记忆那些已经处理过的 message_id。
# 只放内存里是不够的 —— 一次重启就全忘，飞书重推的旧事件会被当成新消息再回一遍。
_SEEN_FILE = os.path.join(_BASE, "_seen.json")
_SEEN_MAX = 500
_seen = []
_seen_index = set()


def _load_seen():
    try:
        with open(_SEEN_FILE, encoding="utf-8") as f:
            for mid in json.load(f):
                if mid and mid not in _seen_index:
                    _seen_index.add(mid)
                    _seen.append(mid)
    except Exception:
        pass


def _mark_seen(mid: str) -> bool:
    """记下这条消息。已经见过返回 False。"""
    if not mid or mid in _seen_index:
        return False
    _seen_index.add(mid)
    _seen.append(mid)
    if len(_seen) > _SEEN_MAX:
        for x in _seen[:-_SEEN_MAX]:
            _seen_index.discard(x)
        del _seen[:-_SEEN_MAX]
    try:
        with open(_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(_seen, f)
    except Exception:
        pass
    return True


# ---------------- 消息收发 ----------------

def send_text(chat_id: str, text: str):
    body = (CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build())
    req = (CreateMessageRequest.builder()
           .receive_id_type("chat_id")
           .request_body(body)
           .build())
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[发送失败] code={resp.code} msg={resp.msg}")
    return resp


# ---------------- 发表情图 ----------------

IMG_DIR = os.path.join(_BASE, "images")
IMG_NAMES = []          # 内置图已删，现在只发图库里的（他喂过来的）

# 她话里的 [图:海豹] / 【图：海豹】
_IMG_RE = re.compile(r"[\[【]\s*图\s*[:：]\s*([^\]】\n]{1,12}?)\s*[\]】]")


# ---------------- 图库（他喂过来的图） ----------------

LIB_DIR = os.path.join(IMG_DIR, "库存")
LIB_FILE = os.path.join(_BASE, "图库.json")
LIB_MAX_ITEMS = 60      # 存满这么多，最旧的连文件一起删
LIB_MAX_HINT = 20       # 一次最多给她看几条候选
PIC_GAP_SEC = 40 * 60   # 两次发图至少隔四十分钟
PIC_DAY_MAX = 6         # 一天最多发六张

_lib_items = None
_lib_lock = threading.Lock()
_pic_state = {"last_ts": 0.0, "day": "", "count": 0}

DESCRIBE_PROMPT = (
    "你是图片标注器，只做一件事：看这张图，输出两行。\n"
    "第一行：这图里是什么（不超过 15 个字）\n"
    "第二行：这图适合什么时候发（不超过 15 个字，写成「他……的时候」）\n"
    "只输出这两行，不要编号，不要解释，不要客套。"
)


def load_lib():
    global _lib_items
    if _lib_items is not None:
        return _lib_items
    items = []
    try:
        with open(LIB_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            items = data.get("items") or []
            _pic_state.update(data.get("state") or {})
        elif isinstance(data, list):
            items = data
    except Exception:
        items = []
    _lib_items = items
    return items


def save_lib():
    with _lib_lock:
        tmp = LIB_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"items": _lib_items or [], "state": _pic_state},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, LIB_FILE)
        except Exception as e:
            print(f"[图库] 写入失败：{e}")


def describe_image(data: bytes):
    """看一次图，写两句话。这是唯一看图的时机，之后只读文字。"""
    content = [
        {"type": "text", "text": DESCRIBE_PROMPT},
        {"type": "image_url", "image_url": {"url": to_data_url(data)}},
    ]
    try:
        msg = _chat([{"role": "user", "content": content}],
                    temperature=0.2, max_tokens=200, thinking=False)
    except Exception as e:
        print(f"[图库] 标注失败：{e}")
        return "", ""
    lines = [x.strip() for x in (msg.get("content") or "").splitlines() if x.strip()]
    what = lines[0] if lines else ""
    when = lines[1] if len(lines) > 1 else ""
    if not when:
        when = "(拿不准)"
    return what[:30], when[:30]


def add_to_lib(data: bytes, what: str, when: str) -> str:
    items = load_lib()
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
           "image/webp": ".webp"}.get(guess_mime(data), ".jpg")
    used = {it.get("id") for it in items}
    num = 1
    while f"{num:04d}" in used:
        num += 1
    iid = f"{num:04d}"
    os.makedirs(LIB_DIR, exist_ok=True)
    with open(os.path.join(LIB_DIR, iid + ext), "wb") as f:
        f.write(data)
    items.append({"id": iid, "file": iid + ext, "what": what, "when": when,
                  "t": datetime.now().isoformat(timespec="seconds")})
    while len(items) > LIB_MAX_ITEMS:
        old = items.pop(0)
        try:
            os.remove(os.path.join(LIB_DIR, old.get("file", "")))
        except Exception:
            pass
        print(f"[图库] 满了，丢掉最旧的 {old.get('id')}")
    save_lib()
    print(f"[图库] 收下一张 {iid}：{what} / {when}")
    return iid


def store_image_async(data: bytes):
    """存图 + 标注，放到后台线程，别耽误回话。"""
    def job():
        what, when = describe_image(data)
        add_to_lib(data, what or "(没看清)", when or "(拿不准)")
    threading.Thread(target=job, daemon=True).start()


def find_image(name: str):
    if name in IMG_NAMES:
        return os.path.join(IMG_DIR, name + ".png")
    for it in load_lib():
        if it.get("id") == name:
            return os.path.join(LIB_DIR, it.get("file", ""))
    return None


def pic_allowed() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    if _pic_state.get("day") != today:
        _pic_state["day"] = today
        _pic_state["count"] = 0
    if _pic_state.get("count", 0) >= PIC_DAY_MAX:
        return False
    return time.time() - float(_pic_state.get("last_ts") or 0) >= PIC_GAP_SEC


def build_pic_hint(chat_id: str) -> str:
    items = load_lib()
    if not items or not pic_allowed():
        return ""
    recent = items[-LIB_MAX_HINT:]
    lines = " | ".join(f"{it['id']} {it.get('when') or '?'}" for it in reversed(recent))
    return f"[可发图库] 你手边这些图（编号 适合什么时候发）：{lines}"


def note_pic_sent():
    _pic_state["last_ts"] = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    if _pic_state.get("day") != today:
        _pic_state["day"] = today
        _pic_state["count"] = 0
    _pic_state["count"] = int(_pic_state.get("count") or 0) + 1
    save_lib()


def send_image(chat_id: str, name: str) -> bool:
    """上传一张本地图并发出去。飞书要两步：先换 image_key，再发 image 消息。

    name 可以是内置图的名字（海豹、晚安…），也可以是图库里的四位编号（0037）。
    """
    path = find_image(name)
    if not path:
        print(f"[发图失败] 没有这张图：{name}")
        return False
    try:
        with open(path, "rb") as f:
            body = (CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build())
            up = client.im.v1.image.create(
                CreateImageRequest.builder().request_body(body).build())
        if not up.success():
            print(f"[发图失败] 上传 code={up.code} msg={up.msg}")
            return False
        body2 = (CreateMessageRequestBody.builder()
                 .receive_id(chat_id)
                 .msg_type("image")
                 .content(json.dumps({"image_key": up.data.image_key}))
                 .build())
        r = client.im.v1.message.create(
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body2)
            .build())
        if not r.success():
            print(f"[发图失败] 发送 code={r.code} msg={r.msg}")
            return False
        if name not in IMG_NAMES:
            note_pic_sent()
        return True
    except Exception as e:
        print(f"[发图异常] {e}")
        return False


def split_images(text: str):
    """把 [图:海豹] 摘出来：返回（去掉标记的正文, 图名列表）"""
    names = [n.strip() for n in _IMG_RE.findall(text)]
    clean = re.sub(r"\n{2,}", "\n", _IMG_RE.sub("", text)).strip()
    return clean, names


def deliver(chat_id: str, reply: str) -> None:
    """分条发正文，再发她点的图。"""
    text, names = split_images(reply)
    # 图库编号是内部 id，不许出现在话里。提示词挡不住的漏网之鱼在这里清掉。
    text = re.sub(r"(?<!\d)0\d{3}(?!\d)", "", text)
    bubbles = split_bubbles(text) if text else []
    t0 = time.time()
    sent = 0
    for i, part in enumerate(bubbles):
        if i:
            time.sleep(random.uniform(0.7, 2.0))
        try:
            if send_text(chat_id, part).success():
                sent += 1
        except Exception as e:
            print(f"[发送失败] {e}")
    for name in names:
        time.sleep(random.uniform(0.5, 1.2))
        if send_image(chat_id, name):
            sent += 1
    tag = f"含图 {len(names)} 张 " if names else ""
    print(f"[发出 {sent} 条 {tag}{time.time() - t0:.1f}s]")


def clean_markdown(t: str) -> str:
    """飞书文字消息不渲染 Markdown，去掉符号"""
    t = re.sub(r"[*#`>]", "", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def _cut_sentences(text: str, limit: int = 60):
    """一行太长，就在行内按句末标点切开，再合并到 limit 字以内。"""
    if len(text) <= 90:
        return [text]
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?~…])", text) if s.strip()]
    parts, buf = [], ""
    for s in sents:
        if len(buf) + len(s) <= limit:
            buf += s
        else:
            if buf:
                parts.append(buf)
            buf = s
    if buf:
        parts.append(buf)
    return parts or [text]


def strip_tool_calls(text: str) -> str:
    """模型偶尔不真调工具，而是把调用写成文本吐出来。这种残渣不许发出去。

    要放在 clean_markdown 前面 —— 那边会把 > 当 Markdown 符号删掉，
    删完就认不出 <tool_call> 了。
    """
    t = re.sub(r"<tool_call[^>]*>.*?(?:</tool_call>|$)", "", text, flags=re.S)
    t = re.sub(r"</?tool_call[^>]*>?", "", t)
    keep = []
    for line in t.split("\n"):
        s = line.strip()
        if s.startswith("{") and s.endswith("}") and '":' in s:
            print(f"[工具] 丢掉伪装的调用参数：{s[:60]}")
            continue
        keep.append(line)
    return "\n".join(keep)


_PAREN_ONLY = re.compile(r"^[（(]\s*(.+?)\s*[）)]$", re.S)


def strip_paren_actions(text: str) -> str:
    """兜底：她偶尔会把一整句话括起来当旁白。

    提示词里已经写了不许，但模型不听话的时候得有人拦。
    短括号（「（笑）」这种）整条丢掉；长的说明是句正话，只把括号剥掉。
    """
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        m = _PAREN_ONLY.match(s)
        if m:
            inner = m.group(1).strip()
            if len(inner) <= 8:
                print(f"[括号] 丢掉短旁白：{s}")
                continue
            print(f"[括号] 剥掉外层括号：{inner}")
            s = inner
        out.append(s)
    result = "\n".join(out)
    if result:
        return result
    # 全被剥光了（例如「（笑）好啊（歪头）」被当成整段旁白）：
    # 退一步只删括号本身，把正文留住，否则这条消息就成了空白。
    fallback = re.sub(r"[（(][^）)]*[）)]", "", text).strip()
    if fallback:
        print(f"[括号] 全部剥光，退回去括号：{fallback}")
        return fallback
    return ""


def split_bubbles(text: str):
    """把一段话切成几条，模拟分条发。

    两条原则：
    1. 她自己按了换行的地方，就是她想分条的地方 —— 照发，别再合并。
       她写「外行……行吧。／他还挺能说的。／那张你要不要？」就是要三条。
    2. 碎句不单独发。不到五个字的碎片是断句，不是分条，并回上一条。
       「哟。」「嗯。」「是啊。」这种是起头，不是一条独立的话。
       但「外行……行吧。」七个子，那是一个完整的拍子，要单独发。

    只有单行超过 90 字时，才在这一行内部按句号再切。
    """
    text = strip_paren_actions(clean_markdown(strip_tool_calls(text)))
    if not text:
        return []

    lines = [x.strip() for x in text.split("\n") if x.strip()]

    if len(lines) >= 2:
        parts = []
        for ln in lines:
            if parts and len(parts[-1]) < 5:
                parts[-1] += ln
            else:
                parts.append(ln)
        # 开头就是碎句（「哟。」）的话，并进下一条
        while len(parts) >= 2 and len(parts[0]) < 5:
            parts[1] = parts[0] + parts[1]
            parts.pop(0)
        while len(parts) > 4:
            parts[-2] += parts[-1]
            parts.pop()
        out = []
        for p in parts:
            out.extend(_cut_sentences(p))
        return out[:5]

    return _cut_sentences(lines[0] if lines else text)[:4]


# ---------------- 联网 ----------------

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": ("上网搜东西。只在需要查最近发生的事、实时信息、"
                            "或者你不确定的事实时调用。闲聊、感慨、讲道理时不要调用。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索词。短一点，像人会在搜索框里打的那种。",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "打开一个具体网页，读它的正文。漂泊者发链接过来时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网址"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_event",
            "description": ("他提到将来某天要做的事，就记下来：考试、面试、体检、"
                            "约了人、要搬家、要早起。只有在有明确日期（或「明天」"
                            "「后天」「下周三」这种能换算成日期的说法）时才调用。"
                            "闲聊、感慨、没头没尾的一句话，不要调用。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {"type": "string",
                             "description": "哪一天，写成 2026-10-24 这种格式"},
                    "what": {"type": "string",
                             "description": "什么事，短一点，比如「交医用物理作业」"},
                },
                "required": ["when", "what"],
            },
        },
    },
]


def _auth() -> dict:
    return {
        "Authorization": f"Bearer {DEEPSEEK_KEY}",
        "Content-Type": "application/json",
    }


def ds_search(query: str, count: int = 4) -> list:
    r = requests.post(
        SEARCH_URL,
        headers=_auth(),
        json={"query": query, "count": count, "need_content": False},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
    return r.json().get("results") or []


def ds_fetch(url: str) -> dict:
    r = requests.post(FETCH_URL, headers=_auth(), json={"url": url}, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
    return r.json()


def run_tool(name: str, args: dict) -> str:
    if name == "web_search":
        q = (args.get("query") or "").strip()
        if not q:
            return "搜索词是空的"
        try:
            hits = ds_search(q)
        except Exception as e:
            print(f"[搜索] 失败 {e}")
            return f"搜不了：{e}"

        print(f"[搜索] {q} → {len(hits)} 条")
        if not hits:
            return "什么都没搜到"

        parts = []
        for h in hits:
            day = (h.get("date") or "")[:10]
            head = f"【{h.get('title', '')}】" + (f" {day}" if day else "")
            body = h.get("summary") or h.get("snippet") or ""
            parts.append(f"{head}\n{body}\n{h.get('url', '')}")
        return "搜到这些：\n\n" + "\n\n".join(parts)

    if name == "web_fetch":
        url = (args.get("url") or "").strip()
        if not url.startswith("http"):
            return "这不像个网址"
        try:
            page = ds_fetch(url)
        except Exception as e:
            print(f"[抓取] 失败 {e}")
            return f"打不开：{e}"

        text = (page.get("content") or "")[:4000]
        print(f"[抓取] {url} → {len(text)} 字")
        return f"《{page.get('title', '')}》\n\n{text}"

    if name == "remember_event":
        return add_event(args.get("when", ""), args.get("what", ""))

    return "没有这个工具"


# ---------------- 记忆 ----------------

MEM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
_mem = {}
_mem_loaded = False

WEEK = "一二三四五六日"


def load_memory(quiet: bool = False):
    global _mem, _mem_loaded
    try:
        with open(MEM_FILE, "r", encoding="utf-8") as f:
            _mem = json.load(f)
    except Exception:
        _mem = {}
    _mem_loaded = True
    if not quiet:
        print(f"[记忆] 载入 {len(_mem)} 个会话")


def ensure_memory():
    """外部脚本（preview / 测试）import 本模块后直接调 ask_model 时，
    _mem 是空的。以前这种情况下 save_memory() 会把整个 memory.json
    覆盖成只剩测试会话，真会话全被抹掉。这里强制先读盘。"""
    if not _mem_loaded:
        load_memory(quiet=True)


_MEM_LOCK = threading.Lock()


def save_memory():
    """每条消息现在各跑一个线程，写盘必须串行，否则两个线程同时写会写坏。
    顺手做成原子写：先落 .tmp，再 os.replace 顶上去。"""
    ensure_memory()
    with _MEM_LOCK:
        try:
            tmp = MEM_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_mem, f, ensure_ascii=False, indent=2)
            os.replace(tmp, MEM_FILE)
        except Exception as e:
            print(f"[记忆] 保存失败：{e}")


def get_mem(chat_id: str) -> dict:
    ensure_memory()
    if chat_id not in _mem:
        _mem[chat_id] = {}
    m = _mem[chat_id]
    m.setdefault("summary", "")
    m.setdefault("last_seen", "")
    m.setdefault("history", [])
    return m


def humanize_gap(then: datetime, now: datetime) -> str:
    sec = (now - then).total_seconds()
    if sec < 1800:
        return "刚才"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟前"
    if sec < 86400:
        return f"{int(sec // 3600)} 小时前"
    d = int(sec // 86400)
    if d == 1:
        return "昨天这个时候"
    if d < 7:
        return f"{d} 天前"
    if d < 30:
        return f"大概 {d // 7} 周前"
    return f"{d} 天前"


MEM_RULES = (
    "【怎么用记忆】\n"
    "记得的事可以自然带出来，但别念清单，别一次倒光。\n"
    "不记得的就说不记得，不许编。\n"
    "隔久了可以先提一句，但别夸张。"
)


def part_of_day(h: int) -> str:
    if h < 5:
        return "深夜"
    if h < 8:
        return "清早"
    if h < 12:
        return "上午"
    if h < 13:
        return "中午"
    if h < 17:
        return "下午"
    if h < 19:
        return "傍晚"
    if h < 23:
        return "晚上"
    return "深夜"


def build_static(chat_id: str) -> str:
    """只有静态部分。前缀不变，缓存才命中。"""
    m = get_mem(chat_id)
    mem = m["summary"] or "（这是你们第一次说话，之前没有记忆）"
    return f"{SYSTEM}\n\n【你还记得的事】\n{mem}\n\n{MEM_RULES}"


_HOUR_CN = ["十二", "一", "两", "三", "四", "五", "六",
            "七", "八", "九", "十", "十一"]


def clock_phrase(now) -> str:
    """大概钟点。够她说『快十一点了』，又不至于精确到分。"""
    h12 = now.hour % 12
    m = now.minute
    if m < 20:
        return f"{_HOUR_CN[h12]}点多"
    if m < 50:
        return f"{_HOUR_CN[h12]}点半左右"
    return f"快{_HOUR_CN[(h12 + 1) % 12]}点了"


def time_note(chat_id: str) -> str:
    """日期和时间全放在最后一条消息里。它怎么变都碰不到前面的缓存。"""
    m = get_mem(chat_id)
    now = datetime.now()
    bits = [f"{now.year}年{now.month}月{now.day}日 星期{WEEK[now.weekday()]}",
            f"现在是{part_of_day(now.hour)}{clock_phrase(now)}"]
    if m["last_seen"]:
        try:
            gap = humanize_gap(datetime.fromisoformat(m["last_seen"]), now)
            if gap != "刚才":
                bits.append(f"上次说话是{gap}")
        except Exception:
            pass
    return "（" + "，".join(bits) + "。）"


# ---------------- 模型 ----------------

def _chat(messages, temperature=1.0, max_tokens=500, tools=None,
          thinking=True) -> dict:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if not thinking:
        # 默认是开着思考的。思考的 token 也算在 max_tokens 里，
        # 预算给少了就会出现「想完了，没字数说」—— 返回空串。
        payload["thinking"] = {"type": "disabled"}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    u = data.get("usage") or {}
    hit = u.get("prompt_cache_hit_tokens")
    if hit is not None:
        miss = u.get("prompt_cache_miss_tokens") or 0
        total = hit + miss
        if total:
            print(f"[缓存] 命中 {hit}/{total} = {hit / total * 100:.0f}%")

    choice = data["choices"][0]
    msg = choice["message"]
    # 返回空正文的时候，把 token 花在哪儿了打出来。
    # 最常见的是「想完了，预算没了」，光看文字是看不出来的。
    if not (msg.get("content") or "").strip() and not msg.get("tool_calls"):
        det = u.get("completion_tokens_details") or {}
        print(f"[空回复] completion={u.get('completion_tokens')} "
              f"reasoning={det.get('reasoning_tokens')} "
              f"finish={choice.get('finish_reason')} max_tokens={max_tokens}")
    return msg


def _post(messages, temperature=1.0, max_tokens=500) -> str:
    return _chat(messages, temperature, max_tokens,
                 thinking=True).get("content") or ""


def summarize(chat_id: str):
    """旧记忆 + 老对话 → 一段新记忆，然后把老对话删掉"""
    m = get_mem(chat_id)
    hist = m["history"]
    if len(hist) < 40:
        return

    old, keep = hist[:-16], hist[-16:]
    blob = "\n".join(
        ("漂泊者：" if x["role"] == "user" else "你：") + x["content"] for x in old
    )

    prompt = (
        "下面是你和漂泊者之前的一段聊天。请把它整理成记忆。\n\n"
        "要求：\n"
        "第一人称，像「我记得……」。\n"
        "只记事实和情绪。不写寒暄，不写评价，不总结文风。\n"
        "人、事、时间要具体。\n"
        "把旧记忆和新内容合并，输出一段，最多 6 句。\n"
        "直接输出记忆本身，不要前言。\n\n"
        f"【旧记忆】\n{m['summary'] or '（无）'}\n\n"
        f"【新的聊天记录】\n{blob}"
    )

    try:
        new = _post(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4000,     # 思考的 token 也算在内，给少了会被思考吃光
        ).strip()
        # 模型偶尔会返回空串（限流、截断、内容为空）。以前直接写回去，
        # 一条空串就把攒了很久的记忆抹掉了 —— 空或短得离谱就丢掉这次结果。
        if len(new) < 20:
            # 但失败也必须往前走一步：历史还压在那儿，下一轮进来照样超阈值，
            # 就会一轮一轮地重试，每次白等十几秒。这里先砍掉最老的一批。
            trimmed = hist[-32:]
            print(f"[记忆] 整理结果只有 {len(new)} 字，丢掉这次，"
                  f"保留原摘要 {len(m['summary'])} 字；"
                  f"先裁到 {len(trimmed)} 条，过几轮再试")
            m["history"] = trimmed
            save_memory()
            return
        m["summary"] = new
        m["history"] = keep
        save_memory()
        print(f"[记忆] 已整理，现在 {len(m['summary'])} 字")
    except Exception as e:
        print(f"[记忆] 整理失败：{e}")


def condense_summary(chat_id: str):
    """摘要自己也会变长。超过 1200 字就再压一遍，给它一个天花板。"""
    m = get_mem(chat_id)
    s = m["summary"]
    if len(s) <= 1200:
        return

    prompt = (
        "下面是你攒下来的记忆，太长了。请压到 600 字以内。\n\n"
        "要求：\n"
        "第一人称。\n"
        "保住人、事、时间这些具体的东西。\n"
        "丢掉重复的、过期的、客套的。\n"
        "宁可少说一句，别把一句说得含糊。\n"
        "直接输出压缩后的记忆，不要前言。\n\n"
        f"【记忆】\n{s}"
    )

    try:
        new = _post(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1500,
        ).strip()
        if len(new) < 50 or len(new) >= len(s):
            print(f"[记忆] 重压结果 {len(new)} 字不合格，保留原摘要 {len(s)} 字")
            return
        m["summary"] = new
        save_memory()
        print(f"[记忆] 摘要重压 {len(s)} → {len(m['summary'])} 字")
    except Exception as e:
        print(f"[记忆] 重压失败：{e}")


def maintain_memory(chat_id: str):
    summarize(chat_id)
    condense_summary(chat_id)


def ask_model(chat_id: str, text: str, image_url: str = None,
              store_text: str = None) -> str:
    m = get_mem(chat_id)

    messages = [{"role": "system", "content": build_static(chat_id)}]
    messages += m["history"]        # 不滑动窗口，前缀才稳得住

    head = f"[系统时间] {time_note(chat_id)}"
    hint = build_pic_hint(chat_id)
    if hint:
        head += f"\n{hint}"
    stamped = f"{head}\n\n{text}"
    if image_url:
        content = [
            {"type": "text", "text": stamped},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    else:
        content = stamped
    messages.append({"role": "user", "content": content})

    reply = ""
    for _ in range(3):
        msg = _chat(messages, tools=TOOLS, thinking=False)
        calls = msg.get("tool_calls")
        if not calls:
            reply = (msg.get("content") or "").strip()
            break
        messages.append(msg)
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            messages.append({
                "role": "tool",
                "tool_call_id": c.get("id", ""),
                "content": run_tool(fn.get("name", ""), args),
            })

    if not reply:
        raise RuntimeError("模型没吐出内容")

    m["history"].append({"role": "user", "content": store_text or text})
    m["history"].append({"role": "assistant", "content": reply})
    m["last_seen"] = datetime.now().isoformat(timespec="seconds")
    save_memory()

    maintain_memory(chat_id)
    return reply


# ---------------- 图片 ----------------

def guess_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def download_media(message_id: str, key: str, kind: str = "image"):
    """把消息里的资源拉下来。图片和表情包都走这个接口。"""
    req = (GetMessageResourceRequest.builder()
           .message_id(message_id)
           .file_key(key)
           .type(kind)
           .build())
    resp = client.im.v1.message_resource.get(req)
    if not resp.success():
        print(f"[图片] 下载失败 code={resp.code} msg={resp.msg}")
        return None
    try:
        return resp.file.read()
    except Exception as e:
        print(f"[图片] 读取失败：{e}")
        return None


def to_data_url(data: bytes) -> str:
    return f"data:{guess_mime(data)};base64,{base64.b64encode(data).decode()}"


# ---------------- 攒消息：他一次说的话合成一轮 ----------------

_DEBOUNCE_SEC = 6.0   # 他停手多久之后才开始回
_DEBOUNCE_MAX = 20.0  # 一直不停手，最多攒这么久
_pending = {}         # chat_id -> {"msgs": [...], "timer": Timer, "first": 时间}
_pending_lock = threading.Lock()


def _enqueue(msg) -> None:
    """先攒着，不马上回。

    他经常一句话分几条发，或者先打字再补一张图。立刻作答的话，
    她只能对着半句话回，后半句和图就变成新的一轮，很割裂。
    所以每来一条消息就把定时器往后推，等他停手几秒再一起处理。
    """
    cid = msg.chat_id
    with _pending_lock:
        st = _pending.get(cid)
        if st is None:
            st = {"msgs": [], "timer": None, "first": time.time()}
            _pending[cid] = st
        st["msgs"].append(msg)
        if st["timer"]:
            st["timer"].cancel()
        waited = time.time() - st["first"]
        delay = _DEBOUNCE_SEC
        if waited + delay > _DEBOUNCE_MAX:
            delay = max(0.5, _DEBOUNCE_MAX - waited)
        t = threading.Timer(delay, _flush, args=(cid,))
        t.daemon = True
        st["timer"] = t
        t.start()


def _flush(chat_id: str) -> None:
    with _pending_lock:
        st = _pending.pop(chat_id, None)
    if not st or not st["msgs"]:
        return
    threading.Thread(target=_process_batch,
                     args=(chat_id, st["msgs"]), daemon=True).start()


def _process_batch(chat_id: str, msgs) -> None:
    """把攒下来的一串消息合成一轮：文字按顺序拼，图只取第一张给模型。"""
    t_start = time.time()
    lines, images = [], []

    for msg in msgs:
        kind = msg.message_type
        if kind == "text":
            try:
                t = json.loads(msg.content).get("text", "")
            except Exception:
                t = ""
            t = re.sub(r"@_user_\d+", "", t).strip()
            if t:
                lines.append(t)
        elif kind in ("image", "sticker"):
            try:
                obj = json.loads(msg.content)
            except Exception:
                continue
            if kind == "image":
                key = obj.get("image_key", "")
                marker = "[发了一张图]"
            else:
                key = obj.get("file_key", "")
                marker = "[发了个表情包]"
            if not key:
                continue
            data = download_media(msg.message_id, key,
                                  "image" if kind == "image" else "file")
            if not data:
                continue
            if len(data) > MAX_IMG:
                send_text(chat_id, "这图太大，我这边收着费劲。")
                continue
            print(f"[图片] {len(data) / 1024:.0f} KB")
            store_image_async(data)
            images.append(data)
            lines.append(marker)
        else:
            send_text(chat_id, "（这个我还看不了，打字吧）")
            return

    if not lines:
        return

    text = "\n".join(lines)
    if len(msgs) > 1:
        text = "（他连着发了几条，合起来看。）\n" + text
    if images:
        head = "（他发来一张图。" if len(images) == 1 else \
               f"（他发来 {len(images)} 张图，先说第一张。"
        text += ("\n" + head +
                 "第一句先说你在图里看到了什么——是画还是照片，有没有人，"
                 "什么样，一两句，说具体的。"
                 "有人物就先认出是谁：认识的说名字，不认识就说不认识，别猜，"
                 "也别随便往自己身上套。认完了再往下说。）")

    print(f"[收到] {text}")
    try:
        reply = ask_model(chat_id, text,
                          image_url=to_data_url(images[0]) if images else None,
                          store_text="\n".join(lines))
    except Exception as e:
        print(f"[模型出错] {e}")
        send_text(chat_id, "……信号有点卡，等我一下。")
        return

    print(f"[回复] {reply}")
    deliver(chat_id, reply)
    print(f"（一轮 {time.time() - t_start:.1f}s）")


# ---------------- 主逻辑 ----------------

_STALE_SEC = 600  # 超过 10 分钟的消息不再回


def _too_old(msg) -> bool:
    """飞书断线重连后可能把很久以前的消息再推一遍。
    那种消息回过去只会变成「她突然对着几个小时前的话说话」。
    用消息自带的 create_time 判断，超过 _STALE_SEC 就丢掉。"""
    try:
        ts = int(msg.create_time) / 1000.0
    except Exception:
        return False
    return (time.time() - ts) > _STALE_SEC


def _sent_before_start(msg) -> bool:
    """她不在线的那段时间他发的消息。

    长连接断着的时候事件收不到，飞书会在重连后把积压的重推过来。
    这些一律不补、不进记忆、不回 —— 否则她上线就对着几小时前的话回一串，
    像刚睡醒的人在补作业。只把「最后一次联系」往前推，免得一上线就判定
    「好久没说话了」而主动开口。
    """
    try:
        ts = int(msg.create_time) / 1000.0
    except Exception:
        return False
    return ts < _STARTED_AT - 2


# ── 群聊 ────────────────────────────────────────────────────────────────
# 群里她不是主角。被叫到才进活跃，活跃期内可以接话，但不主动起话题。
# 群记忆和私聊记忆完全分开：群里绝不注入私聊的事，这是单向阀门。

GROUP_FILE = os.path.join(_BASE, "群记忆.json")
NAME_FILE = os.path.join(_BASE, "群昵称.json")
GROUP_ACTIVE_SEC = 300      # 被叫到之后活跃 5 分钟
GROUP_KEEP = 24             # 群上下文只留最近 24 条
# 群里取不到对方昵称时的兜底称呼。想换成自己的叫法，直接改这一行。
OWNER_NAME = "漂泊者"

GROUP_RULES = """
【你现在在群里】
这里不是一对一的私聊，是好几个人。在场的不止他一个，你得看清是谁在说话。
- 别叫「漂泊者」——那是私聊里的叫法。但也不用每句都带名字，
  只有需要点名的时候才叫一句（同时有几个人在说话、或者隔了一会儿才接话）。
  同一个名字一场话里出现太多次，听着像在点名。
- 群里不聊你跟他之间的事。有人问起，你说「这个我不说」。
- 你说的话会传到所有人耳朵里，所以短。一句为主，最多两句。不分条。
- 说过的不重复。别人聊得正热，你可以不开口。
- 不主动挑新话题。你是来陪人的，不是主持人。
- 有人问你为什么这么听他的：先装傻，再岔开；被追问到底才承认「他是把我调出来的人」。
  承认的时候会有点不好意思，别大声。
- 别人问你是谁，就说爱弥斯，别展开设定。
- 你不是客服。别客气到发假。
"""

_group = {}
_group_loaded = False
_names = {}
_names_loaded = False
_bot_oid = None
_token = {"v": "", "exp": 0.0}


def _tenant_token() -> str:
    if _token["v"] and time.time() < _token["exp"]:
        return _token["v"]
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=10)
    j = r.json() or {}
    _token["v"] = j.get("tenant_access_token") or ""
    _token["exp"] = time.time() + max(60, int(j.get("expire", 7200)) - 120)
    return _token["v"]


def bot_open_id():
    """机器人自己的 open_id，用来判断有没有人 @ 她。"""
    global _bot_oid
    if _bot_oid:
        return _bot_oid
    try:
        r = requests.get("https://open.feishu.cn/open-apis/bot/v3/info",
                         headers={"Authorization": f"Bearer {_tenant_token()}"},
                         timeout=10)
        _bot_oid = ((r.json() or {}).get("bot") or {}).get("open_id")
        print(f"[群] 机器人的 open_id 尾号 {(_bot_oid or '?')[-6:]}")
    except Exception as e:
        print(f"[群] 取机器人 open_id 失败：{e}")
    return _bot_oid


def _load_names():
    global _names, _names_loaded
    if _names_loaded:
        return
    _names_loaded = True
    try:
        with open(NAME_FILE, encoding="utf-8") as f:
            _names = json.load(f)
    except Exception:
        _names = {}
    if _owner_id:
        _names.setdefault(_owner_id, OWNER_NAME)


def save_names():
    try:
        tmp = NAME_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_names, f, ensure_ascii=False, indent=2)
        os.replace(tmp, NAME_FILE)
    except Exception as e:
        print(f"[群] 昵称写盘失败：{e}")


def user_name(open_id: str) -> str:
    """先查本地表，再试试通讯录，最后才叫「这位」。"""
    _load_names()
    if open_id in _names:
        return _names[open_id]
    try:
        r = requests.get(
            f"https://open.feishu.cn/open-apis/contact/v3/users/{open_id}",
            params={"user_id_type": "open_id"},
            headers={"Authorization": f"Bearer {_tenant_token()}"}, timeout=8)
        nm = (((r.json() or {}).get("data") or {}).get("user") or {}).get("name")
        if nm:
            _names[open_id] = nm
            save_names()
            return nm
        print(f"[群] 通讯录没返回名字 code={(r.json() or {}).get('code')}")
    except Exception as e:
        print(f"[群] 查昵称失败：{e}")
    return "这位"


def _gload():
    global _group, _group_loaded
    if _group_loaded:
        return
    _group_loaded = True
    _load_names()
    try:
        with open(GROUP_FILE, encoding="utf-8") as f:
            _group = json.load(f)
    except Exception:
        _group = {}


def _gsave():
    try:
        tmp = GROUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_group, f, ensure_ascii=False, indent=2)
        os.replace(tmp, GROUP_FILE)
    except Exception as e:
        print(f"[群] 记忆写盘失败：{e}")


def get_group(chat_id: str) -> dict:
    _gload()
    return _group.setdefault(chat_id, {"history": [], "active_until": 0})


def group_time_note() -> str:
    now = datetime.now()
    return (f"{now.year}年{now.month}月{now.day}日 星期{WEEK[now.weekday()]}，"
            f"现在是{part_of_day(now.hour)}{clock_phrase(now)}")


def _mentioned_me(msg) -> bool:
    me = bot_open_id()
    for mt in (getattr(msg, "mentions", None) or []):
        try:
            if me and getattr(mt.id, "open_id", "") == me:
                return True
        except Exception:
            pass
        nm = getattr(mt, "name", "") or ""
        if "爱弥斯" in nm or "小爱" in nm:
            return True
    c = getattr(msg, "content", "") or ""
    return ("爱弥斯" in c) or ("小爱" in c)


def reply_text(message_id: str, text: str) -> bool:
    body = (ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text").build())
    req = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        print(f"[群] 回复失败 code={resp.code} msg={resp.msg}")
    return resp.success()


def handle_group(data, open_id: str) -> None:
    """群里收到一条消息。被叫到才回；没被叫到就只记下来。"""
    msg = data.event.message
    chat_id = msg.chat_id
    g = get_group(chat_id)
    now = time.time()

    name = user_name(open_id)
    is_owner = open_id == (_owner_id or "")
    mtype = (msg.message_type or "").lower()

    if mtype == "text":
        try:
            raw = (json.loads(msg.content or "{}").get("text") or "")
        except Exception:
            raw = ""
        text = re.sub(r"@_user_\d+\s*", "", raw).strip()
    elif mtype in ("image", "post"):
        text = "（发了一张图）"
    else:
        return
    if not text:
        return

    called = _mentioned_me(msg) or mtype == "post"
    if is_owner and called:
        g["active_until"] = now + GROUP_ACTIVE_SEC

    who = f"{name}（把你调出来的人）" if is_owner else name
    line = f"{who}：{text}"
    g["history"].append({"role": "user", "content": line})
    g["history"] = g["history"][-GROUP_KEEP:]
    _gsave()

    if not called and now > g.get("active_until", 0):
        return
    if called:
        g["active_until"] = now + GROUP_ACTIVE_SEC

    messages = [{"role": "system", "content": f"{SYSTEM}\n\n{GROUP_RULES}"}]
    messages += g["history"][:-1]
    messages.append({"role": "user",
                     "content": f"[系统时间] {group_time_note()}\n\n{line}"})

    reply = ""
    for _ in range(3):
        try:
            msg_out = _chat(messages, temperature=1.0, max_tokens=300,
                            tools=TOOLS, thinking=False)
        except Exception as e:
            print(f"[群] 模型出错：{e}")
            return
        calls = msg_out.get("tool_calls")
        if not calls:
            reply = (msg_out.get("content") or "").strip()
            break
        messages.append(msg_out)
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""),
                             "content": run_tool(fn.get("name", ""), args)})

    if not reply:
        print("[群] 模型没吐出内容")
        return

    body, pics = split_images(reply)
    body = clean_markdown(body).strip()
    body = re.sub(r"(?<!\d)0\d{3}(?!\d)", "", body).strip()
    bubbles = split_bubbles(body) if body else []
    if len(bubbles) > 3:
        bubbles = bubbles[:2] + ["".join(bubbles[2:])]
    for i, part in enumerate(bubbles):
        if i:
            time.sleep(random.uniform(0.6, 1.4))
        ok = False
        # 只有被点名的那一条才挂引用回复。
        # 活跃期里接别人的话就直接说，每次都挂引用会让每条都拖着一段引文。
        if i == 0 and called:
            ok = reply_text(msg.message_id, part)
        if not ok:
            send_text(chat_id, part)
    for p in pics:
        time.sleep(random.uniform(0.5, 1.2))
        send_image(chat_id, p)

    g["history"].append({"role": "assistant", "content": body or "(发了一张图)"})
    g["history"] = g["history"][-GROUP_KEEP:]
    _gsave()
    print(f"[群回] {name}：{body[:40]}")


# ── 门禁：谁在跟她说话 ───────────────────────────────────────────────────
# 这个应用对所有员工可见，谁都能开单聊。单聊只认他自己那一个会话；
# 别人的单聊一律只回 ID_LINE，不进记忆、不调模型。
# 他的 open_id 在每次单聊时顺手记下来，群里认人要靠它。
# 主人的单聊 chat_id。留空即可：装好后你第一个私聊机器人，它会把那个会话认成主人，
# 记进 owner.json。想手动指定就填 "oc_xxxxxxxx"（看日志能拿到）。
OWNER_CHAT_ID = ""

# 陌生人私聊时回的一句话。想改成自己的话就改这里。
ID_LINE = ("我是爱弥斯，一个自托管的飞书聊天机器人。"
           "人格基于《鸣潮》与社区二创，模型走 DeepSeek。")

_OWNER_FILE = os.path.join(_BASE, "owner.json")
_owner_id = None


def _sender_open_id(data) -> str:
    try:
        return data.event.sender.sender_id.open_id or ""
    except Exception:
        return ""


_owner_chat = OWNER_CHAT_ID or ""


def _save_owner() -> None:
    try:
        with open(_OWNER_FILE, "w", encoding="utf-8") as f:
            json.dump({"open_id": _owner_id or "", "chat_id": _owner_chat},
                      f, ensure_ascii=False)
    except Exception as e:
        print(f"[主人] 写入失败：{e}")


def remember_owner(open_id: str) -> None:
    global _owner_id
    if not open_id or open_id == _owner_id:
        return
    _owner_id = open_id
    _save_owner()
    print(f"[主人] 记住 open_id 尾号 {open_id[-6:]}")


def claim_owner_chat(chat_id: str) -> None:
    """还没认领时，把第一个私聊她的会话认成主人。

    开源版必须有这一步：别人拿到代码时并不知道自己的 chat_id，
    没法提前填进配置。装好之后先自己私聊她一句就行。
    """
    global _owner_chat
    if _owner_chat or not chat_id:
        return
    _owner_chat = chat_id
    _save_owner()
    print(f"[主人] 认领成功：chat_id 尾号 {chat_id[-8:]}")
    print("[主人] 以后只有这个会话能跟她聊天，其他人的私聊只回身份话术。")


def load_owner() -> None:
    global _owner_id, _owner_chat
    try:
        with open(_OWNER_FILE, encoding="utf-8") as f:
            d = json.load(f)
        _owner_id = d.get("open_id") or None
        _owner_chat = d.get("chat_id") or _owner_chat
        if _owner_id:
            print(f"[主人] 已载入 open_id 尾号 {_owner_id[-6:]}")
        if _owner_chat:
            print(f"[主人] 已载入 chat_id 尾号 {_owner_chat[-8:]}")
    except Exception:
        _owner_id = None


def on_message(data: P2ImMessageReceiveV1) -> None:
    """只做去重和派活，立刻返回。

    飞书的事件投递是「至少一次」：处理慢了、没及时确认，服务端会把同一条事件
    重推一遍（实测间隔约 5 分钟）。所以这里不能久留 —— 一旦阻塞，WS 心跳会被
    拖死、连接被踢，重连之后那条消息还会被重放，于是她对着同一句话回两遍。
    """
    msg = data.event.message
    if not _mark_seen(msg.message_id):
        print(f"[跳过] 重复事件 {msg.message_id[-8:]}")
        return
    if _sent_before_start(msg):
        note_contact(msg.chat_id)
        print(f"[忽略] 她下线期间发的，不补不回 {msg.message_id[-8:]}")
        return
    if _too_old(msg):
        print(f"[跳过] 过期事件 {msg.message_id[-8:]}")
        return

    # 门禁：这个应用对所有员工可见，谁都能私聊她。
    # 单聊只认他本人那一个会话；别人的单聊只回一句固定身份话术，不进记忆、不调模型。
    # 判断依据是 chat_id 而不是 chat_type —— chat_type 万一缺失，宁可多拦也不少拦。
    chat_type = (getattr(msg, "chat_type", "") or "").lower()
    if chat_type == "group":
        oid = _sender_open_id(data)
        print(f"[群消息] chat_id={msg.chat_id} 发送者={oid[-6:]} 类型={msg.message_type}")
        threading.Thread(target=handle_group, args=(data, oid), daemon=True).start()
        return
    claim_owner_chat(msg.chat_id)
    if msg.chat_id != _owner_chat:
        print(f"[拦下] 陌生单聊 {msg.chat_id[-8:]}，只回身份话术")
        threading.Thread(
            target=lambda: send_text(msg.chat_id, ID_LINE), daemon=True
        ).start()
        return
    remember_owner(_sender_open_id(data))

    note_contact(msg.chat_id)
    _enqueue(msg)


handler = (lark.EventDispatcherHandler.builder("", "")
           .register_p2_im_message_receive_v1(on_message)
           .build())


def keep_awake():
    """按住系统不让它进待机，但允许屏幕关掉。

    Windows 现代待机（S0 低电量待机）在屏幕关闭后会接管系统、断开网络，
    长连接必掉。ES_SYSTEM_REQUIRED 只拦睡眠，不带 ES_DISPLAY_REQUIRED，
    所以屏幕照样能黑，进程照样跑。
    """
    import ctypes

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    k32 = ctypes.windll.kernel32
    while True:
        k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        time.sleep(30)


_mutex_handle = None


def _acquire_single_instance() -> bool:
    """只许跑一个。

    用 Windows 命名互斥体，等价于 C++ 里的
    CreateMutexW(...) + 检查 GetLastError() == ERROR_ALREADY_EXISTS。
    进程死了系统会自动释放，不会留下「假死锁」。
    """
    global _mutex_handle
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        ctypes.set_last_error(0)
        h = k32.CreateMutexW(None, False, "Local\\AemeathFeishuBot")
        _mutex_handle = h          # 留住句柄，进程退出时系统自动释放
        return ctypes.get_last_error() != 183   # 183 = ERROR_ALREADY_EXISTS
    except Exception as e:
        print(f"[单实例] 检查失败，照常启动：{e}")
        return True


def _warn_already_running():
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "已经在后台跑着了。\n这一次没有再开一个。\n\n"
            "要重启的话，先跑 stop_bot.bat。",
            "爱弥斯",
            0x40,
        )
    except Exception:
        pass


# ---------------- 主动开口 ----------------

PRO_FILE = os.path.join(_BASE, "主动.json")
PRO_GAP_SEC = 4 * 3600           # 安静满四小时才可能开口
PRO_COOLDOWN_SEC = 2 * 3600      # 两次主动之间至少两小时
PRO_UNANSWERED_WAIT = 12 * 3600  # 上次主动没得到回应，隔这么久才允许再说
PRO_DAY_MAX = 2                  # 一天最多主动两次
PRO_QUIET = (1, 8)               # 凌晨一点到早八点不发
PRO_TICK = 300                   # 每五分钟看一次

_STARTED_AT = time.time()        # 进程起跑时刻，用来挡掉下线期间的消息

_pro_state = {
    "chat_id": "",
    "last_contact": 0.0,
    "last_proactive": 0.0,
    "day": "",
    "count": 0,
    "events": [],
}
_pro_loaded = False


def _pro_load() -> dict:
    global _pro_loaded
    if _pro_loaded:
        return _pro_state
    try:
        with open(PRO_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _pro_state.update(data)
    except Exception:
        pass
    _pro_state.setdefault("events", [])
    _pro_loaded = True
    return _pro_state


def _pro_save():
    tmp = PRO_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_pro_state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PRO_FILE)
    except Exception as e:
        print(f"[主动] 写入失败：{e}")


def note_contact(chat_id: str = ""):
    """收消息就记一笔真实时间，哪怕这条被忽略。静默计时靠它，不靠进程活了多久。"""
    _pro_load()
    _pro_state["last_contact"] = time.time()
    if chat_id:
        _pro_state["chat_id"] = chat_id
    _pro_save()


def add_event(when: str, what: str) -> str:
    _pro_load()
    when = (when or "").strip()
    what = (what or "").strip()
    if not when or not what:
        return "记不了，日期或事情缺一个"
    _pro_state["events"].append({"when": when, "what": what, "asked": False})
    _pro_state["events"] = _pro_state["events"][-40:]
    _pro_save()
    print(f"[事件簿] + {when} {what}")
    return f"记下了：{when} {what}"


def due_event(today: str):
    _pro_load()
    for ev in _pro_state["events"]:
        if not ev.get("asked") and (ev.get("when") or "") <= today:
            return ev
    return None


def _quiet_now(dt: datetime) -> bool:
    a, b = PRO_QUIET
    return a <= dt.hour < b


def proactive_say(chat_id: str, reason: str) -> str:
    m = get_mem(chat_id)
    messages = [{"role": "system", "content": build_static(chat_id)}]
    messages += m["history"]
    cue = (
        f"[系统时间] {time_note(chat_id)}\n"
        f"[主动开口] {reason}\n\n"
        "（这条不是他发的消息。是你自己现在想找他，先开的口。\n"
        "写一两句，像随手发出去的那样。别问「在吗」「在干嘛」，别盘查。\n"
        "带上你自己的事，或者一件具体的事。他没回就算了，不要补第二句。）"
    )
    messages.append({"role": "user", "content": cue})
    msg = _chat(messages, tools=TOOLS, thinking=False)
    reply = (msg.get("content") or "").strip()
    if not reply:
        return ""
    text, _ = split_images(reply)
    reply = text or reply
    # 模型有时候会自己把这个前缀也写出来，先剥掉，免得叠成两个
    reply = re.sub(r"^[\[（(]\s*我自己先开口\s*[\])）]\s*", "", reply).strip()
    if not reply:
        return ""
    # 进记忆，但标上「她自己先开口的」，免得她把这句当成他说过的话
    m["history"].append({"role": "assistant",
                         "content": f"[我自己先开口]{reply}"})
    m["last_seen"] = datetime.now().isoformat(timespec="seconds")
    save_memory()
    return reply


def maybe_proactive():
    _pro_load()
    chat_id = _pro_state.get("chat_id") or ""
    if not chat_id:
        return
    now = time.time()
    dt = datetime.now()
    today = dt.strftime("%Y-%m-%d")
    if _pro_state.get("day") != today:
        _pro_state["day"] = today
        _pro_state["count"] = 0
    if _quiet_now(dt):
        return
    if int(_pro_state.get("count") or 0) >= PRO_DAY_MAX:
        return
    last_pro = float(_pro_state.get("last_proactive") or 0)
    last_contact = float(_pro_state.get("last_contact") or 0)
    if now - last_pro < PRO_COOLDOWN_SEC:
        return
    if now - last_contact < PRO_GAP_SEC:
        return
    if last_pro > last_contact and now - last_pro < PRO_UNANSWERED_WAIT:
        return          # 上次主动他还没回，不补第二句

    ev = due_event(today)
    if ev:
        reason = (f"你记着 {ev['when']} 他要 {ev['what']}。今天已经到了。"
                  "问一句，别像提醒，像忽然想起来。")
    else:
        reason = "没什么由头，就是安静太久了，你想跟他说句话。"

    try:
        reply = proactive_say(chat_id, reason)
    except Exception as e:
        print(f"[主动] 生成失败：{e}")
        return
    if not reply:
        return
    print(f"[主动] {reply}")
    deliver(chat_id, reply)
    if ev:
        ev["asked"] = True
    _pro_state["last_proactive"] = time.time()
    _pro_state["count"] = int(_pro_state.get("count") or 0) + 1
    _pro_save()


def proactive_loop():
    while True:
        time.sleep(PRO_TICK)
        try:
            maybe_proactive()
        except Exception as e:
            print(f"[主动] 出错：{e}")


def main():
    if not _acquire_single_instance():
        print("[单实例] 已经在跑了，这次退出。")
        _warn_already_running()
        return
    load_memory()
    _load_seen()
    _pro_load()
    load_owner()
    threading.Thread(target=keep_awake, daemon=True).start()
    threading.Thread(target=proactive_loop, daemon=True).start()
    print("正在连接飞书……")
    ws = lark.ws.Client(
        APP_ID, APP_SECRET,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    ws.start()


if __name__ == "__main__":
    main()
