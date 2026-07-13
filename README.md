# 百业-云间甜铺 · 思维导图知识库

《燕云十六声》玩家社区「百业-云间甜铺」会员制游戏百科网站。以思维导图形式整理武学流派、奇术攻略、装备养成、抽卡系统等知识。

## 项目结构

```
my_mindmap/
├── app.py                  # Flask 后端主程序（路由、认证、日志）
├── mindmap_data.json       # 思维导图知识库数据
├── authorized_ids.json     # 已授权用户 ID 列表
├── login_logs.json         # 登录日志
├── requirements.txt        # Python 依赖
├── .gitignore              # Git 忽略规则
├── static/
│   ├── jsmind.js           # jsMind 思维导图库
│   ├── jsmind.css          # jsMind 样式
│   └── particles.js        # 粒子背景动画（三页面共享）
└── templates/
    ├── login.html          # 用户登录页
    ├── index.html          # 思维导图主页面
    └── admin.html          # 管理后台
```

## 路由一览

| 网址 | 页面 | 说明 |
|------|------|------|
| `/` | 思维导图主页 | 需登录 |
| `/login` | 用户登录 | 10位数字ID验证 |
| `/logout` | 退出登录 | - |
| `/admin` | 管理后台 | 需管理员密码 |
| `/admin/logout` | 退出管理 | - |

## 功能清单

- **权限控制** — 已授权ID白名单 + Session 认证
- **频率限制** — 同IP失败5次封禁5分钟
- **日志轮转** — 登录日志自动保留最近200条
- **管理员后台** — 表单登录、增删授权ID、查看登录记录
- **思维导图** — jsMind 渲染，支持展开/折叠/拖拽/缩放
- **粒子动画** — Canvas 粒子背景（提取为独立JS文件复用）
- **安全加固** — Session HttpOnly/SameSite、密码支持环境变量

## 启动方式

```bash
# 安装依赖
pip install -r requirements.txt

# 生产模式（推荐，waitress 多线程 WSGI 服务器）
python app.py

# 开发模式（Flask 内置服务器，修改代码自动重载）
python app.py --dev

# 设置环境变量后启动（推荐）
# PowerShell:
$env:MINDMAP_SECRET_KEY = "你的随机密钥"
$env:MINDMAP_ADMIN_PASSWORD = "你的强密码"
python app.py

# 访问 http://localhost:5000/
```

## 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINDMAP_SECRET_KEY` | `x7k9p2m4n8q6w3r5t1y` | Flask 会话密钥，生产环境务必更换 |
| `MINDMAP_ADMIN_PASSWORD` | `admin123` | 管理员后台密码，生产环境务必更换 |

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python · Flask 3.1 |
| 前端 | jsMind 0.8.7 · Canvas API · 原生 CSS |
| 数据 | JSON 文件存储 |
| 样式 | 毛玻璃 (Glassmorphism) · CSS 动画 |

---

*百业-云间甜铺 · 燕云十六声玩家社区*
