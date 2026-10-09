# -*- coding: utf-8 -*-
"""图书管理系统 - Flask + SQLite 版

课程设计项目, 定位为可独立运行的完整演示系统。
本文件位于 03-项目源码 目录下, 所有命令需在该目录内执行。

启动方式(任选其一, 推荐第一种):
    01-【双击开始】.bat        # 双击即可, 自动进入本目录、检查依赖、打开浏览器
    cd 03-项目源码 && python app.py   # 命令行手动启动, 首次运行自动建库
    flask run                # 亦可, 模块导入时已初始化数据库

默认账号:
    管理员 admin / admin123
    读者   reader / reader123

配套文档:
    00-【必读】从这里开始.html   双击可看的图文使用说明
    02-课程设计文档\             需求分析/系统设计/数据库/接口/测试/使用说明书

设计说明(答辩可讲):
    1. 会话密钥使用固定值。教学演示系统不部署生产环境, 固定密钥可保证重启后
       登录状态不失效, 便于课堂演示。若上生产必须改为环境变量读取随机密钥。
    2. 库存扣减采用"条件更新 + 事务"模式, 由数据库层面保证不会超借,
       避免并发下 available 被扣成负数。
    3. 读者注销采用软删除(status 字段), 保留历史借阅记录作为凭证。
    4. 图书删除要求零借阅记录, 避免产生指向不存在图书的孤儿记录。
"""
import math
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, g, redirect, render_template, request, session,
                   flash, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))  # 项目根目录, 用于定位数据库文件
DB_PATH = os.path.join(BASE_DIR, "library.db")  # SQLite 数据库文件绝对路径

app = Flask(__name__)  # 创建 Flask 应用实例

# 会话密钥: 教学场景使用固定值, 避免重启服务导致课堂演示登录状态失效
app.secret_key = "library-system-secret-key-change-me"

# cookie 安全属性: HttpOnly 防止 JS 读取会话, SameSite=Lax 阻断大部分跨站提交
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # 禁止前端 JS 读取 cookie
    SESSION_COOKIE_SAMESITE="Lax",  # 阻断跨站 POST, 降低 CSRF 风险
)

# ----------------------------- 业务参数 -----------------------------
# 集中管理业务规则, 避免魔法数字散落各处, 调整时只需改这一处
DATABASE_DAYS = 60     # 借阅期限(天)
FINE_PER_DAY = 0.1     # 逾期违约金(元/天)
PAGE_SIZE = 10         # 列表分页每页条数
MAX_PAGE_ITEMS = 100   # 单页最大条数, 防止传入超大 per_page 拖垮服务

ROLE_ADMIN = "admin"    # 管理员角色标识
ROLE_READER = "reader"  # 读者角色标识

STATUS_ACTIVE = "active"    # 账号正常状态
STATUS_DELETED = "deleted"  # 账号已注销(软删除, 保留历史借阅记录)

MAX_USERNAME_LEN = 30   # 用户名最大长度
MAX_NAME_LEN = 30       # 姓名最大长度
MAX_PHONE_LEN = 20      # 手机号最大长度
MAX_ISBN_LEN = 20       # ISBN 最大长度
MAX_TEXT_LEN = 100      # 书名/作者/出版社/分类最大长度


# ----------------------------- 数据库 -----------------------------
def get_db():
    """获取当前请求上下文中的数据库连接, 首次调用时创建并绑定到 g"""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)  # 建立与 SQLite 的连接
        g.db.row_factory = sqlite3.Row   # 让查询结果可按列名访问, 更易读
    return g.db


@app.teardown_appcontext
def close_db(exc):
    """请求结束(或异常)时自动关闭连接, 避免连接泄漏"""
    db = g.pop("db", None)  # 从 g 中取出并移除连接
    if db is not None:      # 若连接存在
        db.close()          # 关闭连接释放资源


def _migrate(conn):
    """轻量数据库迁移: 为已存在的旧库补齐新增字段

    课程设计不引入 Alembic 等迁移框架, 采用最小可用方案:
    启动时检查字段是否存在, 缺失则补建, 保证旧 library.db 依然能跑。
    """
    cur = conn.cursor()  # 获取游标
    columns = [r[1] for r in cur.execute("PRAGMA table_info(users)")]  # 读取 users 表字段名
    if "status" not in columns:  # 若缺少 status 字段
        # 补建 status 字段, 默认值 active 以兼容历史数据
        cur.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    cur.execute("UPDATE users SET status='active' WHERE status IS NULL OR status=''")  # 空值归一为 active
    conn.commit()  # 提交迁移结果


