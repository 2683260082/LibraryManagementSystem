# 图书管理系统（Flask + SQLite）

一个功能完整、可独立运行的图书借阅管理系统，适合作为课程设计项目。

## 快速开始（三步）

```bash
# 1. 安装依赖（只需 Flask，无需数据库服务）
pip install -r requirements.txt

# 2. 启动
python app.py

# 3. 浏览器打开
#    http://127.0.0.1:5000
```

Windows 下也可以直接双击 `start.bat`，脚本会自动检查依赖并启动服务。

首次启动会自动创建 `library.db` 并写入 8 本演示图书和 2 个演示账号，无需手动导入数据。

## 演示账号

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | `admin` | `admin123` |
| 读者 | `reader` | `reader123` |

## 功能清单

**读者功能**
- 注册、登录、退出
- 浏览图书、按书名/作者/ISBN 搜索、按分类筛选
- 借书、还书
- 查看我的借阅记录（含逾期状态与应计违约金）

**管理员功能**
- 读者的全部功能
- 图书的新增、编辑、删除（有未归还记录时禁止删除）
- 读者列表与注销（有未还图书时禁止注销）
- 全馆借阅记录查询，支持按状态（全部/在借/逾期）与书名、读者姓名筛选
- 首页统计：图书种类、馆藏总量、当前借出、注册读者数

## 项目结构

```
library-system-flask/
├── app.py                  # 应用主文件：路由、数据库、业务逻辑
├── library.db              # SQLite 数据库（首次启动自动生成）
├── requirements.txt        # 依赖清单
├── start.bat               # Windows 一键启动脚本
├── README.md               # 本说明文件
├── static/
│   └── style.css           # 全局样式
├── templates/              # Jinja2 页面模板
│   ├── base.html           # 基础布局（导航栏、消息提示、页脚）
│   ├── _pager.html         # 通用分页控件
│   ├── login.html          # 登录
│   ├── register.html       # 注册
│   ├── dashboard.html      # 控制台
│   ├── books.html          # 图书检索
│   ├── book_form.html      # 图书新增/编辑表单
│   ├── my_borrows.html     # 我的借阅
│   ├── readers.html        # 读者管理
│   └── borrows.html        # 全部借阅记录
└── unit_test/
    ├── __init__.py
    └── test_library.py     # 单元测试（39 个用例）
```

## 数据库设计

三张表，外键关联：

| 表名 | 说明 | 关键字段 |
|------|------|----------|
| `users` | 用户 | `role`(admin/reader)、`status`(active/deleted)、`password`(哈希存储) |
| `books` | 图书 | `total`(总册数)、`available`(可借册数) |
| `borrows` | 借阅记录 | `borrow_date`、`due_date`、`return_date`(未还为 NULL)、`fine`(违约金) |

已为 `borrows(user_id, return_date)`、`borrows(book_id, return_date)`、`books(category)` 建索引。

## 设计要点

**1. 并发安全借书**

借书没有采用"先查库存再扣减"的分步写法，而是让数据库在更新时自行判断：

```sql
UPDATE books SET available = available - 1 WHERE id=? AND available > 0
```

只有确实扣减成功（`rowcount == 1`）才写入借阅记录，且整个过程放在事务里。这样即便两个人同时借最后一本，也只有一人成功，不会出现负库存。

**2. 读者注销采用软删除**

注销读者只把 `status` 标记为 `deleted`，不删除数据。这样历史借阅记录完整保留，可追溯。

**3. 逾期判定交给数据库**

逾期天数由 SQL 用 `julianday()` 计算，不在模板里做字符串日期比较，避免日期格式一变判断就错。

**4. 库存由系统反推**

编辑图书时，可借册数 = 馆藏总数 − 当前未归还数量，由系统计算，不允许手工填写，避免与实际借阅情况不一致。若把总数改到小于已借出数量，系统会拒绝并提示。

**5. 统一错误提示**

登录失败时提示"用户名或密码错误，或账号已注销"，不区分"用户不存在"和"密码错误"，避免暴露账号是否存在。

## 业务规则

| 规则 | 值 |
|------|-----|
| 借阅期限 | 60 天 |
| 逾期违约金 | 每天 0.1 元 |
| 单页条数 | 10 条（可通过 `?per_page=` 调整，上限 100） |
| 同一本书重复借阅 | 未归还前不可再借 |

## 运行测试

```bash
# 在 library-system-flask 目录下执行
python -m unittest discover -s unit_test -t . -v
```

测试使用独立的临时数据库，不会影响 `library.db`。共 39 个用例，覆盖工具函数、认证与权限、图书管理、借阅归还（含并发超借防护）、读者软删除、分页。

## 常见问题

**Q：端口 5000 被占用？**
修改 `app.py` 最后一行 `app.run()` 的 `port=5000` 为其他端口，如 `port=5001`。

**Q：想重置演示数据？**
删除 `library.db` 后重启，服务会自动重建并写入演示数据。

**Q：忘记密码？**
同上删除数据库重置，或用管理员账号登录后直接在数据库中修改。

**Q：页面样式不生效？**
确认浏览器强制刷新（`Ctrl + F5`），避免旧 CSS 缓存。

## 说明

本项目定位为教学演示系统，不用于生产部署。会话密钥（`secret_key`）使用固定值，以保证重启后登录状态不失效；若要部署到真实环境，需改为从环境变量读取随机密钥，并引入 CSRF 防护、HTTPS 等措施。