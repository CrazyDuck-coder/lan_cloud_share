# LAN Cloud Share v7 完整优化版

启动：

```bat
cd lan_cloud_share_v7_ui
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```

默认账号：`admin / admin123`

## 本版修复

- 日志管理：用户筛选改为可弹出的选择控件。
- 版本日志：增加日期、文件名、用户、关键字高级搜索。
- 版本日志：增加“恢复”操作，可把旧版本恢复并覆盖当前文件。
- 修复多处操作快捷菜单显示不完整的问题。
- 上传文件列表排版优化，移除按钮不再挤到一起。
- 我的分享：目标会随“公开链接/所有人/用户/部门/空间”动态切换。
- 我的分享：有效期支持“永久/天数”，权限改为下拉复选框。
- 预览页面增加“在线编辑”入口。
- 系统设置增加 OnlyOffice / Collabora 地址配置项。

## Office 在线预览和编辑说明

浏览器无法直接编辑 docx/xlsx/pptx 文件，必须部署 OnlyOffice Document Server 或 Collabora。
本程序已预留配置入口和编辑页面入口；完整生产对接仍需要配置文档服务、回调地址和签名密钥。