def init_db():
    """建表并写入演示数据(幂等, 可重复执行)

    幂等保证: 重复调用不会重复建表, 也不会重复插入演示数据。
    """
    conn = sqlite3.connect(DB_PATH)  # 建立建库专用连接
    cur = conn.cursor()  # 获取游标
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'reader',   -- admin / reader
        name TEXT,
        phone TEXT,
        status TEXT NOT NULL DEFAULT 'active',  -- active / deleted
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        isbn TEXT,
        title TEXT NOT NULL,
        author TEXT,
        publisher TEXT,
        category TEXT,
        price REAL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 1,      -- 总册数
        available INTEGER NOT NULL DEFAULT 1,  -- 可借册数
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS borrows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        borrow_date TEXT NOT NULL,
        due_date TEXT NOT NULL,
        return_date TEXT,               -- NULL 表示未还
        fine REAL DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(book_id) REFERENCES books(id)
    );

    -- 常用查询索引, 避免借阅记录增多后全表扫描拖慢响应
    CREATE INDEX IF NOT EXISTS idx_borrows_user   ON borrows(user_id, return_date);
    CREATE INDEX IF NOT EXISTS idx_borrows_book   ON borrows(book_id, return_date);
    CREATE INDEX IF NOT EXISTS idx_books_category ON books(category);
    """)  # 一次性执行建表语句

    _migrate(conn)  # 补齐旧库缺失的字段

    # 已有演示数据则不再重复插入
    if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        conn.commit()  # 仅提交迁移产生的变更
        conn.close()   # 关闭连接后返回
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 统一时间戳
    # 管理员账号
    cur.execute(
        "INSERT INTO users(username,password,role,name,phone,status,created_at) VALUES(?,?,?,?,?,?,?)",
        ("admin", generate_password_hash("admin123"), ROLE_ADMIN, "系统管理员", "13800000000",
         STATUS_ACTIVE, now),
    )
    # 演示读者账号
    cur.execute(
        "INSERT INTO users(username,password,role,name,phone,status,created_at) VALUES(?,?,?,?,?,?,?)",
        ("reader", generate_password_hash("reader123"), ROLE_READER, "张小明", "13900000000",
         STATUS_ACTIVE, now),
    )
    # 演示图书数据, 覆盖计算机/历史/经济/文学/科幻多个分类便于演示筛选功能
    demo_books = [
        ("9787111213826", "C程序设计", "谭浩强", "清华大学出版社", "计算机", 39.5, 10),
        ("9787302275954", "Java核心技术", "Cay S. Horstmann", "机械工业出版社", "计算机", 108.0, 6),
        ("9787115545381", "Python编程：从入门到实践", "埃里克·马瑟斯", "人民邮电出版社", "计算机", 89.8, 8),
        ("9787121383595", "数据结构（C语言版）", "严蔚敏", "清华大学出版社", "计算机", 45.0, 5),
        ("9787108050403", "人类简史", "尤瓦尔·赫拉利", "中信出版社", "历史", 68.0, 4),
        ("9787508647357", "经济学原理", "曼昆", "中信出版社", "经济", 88.0, 3),
        ("9787540477141", "活着", "余华", "湖南文艺出版社", "文学", 35.0, 6),
        ("9787536692930", "三体", "刘慈欣", "重庆出版社", "科幻", 23.0, 7),
    ]
    for b in demo_books:
        # b = (isbn, title, author, publisher, category, price, total)
        cur.execute(
            "INSERT INTO books(isbn,title,author,publisher,category,price,total,available,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (*b, b[6], now),  # available 初始值等于 total
        )
    conn.commit()  # 提交全部初始数据
    conn.close()   # 关闭连接释放资源


# ----------------------------- 工具函数 -----------------------------
def _to_int(value, default=0):
    """安全转整数: 输入非法时返回默认值, 避免 ValueError 导致 500 错误"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    """安全转浮点数: 输入非法时返回默认值, 避免 ValueError 导致 500 错误"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean(value, max_len):
    """去除首尾空白并按最大长度截断, 防止超长输入撑爆表单或数据库"""
    return (value or "").strip()[:max_len]


