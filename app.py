from flask import Flask, render_template, request, redirect, url_for, session
import json
import os
import time
import secrets
import threading
import logging
import shutil
from functools import wraps
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo
from werkzeug.middleware.proxy_fix import ProxyFix

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 中国时区（UTC+8）
CN_TZ = ZoneInfo("Asia/Shanghai")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=0, x_host=0)
app.secret_key = os.environ.get('MINDMAP_SECRET_KEY', 'x7k9p2m4n8q6w3r5t1y')
app.config['SESSION_PERMANENT'] = False  # 关闭浏览器即失效
app.config['SESSION_COOKIE_HTTPONLY'] = True  # 防止 JS 读取 session cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# 文件路径
AUTH_FILE = 'authorized_ids.json'
LOG_FILE = 'login_logs.json'
ADMIN_PASSWORD = os.environ.get('MINDMAP_ADMIN_PASSWORD', 'admin123')  # 建议通过环境变量设置

# 登录保护 - 内存中的失败计数器（重启后重置）
MAX_LOGIN_ATTEMPTS = 5        # 同一 IP 最大失败次数
LOGIN_BLOCK_SECONDS = 300     # 封禁时间（5分钟）
LOG_MAX_ENTRIES = 200         # 日志最大条数
LOGIN_RATE_LIMIT = {}         # {ip: {'failures': n, 'blocked_until': timestamp}}
FILE_LOCKS = {}              # 每个文件一把锁，防止并发写入损坏数据
_file_locks_lock = threading.Lock()  # 保护 FILE_LOCKS 字典本身

def _get_file_lock(filename):
    """获取指定文件的线程锁（懒创建）"""
    with _file_locks_lock:
        if filename not in FILE_LOCKS:
            FILE_LOCKS[filename] = threading.Lock()
        return FILE_LOCKS[filename]

