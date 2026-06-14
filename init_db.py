import sqlite3, hashlib, hmac, secrets
from pathlib import Path
from datetime import datetime
import json

ROOT = Path(__file__).resolve().parent
DB = ROOT / "app" / "data" / "app.db"
STORE = ROOT / "app" / "data" / "storage"
DB.parent.mkdir(parents=True, exist_ok=True)
STORE.mkdir(parents=True, exist_ok=True)

def now():
    return datetime.now().isoformat(sep=" ", timespec="seconds")

def hp(p):
    s = secrets.token_hex(16)
    d = hashlib.pbkdf2_hmac("sha256", p.encode(), s.encode(), 120000).hex()
    return f"p${s}${d}"

theme = {
    "accent":"#1677ff",
    "logo":"局域网共享云",
    "font_size":"16",
    "font_family":"Microsoft YaHei",
    "menus":{
        "files":"共享文件/文件夹","printers":"共享打印机","users":"用户管理","departments":"部门架构",
        "permissions":"权限管理","spaces":"团队空间","shares":"我的分享","logs":"日志管理",
        "theme":"图标配置","versions":"版本日志","backup":"数据备份","settings":"系统设置"
    }
}

con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
create table if not exists departments(id integer primary key autoincrement,name text unique,parent_id integer);
create table if not exists users(id integer primary key autoincrement,username text unique,display_name text,password text,role text,department_id integer,active integer default 1,created_at text);
create table if not exists spaces(id integer primary key autoincrement,name text unique,kind text,root_path text,owner_id integer,note text,created_at text);
create table if not exists permissions(id integer primary key autoincrement,subject_type text,subject_id integer,resource_type text,resource_id integer,rel_path text,effect text,can_read integer,can_write integer,can_admin integer,can_preview integer default 1,can_download integer default 1,can_upload integer default 0,can_share integer default 0,can_rename integer default 0,can_move integer default 0,can_delete integer default 0);
create table if not exists file_versions(id integer primary key autoincrement,space_id integer,rel_path text,version_no integer,stored_path text,user_id integer,action text,created_at text);
create table if not exists printers(id integer primary key autoincrement,name text unique,address text,share_name text,driver text,note text,active integer default 1,owner_id integer);
create table if not exists share_links(id integer primary key autoincrement,token text unique,space_id integer,rel_path text,owner_id integer,target_type text,target_id integer,can_download integer,can_upload integer,can_write integer,created_at text,expires_at text);
create table if not exists settings(k text primary key,v text);
create table if not exists logs(id integer primary key autoincrement,user_id integer,username text,action text,detail text,ip text,created_at text);
""")

if cur.execute("select count(*) from users").fetchone()[0] == 0:
    cur.execute("insert into departments(name,parent_id) values(?,null)", ("默认部门",))
    dep = cur.lastrowid
    cur.execute("insert into users(username,display_name,password,role,department_id,active,created_at) values(?,?,?,?,?,?,?)",
                ("admin","管理员",hp("admin123"),"admin",dep,1,now()))
    (STORE / "团队空间").mkdir(parents=True, exist_ok=True)
    cur.execute("insert into spaces(name,kind,root_path,owner_id,note,created_at) values(?,?,?,?,?,?)",
                ("团队空间","team",str(STORE / "团队空间"),1,"默认团队空间，仅管理员默认拥有权限。",now()))
    sid = cur.lastrowid
    cur.execute("""insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin,can_preview,can_download,can_upload,can_share,can_rename,can_move,can_delete)
                   values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("user",1,"space",sid,"","allow",1,1,1,1,1,1,1,1,1,1))
    cur.execute("insert or replace into settings(k,v) values(?,?)", ("theme", json.dumps(theme, ensure_ascii=False)))

con.commit()
con.close()
print("SQLite 初始化完成：", DB)
print("默认账号：admin / admin123")