def _page_args(default_size=PAGE_SIZE):
    """解析并校验分页参数, 非法值一律回退到安全默认值"""
    page = max(1, _to_int(request.args.get("page"), 1))          # 页码至少为 1
    per_page = _to_int(request.args.get("per_page"), default_size)  # 每页条数
    per_page = min(max(1, per_page), MAX_PAGE_ITEMS)              # 限制在 1~100 之间
    return page, per_page, (page - 1) * per_page                  # 返回页码/每页条数/偏移量


def _pager(page, per_page, total):
    """构造分页信息字典, 供模板渲染翻页控件与页码统计"""
    pages = max(1, math.ceil(total / per_page)) if per_page else 1  # 总页数, 至少 1 页
    page = min(page, pages)                                       # 页码越界时收敛到最后一页
    return {
        "page": page,        # 当前页码
        "pages": pages,      # 总页数
        "total": total,      # 记录总数
        "per_page": per_page,  # 每页条数
        "has_prev": page > 1,   # 是否有上一页
        "has_next": page < pages,  # 是否有下一页
    }


@app.template_filter("money")
def _money(value):
    """模板过滤器: 金额格式化为 ¥xx.xx, 空值显示为占位符"""
    if not value:
        return "—"
    return "￥%.2f" % value


@app.template_filter("dash")
def _dash(value):
    """模板过滤器: 空值统一显示为占位符"""
    return value if value else "—"


# ----------------------------- 登录控制 -----------------------------
def login_required(view):
    """装饰器: 要求已登录才能访问, 未登录跳转登录页"""

    @wraps(view)  # 保留原函数元信息, 避免函数名被覆盖
    def wrapped(*args, **kwargs):
        if "user_id" not in session:      # 未登录
            flash("请先登录", "warning")   # 提示用户
            return redirect(url_for("login"))  # 跳转登录页
        return view(*args, **kwargs)      # 已登录则正常执行视图

    return wrapped


def admin_required(view):
    """装饰器: 要求管理员身份, 读者访问时提示并跳回首页"""

    @wraps(view)  # 保留原函数元信息
    def wrapped(*args, **kwargs):
        if "user_id" not in session:             # 未登录
            flash("请先登录", "warning")          # 提示登录
            return redirect(url_for("login"))     # 跳转登录页
        if session.get("role") != ROLE_ADMIN:    # 已登录但非管理员
            flash("该功能仅管理员可用", "danger")  # 提示权限不足
            return redirect(url_for("index"))     # 跳回首页
        return view(*args, **kwargs)             # 管理员正常访问

    return wrapped


@app.context_processor
def inject_user():
    """模板全局变量: 让所有页面都能访问当前登录用户与角色"""
    return {"current_user": session.get("name"), "role": session.get("role")}


