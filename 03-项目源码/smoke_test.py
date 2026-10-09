# -*- coding: utf-8 -*-
"""端到端冒烟测试(基于真实 HTTP 请求)

与 unit_test/ 的区别:
    unit_test/  使用 Flask 测试客户端, 无需启动服务, 覆盖细粒度断言。
    本脚本     启动真实服务后用 HTTP 请求走完整流程, 验证"实际能跑起来"。

用途:
    交付前自检——确认服务能启动、各页面能访问、核心流程能走通。

运行方式:
    python app.py                      # 先在一个终端启动服务
    python smoke_test.py               # 在另一个终端运行本脚本

脚本会自行创建临时数据并在结束时清理, 不会污染 library.db 的演示数据。
"""
import http.cookiejar  # Cookie 管理, 维持登录会话
import re  # 正则表达式, 用于从 HTML 中提取链接
import sqlite3  # 数据库查询, 用于精确定位数据 id
import sys  # 系统退出码
import urllib.parse  # URL 编解码
import urllib.request  # HTTP 请求

import app as app_module  # 引入被测应用, 直接复用其数据库路径, 确保操作同一份数据

BASE = "http://127.0.0.1:5000"  # 服务地址
DB_PATH = app_module.DB_PATH    # 复用应用的数据库路径

# 测试用的临时数据标识, 结束后按此清理
MARK = "冒烟测试"  # 仅用于 LIKE 匹配, 不含下划线(避免被当作通配符)
TEST_SUFFIX = "_" + MARK  # 拼在测试数据标题/用户名后面的后缀


def _log(step, detail=""):
    """打印测试步骤日志, 便于追踪执行过程"""
    print(f"[SMOKE] {step}" + (f" -> {detail}" if detail else ""))


def new_client():
    """创建带 Cookie 的独立客户端, 相当于一个独立的浏览器会话"""
    cj = http.cookiejar.CookieJar()  # 创建 Cookie 罐
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))  # 返回携带 Cookie 的打开器


def get(opener, path):
    """发送 GET 请求, 返回 (最终URL, 页面HTML)"""
    resp = opener.open(BASE + path)
    return resp.geturl(), resp.read().decode("utf-8")


def post(opener, path, data):
    """发送 POST 请求, 返回 (最终URL, 页面HTML)"""
    body = urllib.parse.urlencode(data).encode()  # 表单编码为请求体
    resp = opener.open(BASE + path, body)
    return resp.geturl(), resp.read().decode("utf-8")


def query(sql, params=()):
    """直接查询数据库获取 id, 比从 HTML 里猜链接更可靠"""
    conn = sqlite3.connect(DB_PATH)  # 建立只读查询连接
    conn.row_factory = sqlite3.Row
    row = conn.execute(sql, params).fetchone()  # 执行查询取首行
    conn.close()  # 及时关闭连接
    return row


def cleanup():
    """清理本次测试产生的临时数据"""
    _log("cleanup", "清理测试数据")
    conn = sqlite3.connect(DB_PATH)  # 建立清理用连接
    # 顺序必须是"先借阅、后图书": 图书删除受借阅记录约束, 反过来会被拒绝
    conn.execute(
        "DELETE FROM borrows WHERE book_id IN (SELECT id FROM books WHERE title LIKE ?)",
        (f"%{MARK}%",),
    )
    conn.execute("DELETE FROM books WHERE title LIKE ?", (f"%{MARK}%",))
    conn.execute("DELETE FROM users WHERE username LIKE ?", (f"%{MARK}%",))
    conn.commit()  # 提交清理
    conn.close()  # 关闭连接


ok = True  # 全局测试结果标记


def check(name, cond):
    """记录单条断言结果, 失败时将全局标记置为 False"""
    global ok
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        ok = False


