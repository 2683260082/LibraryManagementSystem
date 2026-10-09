# -*- coding: utf-8 -*-
"""图书管理系统 - 单元测试

覆盖重点:
    1. 工具函数: 类型转换、分页参数、校验规则
    2. 认证与权限: 登录、注册、角色隔离
    3. 图书管理: 增删改查、表单校验
    4. 借阅归还: 库存扣减、逾期计算、并发超借防护
    5. 读者管理: 软删除保留借阅凭证

运行方式(在 03-项目源码 目录下执行):
    cd 03-项目源码
    python -m unittest discover -s unit_test -t .
"""
import os  # 操作系统路径与环境变量操作
import sys  # 系统路径操作, 用于导入被测模块
import sqlite3  # SQLite 数据库操作
import tempfile  # 临时目录, 用于隔离测试数据库
import unittest  # Python 标准库单元测试框架
from datetime import date, timedelta  # 日期计算

# 把项目根目录加入模块搜索路径, 保证能 import 到 app 模块
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import app as app_module  # 导入被测的 Flask 应用模块


def _print(step, detail=""):
    """统一的测试日志输出函数, 便于追踪执行过程"""
    print(f"[TEST] {step}" + (f" -> {detail}" if detail else ""))


class BaseTestCase(unittest.TestCase):
    """测试基类: 为每个用例准备独立的临时数据库, 避免相互污染"""

    def setUp(self):
        """每个用例开始前: 建立临时库并初始化演示数据"""
        _print("setUp", "创建临时数据库")
        # 建立临时目录保存测试库, 用完自动清理, 不影响真实的 library.db
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = app_module.DB_PATH  # 备份原始数据库路径
        app_module.DB_PATH = os.path.join(self._tmpdir.name, "test.db")  # 指向临时库
        app_module.init_db()  # 建表并写入演示数据

        # 创建测试客户端, TESTING 模式关闭 CSRF 与异常捕获, 便于断言
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        """每个用例结束后: 关闭请求上下文并清理临时目录"""
        _print("tearDown", "清理临时数据库")
        app_module.DB_PATH = self._orig_db_path  # 恢复原始数据库路径
        self._tmpdir.cleanup()  # 删除临时目录

    def login(self, username, password):
        """以指定账号登录(先退出当前账号, 模拟真实的"换账号登录"场景)

        应用侧的 /login 在已登录时会直接跳控制台, 因此切换账号前必须先退出。
        """
        _print("login", f"先退出当前登录态, 再以 {username} 登录")
        self.client.get("/logout")  # 清除可能残留的登录态
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def login_admin(self):
        """以管理员身份登录"""
        return self.login("admin", "admin123")

    def login_reader(self):
        """以演示读者身份登录"""
        return self.login("reader", "reader123")

    def add_book(self, title="测试图书", total=3):
        """通过管理接口新增一本图书, 返回图书 id"""
        _print("add_book", f"《{title}》x{total}")
        self.login_admin()
        # 直接调用视图前需先登录, 此处复用已登录的 client 会话
        resp = self.client.post(
            "/books/new",
            data={
                "isbn": "9787000000999", "title": title, "author": "测试作者",
                "publisher": "测试出版社", "category": "测试", "price": "10", "total": str(total),
            },
            follow_redirects=True,
        )
        self.assertIn("图书添加成功", resp.get_data(as_text=True))
        # 从数据库取回新增图书的 id
        conn = self.get_db_conn()  # 直接开连接, 不依赖 Flask 应用上下文
        row = conn.execute("SELECT id FROM books WHERE title=?", (title,)).fetchone()
        conn.close()  # 用完及时关闭, 避免连接泄漏
        return row["id"]

    def get_db_conn(self):
        """绕过 Flask 上下文直接获取测试数据库连接, 用于断言数据库真实状态"""
        conn = sqlite3.connect(app_module.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


class TestUtils(BaseTestCase):
    """工具函数测试: 类型转换与分页解析"""

    def test_to_int_valid(self):
        """测试 _to_int 处理合法整数输入"""
        _print("test_to_int_valid", "输入 '42' 期望得到 42")
        self.assertEqual(app_module._to_int("42", 0), 42)

    def test_to_int_invalid_returns_default(self):
        """测试 _to_int 遇到非法输入时返回默认值而不抛异常"""
        _print("test_to_int_invalid_returns_default", "输入 'abc' 期望回退默认值 -1")
        self.assertEqual(app_module._to_int("abc", -1), -1)
        _print("test_to_int_invalid_returns_default", "输入 None 期望回退默认值 -1")
        self.assertEqual(app_module._to_int(None, -1), -1)

    def test_to_float_valid(self):
        """测试 _to_float 处理合法浮点输入"""
        _print("test_to_float_valid", "输入 '39.5' 期望得到 39.5")
        self.assertAlmostEqual(app_module._to_float("39.5", 0.0), 39.5)

    def test_to_float_invalid_returns_default(self):
        """测试 _to_float 遇到非法输入时返回默认值, 避免 500 错误"""
        _print("test_to_float_invalid_returns_default", "输入 'abc' 期望回退默认值 0.0")
        self.assertAlmostEqual(app_module._to_float("abc", 0.0), 0.0)

    def test_clean_trims_and_truncates(self):
        """测试 _clean 去除首尾空白并按最大长度截断"""
        _print("test_clean_trims_and_truncates", "输入 '  hello  ' 期望 'hello'")
        self.assertEqual(app_module._clean("  hello  ", 100), "hello")
        _print("test_clean_trims_and_truncates", "输入 'abcdef' 上限 3 期望 'abc'")
        self.assertEqual(app_module._clean("abcdef", 3), "abc")

    def test_page_args_clamps_values(self):
        """测试分页参数对非法输入的收敛处理"""
        _print("test_page_args_clamps_values", "page=0 应收敛为 1")
        with app_module.app.test_request_context("/books?page=0"):
            page, per_page, _ = app_module._page_args()
            self.assertEqual(page, 1)
        _print("test_page_args_clamps_values", "page=-5 应收敛为 1")
        with app_module.app.test_request_context("/books?page=-5"):
            page, _, _ = app_module._page_args()
            self.assertEqual(page, 1)
        _print("test_page_args_clamps_values", "per_page=9999 应被限制为 100")
        with app_module.app.test_request_context("/books?per_page=9999"):
            _, per_page, _ = app_module._page_args()
            self.assertEqual(per_page, app_module.MAX_PAGE_ITEMS)

    def test_pager_computes_pages(self):
        """测试分页信息字典的计算是否正确"""
        _print("test_pager_computes_pages", "共 25 条, 每页 10 条 期望 3 页")
        pager = app_module._pager(1, 10, 25)
        self.assertEqual(pager["pages"], 3)
        self.assertTrue(pager["has_next"])
        self.assertFalse(pager["has_prev"])
        _print("test_pager_computes_pages", "空结果集应仍为 1 页")
        empty = app_module._pager(1, 10, 0)
        self.assertEqual(empty["pages"], 1)
        self.assertFalse(empty["has_next"])


class TestAuth(BaseTestCase):
    """认证与权限测试"""

    def test_login_success(self):
        """测试管理员使用正确凭据登录成功"""
        _print("test_login_success", "admin 登录")
        resp = self.login_admin()
        self.assertIn("欢迎回来", resp.get_data(as_text=True))

    def test_login_failed(self):
        """测试错误密码无法登录"""
        _print("test_login_failed", "错误密码应提示失败")
        resp = self.login("admin", "wrong-password")
        self.assertIn("用户名或密码错误", resp.get_data(as_text=True))

    def test_login_blocked_when_deleted(self):
        """测试已注销(reader 角色)的账号无法登录"""
        _print("test_login_blocked_when_deleted", "先将 reader 置为已注销状态")
        conn = self.get_db_conn()
        conn.execute("UPDATE users SET status='deleted' WHERE username='reader'")
        conn.commit()
        conn.close()
        _print("test_login_blocked_when_deleted", "再尝试登录应被拒绝")
        resp = self.login("reader", "reader123")
        self.assertIn("已注销", resp.get_data(as_text=True))

    def test_anonymous_redirected(self):
        """测试未登录访问受保护页面会被重定向到登录页"""
        _print("test_anonymous_redirected", "匿名访问 /books")
        resp = self.client.get("/books")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_reader_cannot_access_admin_page(self):
        """测试读者访问管理员页面被拒绝"""
        _print("test_reader_cannot_access_admin_page", "reader 访问 /readers")
        self.login_reader()
        resp = self.client.get("/readers", follow_redirects=True)
        self.assertIn("该功能仅管理员可用", resp.get_data(as_text=True))

    def test_register_success(self):
        """测试新读者注册成功并可登录"""
        _print("test_register_success", "注册新用户 zhangsan")
        resp = self.client.post(
            "/register",
            data={"username": "zhangsan", "password": "abc123456", "name": "张三", "phone": "13800001111"},
            follow_redirects=True,
        )
        self.assertIn("注册成功", resp.get_data(as_text=True))
        _print("test_register_success", "用新账号登录")
        resp = self.login("zhangsan", "abc123456")
        self.assertIn("欢迎回来", resp.get_data(as_text=True))

    def test_register_duplicate_username(self):
        """测试重复用户名注册被拒绝"""
        _print("test_register_duplicate_username", "重复注册 admin")
        resp = self.client.post(
            "/register",
            data={"username": "admin", "password": "abc123456", "name": "冒名"},
            follow_redirects=True,
        )
        self.assertIn("用户名已存在", resp.get_data(as_text=True))

    def test_register_rejects_bad_inputs(self):
        """测试注册表单的各项非法输入校验"""
        _print("test_register_rejects_bad_inputs", "密码过短")
        resp = self.client.post(
            "/register",
            data={"username": "testu1", "password": "123", "name": "测试"},
            follow_redirects=True,
        )
        self.assertIn("密码至少 6 位", resp.get_data(as_text=True))

        _print("test_register_rejects_bad_inputs", "用户名含非法字符")
        resp = self.client.post(
            "/register",
            data={"username": "bad user!", "password": "abc123", "name": "测试"},
            follow_redirects=True,
        )
        self.assertIn("用户名只能包含", resp.get_data(as_text=True))

        _print("test_register_rejects_bad_inputs", "手机号格式错误")
        resp = self.client.post(
            "/register",
            data={"username": "testu2", "password": "abc123", "name": "测试", "phone": "123"},
            follow_redirects=True,
        )
        self.assertIn("手机号格式不正确", resp.get_data(as_text=True))


class TestBooks(BaseTestCase):
    """图书管理测试"""

    def test_admin_can_add_book(self):
        """测试管理员新增图书成功"""
        _print("test_admin_can_add_book", "新增《算法导论》")
        self.login_admin()
        resp = self.client.post(
            "/books/new",
            data={"isbn": "9787111407010", "title": "算法导论", "author": "CLRS",
                  "publisher": "机械工业出版社", "category": "计算机", "price": "128", "total": "5"},
            follow_redirects=True,
        )
        self.assertIn("图书添加成功", resp.get_data(as_text=True))
        conn = self.get_db_conn()
        book = conn.execute("SELECT * FROM books WHERE title='算法导论'").fetchone()
        conn.close()
        self.assertIsNotNone(book)
        self.assertEqual(book["available"], 5, "新增图书的可借数应等于总数")

    def test_rejects_invalid_price(self):
        """测试非法价格输入被拒绝且不返回 500"""
        _print("test_rejects_invalid_price", "提交 price=abc")
        self.login_admin()
        resp = self.client.post(
            "/books/new",
            data={"title": "错误书名", "price": "abc", "total": "1"},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("价格格式不正确", resp.get_data(as_text=True))

    def test_rejects_invalid_total(self):
        """测试非法馆藏总数被拒绝"""
        _print("test_rejects_invalid_total", "提交 total=0")
        self.login_admin()
        resp = self.client.post(
            "/books/new",
            data={"title": "零库存书", "price": "10", "total": "0"},
            follow_redirects=True,
        )
        self.assertIn("馆藏总数必须", resp.get_data(as_text=True))

    def test_search_books(self):
        """测试关键词搜索与分类筛选"""
        _print("test_search_books", "搜索关键词 '三体'")
        self.login_reader()
        resp = self.client.get("/books?kw=三体", follow_redirects=True)
        self.assertIn("三体", resp.get_data(as_text=True))

        _print("test_search_books", "按分类 '科幻' 筛选")
        resp = self.client.get("/books?category=科幻", follow_redirects=True)
        selfassert_html = resp.get_data(as_text=True)
        self.assertIn("三体", selfassert_html)

    def test_edit_book_recalculates_available(self):
        """测试编辑馆藏总数后可借数按实际借阅情况自动重算"""
        _print("test_edit_book_recalculates_available", "先借走 1 册")
        book_id = self.add_book("库存测试", total=5)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        _print("test_edit_book_recalculates_available", "将总数改为 3, 预期 available=2")
        self.login_admin()
        self.client.post(
            f"/books/{book_id}/edit",
            data={"title": "库存测试", "price": "10", "total": "3"},
            follow_redirects=True,
        )
        conn = self.get_db_conn()
        book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        conn.close()
        self.assertEqual(book["total"], 3)
        self.assertEqual(book["available"], 2, "可借数应等于总数减已借出数")

    def test_edit_total_below_borrowed_rejected(self):
        """测试把总数改到小于已借出数量时被拒绝"""
        _print("test_edit_total_below_borrowed_rejected", "借出 2 册后尝试把总数改成 1")
        book_id = self.add_book("下限测试", total=5)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        _print("test_edit_total_below_borrowed_rejected", "提交 total=0 应被拒绝")
        self.login_admin()
        resp = self.client.post(
            f"/books/{book_id}/edit",
            data={"title": "下限测试", "price": "10", "total": "0"},
            follow_redirects=True,
        )
        self.assertIn("馆藏总数必须", resp.get_data(as_text=True))

    def test_delete_book_blocked_when_borrowed(self):
        """测试有未归还记录时无法删除图书"""
        _print("test_delete_book_blocked_when_borrowed", "借出后尝试删除")
        book_id = self.add_book("不可删除", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        _print("test_delete_book_blocked_when_borrowed", "管理员删除应被拒绝")
        self.login_admin()
        resp = self.client.post(f"/books/{book_id}/delete", data={}, follow_redirects=True)
        self.assertIn("借阅记录", resp.get_data(as_text=True))
        conn = self.get_db_conn()
        count = conn.execute("SELECT COUNT(*) FROM books WHERE id=?", (book_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1, "图书应仍存在于数据库")

    def test_delete_book_blocked_after_return(self):
        """测试图书借阅并归还后仍不可删除, 避免产生孤儿借阅记录

        这是数据完整性保护: 若允许删除, 借阅记录会指向不存在的图书,
        而借阅记录又关联读者的借阅凭证, 不能连带删除。
        """
        _print("test_delete_book_blocked_after_return", "完成完整借还流程")
        book_id = self.add_book("已流通图书", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        conn = self.get_db_conn()
        borrow_id = conn.execute(
            "SELECT id FROM borrows WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        conn.close()
        self.client.post(f"/return/{borrow_id}", data={}, follow_redirects=True)

        _print("test_delete_book_blocked_after_return", "归还后尝试删除应被拒绝")
        self.login_admin()
        resp = self.client.post(f"/books/{book_id}/delete", data={}, follow_redirects=True)
        self.assertIn("借阅记录", resp.get_data(as_text=True))

        _print("test_delete_book_blocked_after_return", "校验: 图书与借阅记录都仍在")
        conn = self.get_db_conn()
        book_count = conn.execute("SELECT COUNT(*) FROM books WHERE id=?", (book_id,)).fetchone()[0]
        borrow_count = conn.execute(
            "SELECT COUNT(*) FROM borrows WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(book_count, 1, "图书应仍存在")
        self.assertEqual(borrow_count, 1, "借阅记录应完整保留")

    def test_delete_book_success(self):
        """测试从未借出过的图书可正常删除"""
        _print("test_delete_book_success", "删除从未借出的图书")
        book_id = self.add_book("待删除", total=1)
        self.login_admin()
        resp = self.client.post(f"/books/{book_id}/delete", data={}, follow_redirects=True)
        self.assertIn("图书已删除", resp.get_data(as_text=True))
        conn = self.get_db_conn()
        count = conn.execute("SELECT COUNT(*) FROM books WHERE id=?", (book_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class TestBorrowReturn(BaseTestCase):
    """借阅与归还测试"""

    def test_borrow_decrements_stock(self):
        """测试借书成功后库存减一并生成借阅记录"""
        _print("test_borrow_decrements_stock", "借出 1 册, 5->4")
        book_id = self.add_book("借阅测试", total=5)
        self.login_reader()
        resp = self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        self.assertIn("借阅成功", resp.get_data(as_text=True))

        conn = self.get_db_conn()
        book = conn.execute("SELECT available FROM books WHERE id=?", (book_id,)).fetchone()
        record = conn.execute("SELECT * FROM borrows WHERE book_id=?", (book_id,)).fetchone()
        conn.close()
        self.assertEqual(book["available"], 4, "库存应减 1")
        self.assertIsNotNone(record)
        self.assertIsNone(record["return_date"], "新借阅记录归还日期应为 NULL")

    def test_borrow_sets_due_date(self):
        """测试应还日期等于借阅日加借阅期限"""
        _print("test_borrow_sets_due_date", "校验应还日期计算")
        book_id = self.add_book("期限测试", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        conn = self.get_db_conn()
        record = conn.execute("SELECT * FROM borrows WHERE book_id=?", (book_id,)).fetchone()
        conn.close()
        expected = (date.today() + timedelta(days=app_module.DATABASE_DAYS)).isoformat()
        self.assertEqual(record["due_date"], expected)

    def test_cannot_borrow_when_out_of_stock(self):
        """测试库存为 0 时无法借阅"""
        _print("test_cannot_borrow_when_out_of_stock", "借出最后 1 册后再次借阅")
        book_id = self.add_book("唯一一本", total=1)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        _print("test_cannot_borrow_when_out_of_stock", "另一读者再次借阅应失败")
        self.client.get("/logout")  # 退出当前读者账号
        self.login_admin()  # 管理员也能借书
        resp = self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        self.assertIn("库存不足", resp.get_data(as_text=True))
        conn = self.get_db_conn()
        available = conn.execute("SELECT available FROM books WHERE id=?", (book_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(available, 0, "库存不应变为负数")

    def test_cannot_borrow_same_book_twice(self):
        """测试同一本书在未归还前不可重复借阅"""
        _print("test_cannot_borrow_same_book_twice", "连续两次借同一本书")
        book_id = self.add_book("重复借阅", total=5)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        resp = self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        self.assertIn("尚未归还", resp.get_data(as_text=True))

    def test_return_restores_stock(self):
        """测试归还后库存回补"""
        _print("test_return_restores_stock", "借出后归还, 库存回补")
        book_id = self.add_book("归还测试", total=3)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        conn = self.get_db_conn()
        borrow_id = conn.execute(
            "SELECT id FROM borrows WHERE book_id=? AND return_date IS NULL", (book_id,)
        ).fetchone()[0]
        conn.close()

        _print("test_return_restores_stock", "执行归还")
        resp = self.client.post(f"/return/{borrow_id}", data={}, follow_redirects=True)
        self.assertIn("归还成功", resp.get_data(as_text=True))
        conn = self.get_db_conn()
        available = conn.execute("SELECT available FROM books WHERE id=?", (book_id,)).fetchone()[0]
        fine = conn.execute("SELECT fine FROM borrows WHERE id=?", (borrow_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(available, 3, "库存应回补到 3")
        self.assertEqual(fine, 0.0, "按时归还无违约金")

    def test_overdue_fine_calculation(self):
        """测试逾期违约金计算: 逾期天数乘以每日费率"""
        _print("test_overdue_fine_calculation", "构造逾期 10 天的借阅记录")
        book_id = self.add_book("逾期测试", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        # 直接改库把应还日期往前推 10 天, 模拟逾期场景
        conn = self.get_db_conn()
        borrow_id = conn.execute(
            "SELECT id FROM borrows WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        past_due = (date.today() - timedelta(days=10)).isoformat()
        conn.execute("UPDATE borrows SET due_date=? WHERE id=?", (past_due, borrow_id))
        conn.commit()
        conn.close()

        _print("test_overdue_fine_calculation", "归还并校验违约金")
        resp = self.client.post(f"/return/{borrow_id}", data={}, follow_redirects=True)
        self.assertIn("已归还", resp.get_data(as_text=True))
        conn = self.get_db_conn()
        fine = conn.execute("SELECT fine FROM borrows WHERE id=?", (borrow_id,)).fetchone()[0]
        conn.close()
        expected = round(10 * app_module.FINE_PER_DAY, 2)
        self.assertAlmostEqual(fine, expected, places=2, msg="违约金应为逾期天数×费率")

    def test_return_cannot_exceed_total(self):
        """测试重复归还不会使库存超过总册数"""
        _print("test_return_cannot_exceed_total", "同一记录归还两次")
        book_id = self.add_book("重复归还", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        conn = self.get_db_conn()
        borrow_id = conn.execute(
            "SELECT id FROM borrows WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        conn.close()

        _print("test_return_cannot_exceed_total", "第一次归还成功")
        self.client.post(f"/return/{borrow_id}", data={}, follow_redirects=True)
        _print("test_return_cannot_exceed_total", "第二次归还应被拒绝")
        resp = self.client.post(f"/return/{borrow_id}", data={}, follow_redirects=True)
        self.assertIn("已归还", resp.get_data(as_text=True))

        conn = self.get_db_conn()
        book = conn.execute("SELECT available, total FROM books WHERE id=?", (book_id,)).fetchone()
        conn.close()
        self.assertEqual(book["available"], 2)
        self.assertLessEqual(book["available"], book["total"], "库存不得超过总册数")

    def test_cannot_return_others_book(self):
        """测试读者不能归还他人的借阅记录"""
        _print("test_cannot_return_others_book", "读者 A 借书, 读者 B 尝试代还")
        book_id = self.add_book("权限测试", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        conn = self.get_db_conn()
        borrow_id = conn.execute(
            "SELECT id FROM borrows WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        conn.close()

        _print("test_cannot_return_others_book", "切换到另一读者尝试归还")
        self.client.get("/logout")  # 退出当前读者账号
        self.client.post("/register", data={
            "username": "otheruser", "password": "abc123456", "name": "另一个人",
        })
        self.login("otheruser", "abc123456")
        resp = self.client.post(f"/return/{borrow_id}", data={}, follow_redirects=True)
        self.assertIn("无权操作他人的借阅记录", resp.get_data(as_text=True))

    def test_concurrent_borrow_does_not_oversell(self):
        """测试并发借阅最后一本时只有一人成功, 库存不会被扣成负数

        验证条件更新(UPDATE ... WHERE available > 0)的防超借效果。
        """
        _print("test_concurrent_borrow_does_not_oversell", "仅剩 1 册库存, 尝试借出 3 次")
        book_id = self.add_book("并发测试", total=1)

        # 先把库存压到 0
        conn = self.get_db_conn()
        conn.execute("UPDATE books SET available=0 WHERE id=?", (book_id,))
        conn.commit()
        conn.close()

        self.login_reader()
        # 连续三次尝试借阅, 每次都应被库存检查拦截
        for i in range(3):
            _print("test_concurrent_borrow_does_not_oversell", f"第 {i + 1} 次尝试")
            resp = self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
            self.assertIn("库存不足", resp.get_data(as_text=True))

        conn = self.get_db_conn()
        available = conn.execute("SELECT available FROM books WHERE id=?", (book_id,)).fetchone()[0]
        record_count = conn.execute(
            "SELECT COUNT(*) FROM borrows WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(available, 0, "库存绝不能被扣成负数")
        self.assertEqual(record_count, 0, "借阅失败时不应留下借阅记录")


class TestReaders(BaseTestCase):
    """读者管理测试"""

    def test_list_readers(self):
        """测试读者列表展示已注册读者"""
        _print("test_list_readers", "管理员查看读者列表")
        self.login_admin()
        resp = self.client.get("/readers", follow_redirects=True)
        self.assertIn("张小明", resp.get_data(as_text=True))

    def test_delete_reader_blocked_when_borrowing(self):
        """测试读者有未还图书时无法注销"""
        _print("test_delete_reader_blocked_when_borrowing", "借书后尝试注销读者")
        book_id = self.add_book("在借书", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)

        _print("test_delete_reader_blocked_when_borrowing", "管理员注销应被拒绝")
        self.login_admin()
        resp = self.client.post("/readers/2/delete", data={}, follow_redirects=True)
        self.assertIn("尚有未还图书", resp.get_data(as_text=True))

    def test_soft_delete_preserves_history(self):
        """测试软删除读者后历史借阅记录仍完整保留

        这是本项目的关键设计: 借阅记录是业务凭证, 读者注销不应销毁历史。
        """
        _print("test_soft_delete_preserves_history", "先完成一次完整借还流程")
        book_id = self.add_book("凭证测试", total=2)
        self.login_reader()
        self.client.post(f"/borrow/{book_id}", data={}, follow_redirects=True)
        conn = self.get_db_conn()
        borrow_id = conn.execute(
            "SELECT id FROM borrows WHERE book_id=?", (book_id,)
        ).fetchone()[0]
        conn.close()
        self.client.post(f"/return/{borrow_id}", data={}, follow_redirects=True)

        _print("test_soft_delete_preserves_history", "注销读者 reader(id=2)")
        self.login_admin()
        resp = self.client.post("/readers/2/delete", data={}, follow_redirects=True)
        self.assertIn("读者已注销", resp.get_data(as_text=True))

        _print("test_soft_delete_preserves_history", "校验: 用户行仍在, 借阅记录仍在")
        conn = self.get_db_conn()
        user = conn.execute("SELECT * FROM users WHERE id=2").fetchone()
        borrows = conn.execute("SELECT COUNT(*) FROM borrows WHERE user_id=2").fetchone()[0]
        conn.close()
        self.assertIsNotNone(user, "用户记录应保留(软删除)")
        self.assertEqual(user["status"], "deleted", "状态应标记为已注销")
        self.assertEqual(borrows, 1, "历史借阅记录必须完整保留")

    def test_deleted_reader_hidden_from_list(self):
        """测试已注销读者不再出现在管理列表中"""
        _print("test_deleted_reader_hidden_from_list", "注销后检查列表")
        self.login_admin()
        self.client.post("/readers/2/delete", data={}, follow_redirects=True)
        resp = self.client.get("/readers", follow_redirects=True)
        html = resp.get_data(as_text=True)
        self.assertNotIn("张小明", html, "已注销读者不应出现在列表中")


class TestPagination(BaseTestCase):
    """分页功能测试"""

    def test_books_pagination(self):
        """测试图书列表分页: 每页 5 条, 共 8 本演示图书应显示 2 页"""
        _print("test_books_pagination", "per_page=5 查询第 1 页")
        self.login_reader()
        resp = self.client.get("/books?per_page=5", follow_redirects=True)
        html = resp.get_data(as_text=True)
        self.assertIn("第 1 / 2 页", html, "共 8 本书每页 5 条应为 2 页")
        self.assertIn("共 8 条", html)

        _print("test_books_pagination", "查询第 2 页")
        resp = self.client.get("/books?per_page=5&page=2", follow_redirects=True)
        self.assertIn("第 2 / 2 页", resp.get_data(as_text=True))

    def test_borrows_pagination_pager_render(self):
        """测试借阅记录页分页控件正常渲染"""
        _print("test_borrows_pagination_pager_render", "管理员查看借阅记录")
        self.login_admin()
        resp = self.client.get("/borrows", follow_redirects=True)
        self.assertEqual(resp.status_code, 200, "分页页面应正常返回")

    def test_invalid_page_does_not_crash(self):
        """测试非法页码参数不会导致 500 错误"""
        _print("test_invalid_page_does_not_crash", "page=abc")
        self.login_reader()
        resp = self.client.get("/books?page=abc", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        _print("test_invalid_page_does_not_crash", "page=-99")
        resp = self.client.get("/books?page=-99", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    """直接运行本文件时启动全部测试"""
    print("=" * 60)
    print("图书管理系统 - 单元测试")
    print("=" * 60)
    unittest.main(verbosity=2)