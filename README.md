# LAN Cloud Share v13 权限修复版

## 启动

默认使用 SQLite。解压后双击 `start.bat`，或手动运行：

```bat
cd lan_cloud_share_v13_full
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app\main.py
```

默认账号：

```text
admin / admin123
```

## 本版修复

- 修复旧数据库字段不完整导致“添加用户”报错的问题，启动时会自动补齐常用字段。
- 修复权限管理界面：恢复树状主体选择，可展开部门和用户。
- 权限资源类型明确为：空间、文件夹、文件、打印机。
- 选择资源类型后，资源下拉只显示对应资源。
- 新用户默认不能创建团队空间、不能访问团队空间；团队空间只能管理员创建并授权。
- 新用户在没有团队空间权限时，仍可创建自己的本机共享文件夹。
- 普通用户只能管理自己创建的共享文件夹/打印机权限。
- 保留独立权限判断：访问、预览、下载、上传/新建、分享、重命名、移动、删除、管理。

## 数据库

默认 `app/config.json` 使用 SQLite：

```json
{
  "db_backend": "sqlite",
  "db_path": "data/app.db"
}
```

配置里保留 `db_backend` 和 `db_url`，用于后续切换 MySQL/PostgreSQL；本包默认可直接使用 SQLite 启动。