# ----------------------------- 认证 -----------------------------
@app.route("/")
def index():
    """首页: 已登录进控制台, 未登录去登录页"""
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页: 校验用户名密码, 成功后写入会话"""
    if "user_id" in session:
        # 已登录仍访问登录页, 直接跳控制台, 避免出现"登录框套着导航栏"的矛盾界面
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = _clean(request.form.get("username"), MAX_USERNAME_LEN)  # 取用户名并清洗
        password = request.form.get("password", "")                       # 取密码(密码不做截断)
        user = get_db().execute(                          # 按用户名查询用户
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        # 用户存在、密码正确、且账号未被注销, 三者同时满足才算通过
        if user and user["status"] == STATUS_ACTIVE and check_password_hash(user["password"], password):
            session.update(          # 写入会话
                user_id=user["id"],
                username=user["username"],
                role=user["role"],
                name=user["name"],
            )
            flash(f"欢迎回来，{user['name']}！", "success")  # 欢迎提示
            return redirect(url_for("dashboard"))             # 进入控制台
        flash("用户名或密码错误，或账号已注销", "danger")      # 统一提示, 不暴露账号是否存在
    return render_template("login.html")  # 渲染登录页


@app.route("/register", methods=["GET", "POST"])
def register():
    """读者注册: 校验格式后创建 reader 角色账号"""
    if "user_id" in session:
        # 已登录仍访问注册页, 直接跳控制台
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = _clean(request.form.get("username"), MAX_USERNAME_LEN)  # 用户名
        password = request.form.get("password", "")                         # 密码
        name = _clean(request.form.get("name"), MAX_NAME_LEN)               # 姓名
        phone = _clean(request.form.get("phone"), MAX_PHONE_LEN)            # 手机号

        if not username or not password or not name:
            flash("请填写完整的注册信息", "danger")          # 必填项校验
        elif len(password) < 6:
            flash("密码至少 6 位", "danger")                 # 密码强度校验
        elif not _is_username(username):
            flash("用户名只能包含字母、数字、下划线", "danger")  # 用户名字符集校验
        elif phone and not _is_phone(phone):
            flash("手机号格式不正确", "danger")                # 手机号格式校验
        else:
            db = get_db()  # 获取数据库连接
            try:
                db.execute(  # 插入新用户, 固定为 reader 角色
                    "INSERT INTO users(username,password,role,name,phone,status,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (username, generate_password_hash(password), ROLE_READER, name, phone,
                     STATUS_ACTIVE, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                db.commit()              # 提交事务
                flash("注册成功，请登录", "success")  # 提示注册成功
                return redirect(url_for("login"))      # 跳转登录页
            except sqlite3.IntegrityError:
                flash("用户名已存在", "danger")          # 唯一约束冲突
    return render_template("register.html")  # 渲染注册页


def _is_username(value):
    """校验用户名: 仅允许字母、数字、下划线, 长度 3~30"""
    if not 3 <= len(value) <= MAX_USERNAME_LEN:
        return False
    return all(ch.isalnum() or ch == "_" for ch in value)


def _is_phone(value):
    """校验手机号: 11 位数字, 且以 1 开头"""
    return value.isdigit() and len(value) == 11 and value.startswith("1")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """退出登录: 清空会话

    同时接受 GET 和 POST: 页面导航链接走 GET, 脚本/测试提交走 POST 都能用。
    """
    session.clear()                   # 清空所有会话数据
    flash("已退出登录", "info")        # 提示
    return redirect(url_for("login"))  # 跳转登录页


# ----------------------------- 首页 / 仪表盘 -----------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    """控制台: 管理员看全馆统计, 读者看自己的借阅"""
    db = get_db()  # 获取数据库连接
    today = date.today().isoformat()  # 今日日期字符串

    # 统计卡: 图书种类 / 馆藏总量 / 当前借出数
    stats = {
        "book_kinds": db.execute("SELECT COUNT(*) c FROM books").fetchone()["c"],
        "book_total": db.execute("SELECT IFNULL(SUM(total),0) c FROM books").fetchone()["c"],
        "borrowing": db.execute(
            "SELECT COUNT(*) c FROM borrows WHERE return_date IS NULL"
        ).fetchone()["c"],
    }

    if session["role"] == ROLE_ADMIN:  # 管理员视角: 额外统计读者数与全馆动态
        stats["readers"] = db.execute(
            "SELECT COUNT(*) c FROM users WHERE role=? AND status=?", (ROLE_READER, STATUS_ACTIVE)
        ).fetchone()["c"]
        # 最新 8 条借阅动态, 带上读者姓名
        recent = db.execute(
            "SELECT b.title, u.name, br.borrow_date FROM borrows br "
            "JOIN books b ON b.id=br.book_id JOIN users u ON u.id=br.user_id "
            "ORDER BY br.id DESC LIMIT 8"
        ).fetchall()
    else:  # 读者视角: 只看自己的记录, 并由 SQL 计算逾期标记与应计违约金
        recent = db.execute(
            "SELECT b.title, br.borrow_date, br.due_date, br.return_date, "
            # 用 julianday 计算未还图书的实时逾期天数, 避免在模板中做字符串日期比较
            "CASE WHEN br.return_date IS NULL AND br.due_date < ? THEN 1 ELSE 0 END AS overdue "
            "FROM borrows br JOIN books b ON b.id=br.book_id "
            "WHERE br.user_id=? ORDER BY br.id DESC LIMIT 8",
            (today, session["user_id"]),
        ).fetchall()

    return render_template("dashboard.html", stats=stats, recent=recent, today=today)


# ----------------------------- 图书管理 -----------------------------
@app.route("/books")
@login_required
def books():
    """图书检索: 支持关键词模糊搜索与分类筛选, 带分页"""
    keyword = _clean(request.args.get("kw"), MAX_TEXT_LEN)   # 关键词
    category = _clean(request.args.get("category"), MAX_TEXT_LEN)  # 分类

    # 动态拼接查询条件, 所有用户输入均以参数绑定方式传入, 杜绝 SQL 注入
    # 说明: WHERE 条件单独拼进 where 片段, 最后统一拼到"结果查询"和"总数查询"两处。
    # 不能用 index("WHERE") 截取, 否则子查询里的 WHERE 会被误选中。
    where = ""  # 查询条件片段(不含 WHERE 关键字本身)
    params = []  # 条件参数, 与 where 中的占位符顺序一一对应
    if keyword:                            # 关键词搜索: 书名/作者/ISBN 模糊匹配
        where += " AND (b.title LIKE ? OR b.author LIKE ? OR b.isbn LIKE ?)"
        params += [f"%{keyword}%"] * 3     # 三个字段使用同一关键词
    if category:                           # 按分类精确筛选
        where += " AND b.category=?"
        params.append(category)

    # 借阅记录数通过关联子查询一并取出, 供模板禁用"删除"按钮
    count_sql = ("SELECT COUNT(*) FROM books b WHERE 1=1" + where)  # 总数查询
    data_sql = ("SELECT b.*, "
                "(SELECT COUNT(*) FROM borrows br WHERE br.book_id = b.id) AS borrow_count "
                "FROM books b WHERE 1=1" + where + " ORDER BY b.id DESC")  # 结果查询

    total = get_db().execute(count_sql, params).fetchone()[0]  # 记录总数, 用于分页控件

    page, per_page, offset = _page_args()  # 解析分页参数
    rows = get_db().execute(               # 查询当前页数据
        data_sql + " LIMIT ? OFFSET ?", params + [per_page, offset]
    ).fetchall()
    categories = [                         # 全部可选分类, 用于下拉框
        r["category"]
        for r in get_db().execute(
            "SELECT DISTINCT category FROM books WHERE category IS NOT NULL AND category<>'' "
            "ORDER BY category"
        ).fetchall()
    ]
    return render_template(
        "books.html", books=rows, kw=keyword, category=category,
        categories=categories, pager=_pager(page, per_page, total),
    )


@app.route("/books/new", methods=["GET", "POST"])
@admin_required
def book_new():
    """新增图书(仅管理员): 服务端校验表单后入库"""
    if request.method == "POST":
        data, error = _parse_book_form()  # 解析并校验表单
        if error:                         # 校验不通过
            flash(error, "danger")        # 回显错误原因
            # 传 request.form 让模板回显用户已填内容, 避免重新录入
            return render_template("book_form.html", book=None, form=request.form)
        db = get_db()                     # 获取连接
        with db:                          # 事务: 保证插入与后续操作原子性
            db.execute(                   # 插入图书记录
                "INSERT INTO books(isbn,title,author,publisher,category,price,total,available,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (*data, data[6], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),  # available 初始=total
            )
        flash("图书添加成功", "success")  # 成功提示
        return redirect(url_for("books"))  # 返回列表页
    # GET 新增页: 传空 form, 让模板能安全调用 form.get() 取默认值
    return render_template("book_form.html", book=None, form={})


@app.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
@admin_required
def book_edit(book_id):
    """编辑图书(仅管理员): 总数不得小于已借出数量"""
    db = get_db()  # 获取连接
    book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()  # 查图书
    if book is None:                                    # 图书不存在
        flash("图书不存在", "danger")
        return redirect(url_for("books"))
    if request.method == "POST":
        data, error = _parse_book_form()  # 解析并校验表单
        borrowed = db.execute(            # 查询当前未归还数量
            "SELECT COUNT(*) c FROM borrows WHERE book_id=? AND return_date IS NULL",
            (book_id,),
        ).fetchone()["c"]
        # 馆藏总数不能少于已借出数量, 否则库存会被算成负数
        if not error and data[6] < borrowed:
            error = f"该书尚有 {borrowed} 册未归还, 馆藏总数不能小于 {borrowed}"
        if error:                         # 校验不通过
            flash(error, "danger")        # 回显错误
            # 传 request.form 回显输入, 同时传 book 保留原有记录供展示
            return render_template("book_form.html", book=book, form=request.form)
        # 可借册数由系统反推, 不允许手工填写, 避免与实际借阅情况不一致
        available = max(data[6] - borrowed, 0)
        with db:                          # 事务: 保证更新原子性
            db.execute(                   # 更新图书信息
                "UPDATE books SET isbn=?,title=?,author=?,publisher=?,category=?,price=?,total=?,available=? "
                "WHERE id=?",
                (*data, available, book_id),
            )
        flash("图书信息已更新", "success")
        return redirect(url_for("books"))
    # GET 编辑页: 传空 form, 优先回显 book 中的现有值
    return render_template("book_form.html", book=book, form={})


def _parse_book_form():
    """解析并校验图书表单

    返回 (数据元组, 错误信息)。校验通过时错误信息为 None。
    服务端做完整校验, 前端 HTML 校验仅用于提升体验, 不能作为唯一防线。
    """
    isbn = _clean(request.form.get("isbn"), MAX_ISBN_LEN)      # ISBN
    title = _clean(request.form.get("title"), MAX_TEXT_LEN)    # 书名
    author = _clean(request.form.get("author"), MAX_TEXT_LEN)  # 作者
    publisher = _clean(request.form.get("publisher"), MAX_TEXT_LEN)  # 出版社
    category = _clean(request.form.get("category"), MAX_TEXT_LEN)    # 分类
    price = _to_float(request.form.get("price"), -1)  # 价格, 默认 -1 便于识别非法输入
    total = _to_int(request.form.get("total"), 0)      # 馆藏总数, 默认 0 便于识别非法输入

    if not title:                    # 书名必填
        return None, "请填写书名"
    if price < 0:                    # 价格非负
        return None, "价格格式不正确或不能为负数"
    if total < 1:                    # 总数至少 1 册
        return None, "馆藏总数必须为不小于 1 的整数"
    if total > 100000:               # 防止误输入超大数量
        return None, "馆藏总数超出合理范围"
    return (isbn, title, author, publisher, category, price, total), None  # 校验通过


@app.route("/books/<int:book_id>/delete", methods=["POST"])
@admin_required
def book_delete(book_id):
    """删除图书(仅管理员): 只要存在借阅记录就不允许删除

    校验规则比"是否有未归还记录"更严格: 只要书被借过(含已归还)就拒绝删除。
    否则删除后, 原借阅记录会指向一个不存在的图书, 形成孤儿数据;
    而这些借阅记录又关联着读者的借阅凭证, 不能一并删掉。
    """
    db = get_db()  # 获取连接
    total_count = db.execute(  # 统计该书的全部借阅记录, 含已归还
        "SELECT COUNT(*) c FROM borrows WHERE book_id=?",
        (book_id,),
    ).fetchone()["c"]
    if total_count:  # 只要有过流通记录
        # 用更明确的提示说明原因, 让管理员知道为何不能删
        flash(f"该图书已有 {total_count} 条借阅记录，为保证凭证完整无法删除", "danger")
    else:
        with db:  # 事务
            db.execute("DELETE FROM books WHERE id=?", (book_id,))  # 删除图书
        flash("图书已删除", "success")
    return redirect(url_for("books"))


# ----------------------------- 读者管理（管理员） -----------------------------
@app.route("/readers")
@admin_required
def readers():
    """读者列表(仅管理员): 展示在借数量, 分页呈现"""
    page, per_page, offset = _page_args()  # 解析分页参数
    db = get_db()  # 获取连接
    # 只统计未注销的读者
    total = db.execute(
        "SELECT COUNT(*) FROM users WHERE role=? AND status=?",
        (ROLE_READER, STATUS_ACTIVE),
    ).fetchone()[0]
    rows = db.execute(  # 查询当前页读者及其在借数量
        "SELECT u.*, "
        "(SELECT COUNT(*) FROM borrows b WHERE b.user_id=u.id AND b.return_date IS NULL) AS borrowing "
        "FROM users u WHERE u.role=? AND u.status=? ORDER BY u.id DESC LIMIT ? OFFSET ?",
        (ROLE_READER, STATUS_ACTIVE, per_page, offset),
    ).fetchall()
    return render_template("readers.html", readers=rows, pager=_pager(page, per_page, total))


@app.route("/readers/<int:user_id>/delete", methods=["POST"])
@admin_required
def reader_delete(user_id):
    """注销读者(仅管理员): 软删除, 保留历史借阅记录

    采用软删除而非物理删除: 借阅记录属于业务凭证, 读者注销后仍需可追溯。
    """
    db = get_db()  # 获取连接
    borrowing = db.execute(  # 统计该读者未归还数量
        "SELECT COUNT(*) c FROM borrows WHERE user_id=? AND return_date IS NULL",
        (user_id,),
    ).fetchone()["c"]
    if borrowing:                                        # 仍有在借
        flash("该读者尚有未还图书，无法注销", "danger")  # 拒绝操作
    else:
        with db:  # 事务
            # 软删除: 标记状态而非删除记录, 借阅历史完整保留
            db.execute(
                "UPDATE users SET status=? WHERE id=? AND role=? AND status=?",
                (STATUS_DELETED, user_id, ROLE_READER, STATUS_ACTIVE),
            )
        flash("读者已注销（历史借阅记录保留）", "success")
    return redirect(url_for("readers"))


# ----------------------------- 借阅 / 归还 -----------------------------
@app.route("/borrow/<int:book_id>", methods=["POST"])
@login_required
def borrow(book_id):
    """借书: 采用条件更新保证并发下不会超借

    并发安全要点: 不做"先查库存再扣减"的分步操作, 而是让数据库在 UPDATE 时
    自行判断 available > 0, 只有确实扣减成功(rowcount=1)才写入借阅记录。
    这样即便两人同时借最后一本, 也只有一人成功, 不会出现库存为负。
    """
    db = get_db()  # 获取连接
    book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()  # 查图书
    if book is None:                                       # 图书不存在
        flash("图书不存在", "danger")
        return redirect(url_for("books"))
    # 校验同一本书是否重复在借(已在借则不允许再借同一本)
    already = db.execute(
        "SELECT COUNT(*) c FROM borrows WHERE user_id=? AND book_id=? AND return_date IS NULL",
        (session["user_id"], book_id),
    ).fetchone()["c"]
    if already:
        flash("你已借阅该书且尚未归还", "warning")
        return redirect(url_for("my_borrows"))

    today = date.today()                            # 借阅日期
    due = today + timedelta(days=DATABASE_DAYS)    # 应还日期 = 借阅日 + 借阅期限

    try:
        with db:  # 事务: 扣库存与写借阅记录要么都成功, 要么都回滚
            # 条件更新: 仅当 available > 0 时才扣减, 由数据库保证原子性
            cur = db.execute(
                "UPDATE books SET available = available - 1 WHERE id=? AND available > 0",
                (book_id,),
            )
            if cur.rowcount == 0:  # 未更新到任何行, 说明已被他人借空
                raise ValueError("该图书库存不足，暂无法借阅")
            db.execute(            # 扣减成功后才写入借阅记录
                "INSERT INTO borrows(user_id,book_id,borrow_date,due_date) VALUES(?,?,?,?)",
                (session["user_id"], book_id, today.isoformat(), due.isoformat()),
            )
    except ValueError as exc:  # 库存不足等业务异常
        db.rollback()          # 回滚已执行的部分(事务上下文已自动回滚, 此处兜底)
        flash(str(exc), "danger")
        return redirect(url_for("books"))

    flash(f"借阅成功！请于 {due.isoformat()} 前归还", "success")
    return redirect(url_for("my_borrows"))


@app.route("/return/<int:borrow_id>", methods=["POST"])
@login_required
def return_book(borrow_id):
    """归还: 计算逾期违约金并回补库存, 管理员可代还"""
    db = get_db()  # 获取连接
    br = db.execute("SELECT * FROM borrows WHERE id=?", (borrow_id,)).fetchone()  # 查借阅记录
    if br is None or br["return_date"] is not None:  # 记录不存在或已归还
        flash("借阅记录不存在或已归还", "danger")
        return redirect(url_for("my_borrows"))
    # 权限校验: 只能还自己的书, 管理员可代还任何人的书
    if br["user_id"] != session["user_id"] and session["role"] != ROLE_ADMIN:
        flash("无权操作他人的借阅记录", "danger")
        return redirect(url_for("my_borrows"))

    today = date.today()                                # 归还日期
    due = date.fromisoformat(br["due_date"])            # 应还日期
    overdue_days = (today - due).days                   # 逾期天数, 负数表示未逾期
    fine = round(overdue_days * FINE_PER_DAY, 2) if overdue_days > 0 else 0.0  # 逾期违约金

    try:
        with db:  # 事务: 更新借阅记录与回补库存原子完成
            db.execute(  # 写入归还日期与违约金
                "UPDATE borrows SET return_date=?, fine=? WHERE id=? AND return_date IS NULL",
                (today.isoformat(), fine, borrow_id),
            )
            # 回补库存, 用 MIN 限制上限防止数据异常时库存超过总册数
            db.execute(
                "UPDATE books SET available = MIN(available + 1, total) WHERE id=?",
                (br["book_id"],),
            )
    except sqlite3.Error:  # 数据库异常兜底
        db.rollback()
        flash("归还失败，请稍后重试", "danger")
        return redirect(url_for("my_borrows"))

    if fine > 0:  # 有逾期
        flash(f"已归还，逾期 {overdue_days} 天，违约金 {fine} 元（每天 {FINE_PER_DAY} 元）", "warning")
    else:
        flash("归还成功", "success")
    return redirect(url_for("my_borrows"))


@app.route("/my")
@login_required
def my_borrows():
    """我的借阅: 分页展示, 逾期标记与应计违约金由 SQL 计算"""
    page, per_page, offset = _page_args()  # 解析分页参数
    today = date.today().isoformat()  # 今日日期
    db = get_db()  # 获取连接

    total = db.execute(  # 统计本人借阅总数
        "SELECT COUNT(*) FROM borrows WHERE user_id=?", (session["user_id"],)
    ).fetchone()[0]
    rows = db.execute(  # 查询当前页借阅记录
        "SELECT br.*, b.title, b.author, "
        # 未归还且已过应还日 -> overdue=1, 并实时计算应计违约金
        "CASE WHEN br.return_date IS NULL AND br.due_date < ? THEN 1 ELSE 0 END AS overdue, "
        "CASE WHEN br.return_date IS NULL AND br.due_date < ? "
        "  THEN ROUND((CAST(julianday(?) - julianday(br.due_date) AS INTEGER)) * ?, 2) "
        "  ELSE 0 END AS accrued "
        "FROM borrows br JOIN books b ON b.id=br.book_id "
        "WHERE br.user_id=? ORDER BY br.id DESC LIMIT ? OFFSET ?",
        (today, today, today, FINE_PER_DAY, session["user_id"], per_page, offset),
    ).fetchall()
    return render_template(
        "my_borrows.html", rows=rows, today=today, pager=_pager(page, per_page, total)
    )


@app.route("/borrows")
@admin_required
def all_borrows():
    """全部借阅记录(仅管理员): 分页 + 逾期统计"""
    page, per_page, offset = _page_args()  # 解析分页参数
    today = date.today().isoformat()  # 今日日期
    db = get_db()  # 获取连接

    # 查询条件: 支持按状态与关键词快速筛选
    status = _clean(request.args.get("status"), 20)  # on(在借) / overdue(逾期) / 空(全部)
    keyword = _clean(request.args.get("kw"), MAX_TEXT_LEN)  # 书名或读者姓名关键词

    # WHERE 条件单独拼进 where 片段, 最后统一拼到"结果查询"和"总数查询"两处,
    # 避免用 index("WHERE") 截取时被子查询中的 WHERE 误中
    joins = ("FROM borrows br "
             "JOIN books b ON b.id=br.book_id JOIN users u ON u.id=br.user_id")  # 表关联部分
    where = ""       # 查询条件片段(不含 WHERE 关键字本身)
    params = []      # 条件参数, 与 where 中的占位符顺序一一对应
    if status == "on":            # 仅看在借
        where += " AND br.return_date IS NULL"
    elif status == "overdue":     # 仅看逾期
        where += " AND br.return_date IS NULL AND br.due_date < ?"
        params.append(today)
    if keyword:                   # 书名或读者姓名模糊搜索
        where += " AND (b.title LIKE ? OR u.name LIKE ?)"
        params += [f"%{keyword}%"] * 2

    total = db.execute(  # 按同条件统计总数
        f"SELECT COUNT(*) {joins} WHERE 1=1{where}", params,
    ).fetchone()[0]
    rows = db.execute(  # 查询当前页数据
        f"SELECT br.*, b.title, u.name AS reader_name {joins} "
        f"WHERE 1=1{where} ORDER BY br.id DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()
    return render_template(
        "borrows.html", rows=rows, pager=_pager(page, per_page, total),
        status=status, kw=keyword, today=today,
    )


# 模块导入时初始化数据库, 确保 flask run 等任意启动方式都能拿到可用数据
init_db()


if __name__ == "__main__":
    # Windows 控制台默认 GBK 编码, 中文打印会乱码, 此处统一转为 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass  # 个别环境不支持重配置, 忽略即可

    print("=" * 50)
    print("图书管理系统已启动")
    print("访问地址: http://127.0.0.1:5000")
    print("管理员账号: admin / admin123")
    print("读者账号:   reader / reader123")
    print("=" * 50)
    # 关闭自动重载: 课堂演示时避免文件变动触发重启导致会话失效
    app.run(debug=True, use_reloader=False, host="127.0.0.1", port=5000)