# ---------- CSRF 保护 ----------
def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def csrf_protect(f):
    """装饰器：对 POST 请求验证 CSRF token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'POST':
            token = session.get('_csrf_token')
            form_token = request.form.get('_csrf_token')
            if not token or not form_token or not secrets.compare_digest(token, form_token):
                return 'CSRF 验证失败，请刷新页面后重试', 403
        return f(*args, **kwargs)
    return decorated

# ---------- 辅助函数 ----------
def load_json(filename, default=None):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            pass
    return default if default is not None else {}

def save_json(filename, data):
    lock = _get_file_lock(filename)
    with lock:
        tmp = filename + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, filename)  # 原子替换，防止写入中途崩溃
        except (IOError, OSError) as e:
            logger.error(f'保存文件失败: {filename} — {e}')

def load_authorized_ids():
    """返回 [{name, id}, ...], 自动兼容旧格式 ['id', ...]"""
    data = load_json(AUTH_FILE, {})
    ids = data.get('ids', [])
    result = []
    needs_migration = False
    for item in ids:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            # 旧格式：纯ID字符串，自动迁移
            result.append({'name': '', 'id': item})
            needs_migration = True
    if needs_migration:
        save_authorized_ids(result)
    return result

def get_authorized_id_set():
    """返回纯ID集合，用于快速查重和登录验证"""
    return {entry['id'] for entry in load_authorized_ids()}

def get_authorized_name(uid):
    """根据ID查找昵称"""
    for entry in load_authorized_ids():
        if entry['id'] == uid:
            return entry.get('name', '')
    return ''

def save_authorized_ids(ids):
    """ids = [{name, id}, ...]"""
    save_json(AUTH_FILE, {'ids': ids})

def log_login(user_id, user_name, ip):
    logger.info(f'用户登录成功: 昵称={user_name}, ID={user_id}, IP={ip}')
    logs = load_json(LOG_FILE, [])
    logs.append({
        'id': user_id,
        'name': user_name,
        'time': datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
        'ip': ip
    })
    # 只保留最近 N 条记录
    if len(logs) > LOG_MAX_ENTRIES:
        logs = logs[-LOG_MAX_ENTRIES:]
    save_json(LOG_FILE, logs)

def check_rate_limit(ip):
    """登录频率限制：同一 IP 失败 N 次后封禁一段时间"""
    now = time.time()
    entry = LOGIN_RATE_LIMIT.get(ip, {'failures': 0, 'blocked_until': 0})
    # 定期清理过期条目，防止内存泄漏
    stale_ips = [k for k, v in LOGIN_RATE_LIMIT.items() if v['blocked_until'] < now]
    for k in stale_ips:
        del LOGIN_RATE_LIMIT[k]
    # 检查是否在封禁期
    if entry['blocked_until'] > now:
        return False, int(entry['blocked_until'] - now)
    return True, 0

def record_login_failure(ip):
    """记录一次失败，达到阈值后封禁"""
    now = time.time()
    entry = LOGIN_RATE_LIMIT.get(ip, {'failures': 0, 'blocked_until': 0})
    if entry['blocked_until'] > now:
        return
    entry['failures'] += 1
    if entry['failures'] >= MAX_LOGIN_ATTEMPTS:
        entry['blocked_until'] = now + LOGIN_BLOCK_SECONDS
        entry['failures'] = 0
        logger.warning(f'IP {ip} 失败 {MAX_LOGIN_ATTEMPTS} 次，已封禁 {LOGIN_BLOCK_SECONDS} 秒')
    LOGIN_RATE_LIMIT[ip] = entry

def clear_login_failures(ip):
    """登录成功后清除失败记录"""
    LOGIN_RATE_LIMIT.pop(ip, None)

def load_mindmap_data():
    """每次请求时重新加载思维导图数据，无需重启即可生效"""
    return load_json('mindmap_data.json', {})

def convert_mindmap_to_guide():
    """将 jsMind 树形数据转为专题卡片结构"""
    data = load_mindmap_data()
    if not data or 'children' not in data:
        return {'categories': []}

    ICONS = {
        'damage': '💪',
        'gacha': '🃏',
        'shop': '🛒',
    }

    categories = []
    for cat_node in data['children']:
        cat_id = cat_node['id']
        topics = []

        for topic_node in cat_node.get('children', []):
            sections = []
            for child in topic_node.get('children', []):
                grandchildren = child.get('children', [])
                if not grandchildren:
                    # 叶子节点：直接条目
                    if not sections:
                        sections.append({'title': '', 'entries': []})
                    sections[0]['entries'].append(child['topic'])
                elif grandchildren[0].get('children'):
                    # 有子栏目的节点（如 太极 → 探索/战斗 → 具体条目）
                    subs = []
                    for gc in grandchildren:
                        subs.append({
                            'title': gc['topic'],
                            'entries': [item['topic'] for item in gc.get('children', [])]
                        })
                    sections.append({
                        'title': child['topic'],
                        'subsections': subs
                    })
                else:
                    # 普通分组节点（如 官配流派 → 10个流派条目）
                    sections.append({
                        'title': child['topic'],
                        'entries': [item['topic'] for item in grandchildren]
                    })
            topics.append({
                'id': topic_node['id'],
                'title': topic_node['topic'],
                'sections': sections
            })

        categories.append({
            'id': cat_id,
            'title': cat_node['topic'],
            'icon': ICONS.get(cat_id, '📄'),
            'tags': ['萌新必看'],
            'topics': topics
        })

    return {'categories': categories}

# ---------- 路由 ----------
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_name = session.get('user_name', '')
    return render_template('index.html', user_name=user_name)

@app.route('/guide')
def guide():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_name = session.get('user_name', '')
    data = convert_mindmap_to_guide()
    return render_template('guide.html', user_name=user_name, categories=data['categories'])

@app.route('/guide/<topic_id>')
def guide_topic(topic_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_name = session.get('user_name', '')
    data = convert_mindmap_to_guide()
    category = next((c for c in data['categories'] if c['id'] == topic_id), None)
    if not category:
        return '专题不存在', 404
    return render_template('guide_topic.html', user_name=user_name, category=category)

@app.route('/login', methods=['GET', 'POST'])
@csrf_protect
def login():
    if request.method == 'POST':
        user_id = request.form.get('user_id', '').strip()
        client_ip = request.remote_addr

        # 频率限制检查
        allowed, remaining = check_rate_limit(client_ip)
        if not allowed:
            minutes = remaining // 60
            seconds = remaining % 60
            return render_template('login.html', error=f'❌ 登录尝试过多，请 {minutes}分{seconds}秒 后再试')

        # 简单验证：必须是10位数字
        if not user_id.isdigit() or len(user_id) != 10:
            record_login_failure(client_ip)
            logger.info(f'登录失败(格式错误): ID={user_id}, IP={client_ip}')
            return render_template('login.html', error='❌ 请输入正确的10位数字ID')
        # 检查是否授权
        auth_entries = load_authorized_ids()
        matched = next((e for e in auth_entries if e['id'] == user_id), None)
        if not matched:
            record_login_failure(client_ip)
            logger.info(f'登录失败(未授权): ID={user_id}, IP={client_ip}')
            return render_template('login.html', error='❌ 该ID未获得访问权限，请联系管理员')
        # 登录成功
        clear_login_failures(client_ip)
        session['user_id'] = user_id
        session['user_name'] = matched.get('name', '')
        # 记录日志
        log_login(user_id, matched.get('name', ''), client_ip)
        return redirect(url_for('index'))
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# ---------- 管理后台 ----------
@app.route('/admin', methods=['GET', 'POST'])
@csrf_protect
def admin():
    # 管理员登录验证
    if 'admin' not in session:
        if request.method == 'POST' and request.form.get('action') == 'login':
            client_ip = request.remote_addr

            # 管理员登录频率限制（复用同一套限流逻辑）
            allowed, remaining = check_rate_limit(client_ip)
            if not allowed:
                minutes = remaining // 60
                seconds = remaining % 60
                return render_template('admin.html', logining=True,
                    error=f'❌ 尝试过多，请 {minutes}分{seconds}秒 后再试')

            if request.form.get('password') == ADMIN_PASSWORD:
                clear_login_failures(client_ip)
                session['admin'] = True
                logger.warning(f'管理员登录成功: IP={client_ip}')
                return redirect(url_for('admin'))
            else:
                record_login_failure(client_ip)
                logger.warning(f'管理员登录密码错误: IP={client_ip}')
                return render_template('admin.html', logining=True, error='密码错误')
        return render_template('admin.html', logining=True, error=None)

    ids = load_authorized_ids()
    if request.method == 'POST':
        action = request.form.get('action')
        id_input = request.form.get('id', '').strip()
        if action == 'add' and id_input:
            # 支持 "昵称 ID" 或纯 "ID"
            parts = id_input.rsplit(None, 1)
            if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 10:
                name, uid = parts[0], parts[1]
            elif id_input.isdigit() and len(id_input) == 10:
                name, uid = '', id_input
            else:
                name, uid = '', id_input
            if uid.isdigit() and len(uid) == 10 and uid not in get_authorized_id_set():
                ids.append({'name': name, 'id': uid})
                save_authorized_ids(ids)
                logger.warning(f'管理员添加授权: {name} ({uid})')
        elif action == 'remove' and id_input:
            # 按ID删除（支持输入昵称+ID，提取ID部分）
            parts = id_input.rsplit(None, 1)
            remove_id = parts[1] if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 10 else id_input
            ids = [e for e in ids if e['id'] != remove_id]
            save_authorized_ids(ids)
            logger.warning(f'管理员移除授权ID: {remove_id}')
        elif action == 'bulk_import':
            bulk_text = request.form.get('bulk_ids', '')
            added = 0
            existing = get_authorized_id_set()
            for line in bulk_text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit(None, 1)
                if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 10:
                    name, uid = parts[0], parts[1]
                elif line.isdigit() and len(line) == 10:
                    name, uid = '', line
                else:
                    continue
                if uid not in existing:
                    ids.append({'name': name, 'id': uid})
                    existing.add(uid)
                    added += 1
            if added:
                save_authorized_ids(ids)
                logger.warning(f'管理员批量导入 {added} 个授权ID')
        return redirect(url_for('admin'))
    # 显示授权列表和日志（按ID分组，每组最新在前）
    logs = load_json(LOG_FILE, [])
    grouped = OrderedDict()
    for i, log in enumerate(logs):
        log_with_index = dict(log)
        log_with_index['_index'] = i
        uid = log['id']
        if uid not in grouped:
            grouped[uid] = []
        grouped[uid].insert(0, log_with_index)  # 每组内最新在前
    # 按每组最新登录时间排序
    log_groups = sorted(grouped.items(), key=lambda x: x[1][0]['time'], reverse=True)
    return render_template('admin.html', logining=False, ids=ids, log_groups=log_groups)

@app.route('/admin/delete-log', methods=['POST'])
@csrf_protect
def admin_delete_log():
    if 'admin' not in session:
        return redirect(url_for('admin'))
    index = request.form.get('index', '')
    if index.isdigit():
        logs = load_json(LOG_FILE, [])
        idx = int(index)
        if 0 <= idx < len(logs):
            logs.pop(idx)
            save_json(LOG_FILE, logs)
    return redirect(url_for('admin'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin'))

if __name__ == '__main__':
    # 确保数据文件存在（首次启动从模板复制，后续使用本地文件）
    for filename in (AUTH_FILE, LOG_FILE):
        if not os.path.exists(filename):
            example = filename.replace('.json', '.example.json')
            if os.path.exists(example):
                shutil.copy2(example, filename)
                logger.info(f'从 {example} 创建初始文件 {filename}')
            else:
                # 兜底：创建空文件
                save_json(filename, [] if filename == LOG_FILE else {'ids': []})

    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--dev':
        # 开发模式：Flask 内置服务器
        logger.info('以开发模式启动 (Flask 内置服务器)')
        app.run(debug=True, host='0.0.0.0', port=5000)
    else:
        # 生产模式：waitress
        from waitress import serve
        logger.info('以生产模式启动 (waitress)')
        serve(app, host='0.0.0.0', port=5000)