def main():
    """执行完整冒烟测试流程"""
    print("=" * 60)
    print("端到端冒烟测试")
    print("=" * 60)

    cleanup()  # 先清理历史残留

    # --- 1. 匿名访问应被拦截 ---
    _log("1. 匿名访问与登录页")
    anon = new_client()
    url, _ = get(anon, "/books")
    check("匿名访问 /books 重定向到登录页", "/login" in url)
    _, html = get(anon, "/login")
    check("登录页可访问", "登录" in html)

    # --- 2. 管理员登录 ---
    _log("2. 管理员登录")
    admin = new_client()
    url, html = post(admin, "/login", {"username": "admin", "password": "admin123"})
    check("管理员登录成功", "/dashboard" in url)
    check("控制台统计卡渲染", "馆藏总量" in html)

    # --- 3. 管理员新增图书 ---
    _log("3. 新增图书")
    book_title = "测试图书" + TEST_SUFFIX
    url, html = post(admin, "/books/new", {
        "isbn": "9787000000888", "title": book_title, "author": "测试",
        "publisher": "测试出版社", "category": "测试分类", "price": "88.8", "total": "3",
    })
    check("新增图书成功", "图书添加成功" in html)
    book = query("SELECT id FROM books WHERE title=?", (book_title,))
    check("图书已入库", book is not None)
    book_id = book["id"] if book else 0

    # --- 4. 表单校验 ---
    # 注意: 校验按"书名 -> 价格 -> 总数"顺序短路返回, 一次只提示第一个错误,
    # 因此每个用例需单独构造只触发该项错误的输入。
    _log("4. 服务端表单校验")
    _, html = post(admin, "/books/new", {"title": "", "price": "10", "total": "1"})
    check("空书名被拒绝", "请填写书名" in html)

    _, html = post(admin, "/books/new", {"title": "非法价格测试", "price": "abc", "total": "1"})
    check("非法价格被拒绝且不报 500", "价格格式不正确" in html)

    _, html = post(admin, "/books/new", {"title": "非法总数测试", "price": "10", "total": "0"})
    check("非法总数被拒绝", "馆藏总数必须" in html)

    _, html = post(admin, "/books/new", {"title": "负价格测试", "price": "-5", "total": "1"})
    check("负价格被拒绝", "价格格式不正确" in html)

    # --- 5. 读者借书 ---
    _log("5. 读者借书")
    reader = new_client()
    url, html = post(reader, "/login", {"username": "reader", "password": "reader123"})
    check("读者登录成功", "/dashboard" in url)

    _, html = post(reader, f"/borrow/{book_id}", {})
    check("借书成功", "借阅成功" in html)
    book = query("SELECT available FROM books WHERE id=?", (book_id,))
    check("库存已扣减为 2", book["available"] == 2)

    # --- 6. 重复借阅拦截 ---
    _log("6. 重复借阅拦截")
    _, html = post(reader, f"/borrow/{book_id}", {})
    check("重复借同一本书被拒绝", "尚未归还" in html)

    # --- 7. 我的借阅 ---
    _log("7. 查看我的借阅")
    _, html = get(reader, "/my")
    check("我的借阅页可见记录", book_title in html)
    check("状态标记为借阅中", "借阅中" in html)

    # --- 8. 归还 ---
    _log("8. 归还图书")
    rec = query("SELECT id FROM borrows WHERE book_id=? AND return_date IS NULL", (book_id,))
    check("借阅记录已生成", rec is not None)
    if rec:
        _, html = post(reader, f"/return/{rec['id']}", {})
        check("归还成功", "归还成功" in html)
        book = query("SELECT available FROM books WHERE id=?", (book_id,))
        check("库存已回补为 3", book["available"] == 3)

    # --- 9. 权限隔离 ---
    _log("9. 权限隔离")
    _, html = get(reader, "/readers")
    check("读者无法访问读者管理", "该功能仅管理员可用" in html)

    # --- 10. 管理端页面 ---
    _log("10. 管理端页面")
    for path, keyword, label in [
        ("/books", book_title, "图书检索"),
        ("/readers", "张小明", "读者管理"),
        ("/borrows", "借阅记录", "全部借阅记录"),
    ]:
        _, html = get(admin, path)
        check(f"{label}页可访问", keyword in html)

    # --- 11. 分页 ---
    _log("11. 分页")
    _, html = get(admin, "/books?per_page=3")
    check("分页控件正常渲染", "共" in html and "条" in html)

    # --- 12. 编辑图书 ---
    _log("12. 编辑图书")
    _, html = post(admin, f"/books/{book_id}/edit", {
        "isbn": "9787000000888", "title": book_title, "author": "测试作者改",
        "publisher": "测试出版社", "category": "测试分类", "price": "99.9", "total": "5",
    })
    check("编辑图书成功", "图书信息已更新" in html)
    book = query("SELECT author, total, available FROM books WHERE id=?", (book_id,))
    check("作者已更新", book["author"] == "测试作者改")
    check("总数已更新为 5", book["total"] == 5)
    check("可借数同步为 5", book["available"] == 5)

    # --- 13. 删除图书 ---
    # 分两种情况: 已流通的书应被拒绝(防孤儿记录), 从未借出的书可删除
    _log("13. 删除图书")
    _, html = post(admin, f"/books/{book_id}/delete", {})
    check("已流通图书删除被拒绝", "借阅记录" in html)
    check("已流通图书仍在库中", query("SELECT id FROM books WHERE id=?", (book_id,)) is not None)

    fresh_title = "全新图书" + TEST_SUFFIX
    post(admin, "/books/new", {
        "isbn": "9787000000777", "title": fresh_title, "author": "测试",
        "publisher": "测试出版社", "category": "测试分类", "price": "66.6", "total": "1",
    })
    fresh = query("SELECT id FROM books WHERE title=?", (fresh_title,))
    fresh_id = fresh["id"] if fresh else 0
    _, html = post(admin, f"/books/{fresh_id}/delete", {})
    check("未借出图书删除成功", "图书已删除" in html)
    check("未借出图书已从库中移除", query("SELECT id FROM books WHERE id=?", (fresh_id,)) is None)

    # --- 14. 注册新读者 ---
    _log("14. 注册新读者")
    new_user = "newuser" + TEST_SUFFIX
    _, html = post(new_client(), "/register", {
        "username": new_user, "password": "abc123456", "name": "冒烟新用户", "phone": "13800002222",
    })
    check("注册成功", "注册成功" in html)
    _, html = post(new_client(), "/login", {"username": new_user, "password": "abc123456"})
    check("新读者可登录", "欢迎回来" in html)

    cleanup()  # 清理本次测试数据

    print("=" * 60)
    print("全部通过" if ok else "存在失败项")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())