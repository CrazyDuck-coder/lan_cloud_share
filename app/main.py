
import os, sqlite3, json, hashlib, hmac, secrets, base64, shutil, zipfile, subprocess, socket, sys, string
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT=Path(__file__).resolve().parent
CFG_FILE=ROOT/'config.json'
DEFAULT={'port':8000,'db_backend':'sqlite','db_path':'data/app.db','db_url':'','storage_root':'data/storage','secret_key':'change-me','restart_command':'','onlyoffice_url':'','collabora_url':''}
def load_cfg():
    if not CFG_FILE.exists(): CFG_FILE.write_text(json.dumps(DEFAULT,ensure_ascii=False,indent=2),encoding='utf-8')
    c=DEFAULT.copy();
    try:c.update(json.loads(CFG_FILE.read_text('utf-8')))
    except Exception: pass
    return c
CFG=load_cfg()
def rp(p):
    q=Path(str(p)); return q if q.is_absolute() else ROOT/q
DATA=ROOT/'data'; STORE=rp(CFG['storage_root']); BACK=DATA/'backups'; PREV=DATA/'previews'; VERS=DATA/'versions'; DB=rp(CFG['db_path'])
for d in [DATA,STORE,BACK,PREV,VERS,DB.parent]: d.mkdir(parents=True,exist_ok=True)
APP='局域网共享云'; SECRET=CFG['secret_key']
app=FastAPI(title=APP); app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static'); tpl=Jinja2Templates(str(ROOT/'templates'))

def con():
    c=sqlite3.connect(DB, timeout=30, isolation_level=None)
    c.row_factory=sqlite3.Row
    try:
        c.execute('pragma journal_mode=WAL')
        c.execute('pragma busy_timeout=30000')
        c.execute('pragma foreign_keys=on')
    except Exception:
        pass
    return c

def init():
    c=con(); cur=c.cursor()
    cur.executescript('''
    create table if not exists departments(id integer primary key autoincrement,name text unique,parent_id integer);
    create table if not exists users(id integer primary key autoincrement,username text unique,display_name text,password text,role text,department_id integer,active integer default 1,created_at text);
    create table if not exists spaces(id integer primary key autoincrement,name text unique,kind text,root_path text,owner_id integer,note text,created_at text);
    create table if not exists permissions(id integer primary key autoincrement,subject_type text,subject_id integer,resource_type text,resource_id integer,rel_path text,effect text,can_read integer,can_write integer,can_admin integer,can_preview integer default 1,can_download integer default 1,can_upload integer default 0,can_share integer default 0,can_rename integer default 0,can_move integer default 0,can_delete integer default 0);
    create table if not exists file_versions(id integer primary key autoincrement,space_id integer,rel_path text,version_no integer,stored_path text,user_id integer,action text,created_at text);
    create table if not exists printers(id integer primary key autoincrement,name text unique,address text,share_name text,driver text,note text,active integer default 1,owner_id integer);
    create table if not exists share_links(id integer primary key autoincrement,token text unique,space_id integer,rel_path text,owner_id integer,target_type text,target_id integer,can_download integer,can_upload integer,can_write integer,created_at text,expires_at text);
    create table if not exists settings(k text primary key,v text);
    create table if not exists logs(id integer primary key autoincrement,user_id integer,username text,action text,detail text,ip text,created_at text);
    ''')

    # 兼容旧版本数据库：旧库如果缺少字段，自动补齐，避免添加用户/空间时报错。
    def addcol(table, col, ddl):
        try:
            cur.execute(f'alter table {table} add column {col} {ddl}')
        except Exception:
            pass
    addcol('users','display_name','text')
    addcol('users','role',"text default 'user'")
    addcol('users','department_id','integer')
    addcol('users','active','integer default 1')
    addcol('users','created_at','text')
    addcol('spaces','kind',"text default 'shared'")
    addcol('spaces','root_path','text')
    addcol('spaces','owner_id','integer')
    addcol('spaces','note','text')
    addcol('spaces','created_at','text')
    addcol('spaces','host_name','text')
    addcol('spaces','host_ip','text')
    try:
        cur.execute('update spaces set host_name=? where host_name is null or host_name=""',(socket.gethostname(),))
        cur.execute('update spaces set host_ip=? where host_ip is null or host_ip=""',(local_ip(),))
    except Exception:
        pass
    addcol('permissions','subject_id','integer')
    addcol('permissions','rel_path','text')
    addcol('permissions','effect',"text default 'allow'")
    addcol('permissions','can_read','integer default 1')
    addcol('permissions','can_write','integer default 0')
    addcol('permissions','can_admin','integer default 0')
    addcol('permissions','creator_id','integer')
    addcol('printers','address','text')
    addcol('printers','share_name','text')
    addcol('printers','driver','text')
    addcol('printers','note','text')
    addcol('printers','active','integer default 1')
    addcol('printers','owner_id','integer')

    for col, default in [('can_preview','1'),('can_download','1'),('can_upload','0'),('can_share','0'),('can_rename','0'),('can_move','0'),('can_delete','0')]:
        try:
            cur.execute(f'alter table permissions add column {col} integer default {default}')
        except Exception:
            pass
    try:
        cur.execute('alter table printers add column owner_id integer')
    except Exception:
        pass
    try:
        # 旧版本可能把共享文件夹(kind!=team)也登记成 resource_type=space，
        # 导致权限资源下拉里共享文件夹混到“空间”里。这里统一迁移为 folder；
        # 团队空间仍保持 space。has() 会同时读取 space/folder/file 权限，不影响访问。
        cur.execute('update permissions set resource_type="folder" where resource_type="space" and resource_id in (select id from spaces where kind<>"team")')
    except Exception:
        pass
    if cur.execute('select count(*) from users').fetchone()[0]==0:
        cur.execute('insert into departments(name,parent_id) values(?,null)',('默认部门',)); dep=cur.lastrowid
        cur.execute('insert into users(username,display_name,password,role,department_id,active,created_at) values(?,?,?,?,?,?,?)',('admin','管理员',hp('admin123'),'admin',dep,1,now()))
        (STORE/'团队空间').mkdir(parents=True,exist_ok=True)
        cur.execute('insert into spaces(name,kind,root_path,owner_id,note,created_at,host_name,host_ip) values(?,?,?,?,?,?,?,?)',('团队空间','team',str(STORE/'团队空间'),1,'团队空间是多人公共空间；共享文件夹是登记本机已有目录并授权访问。',now(),socket.gethostname(),local_ip()))
        sid=cur.lastrowid
        cur.execute('insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin,can_preview,can_download,can_upload,can_share,can_rename,can_move,can_delete,creator_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('user',1,'space',sid,'','allow',1,1,1,1,1,1,1,1,1,1,1))
        cur.execute('insert into settings(k,v) values(?,?)',('theme',json.dumps(theme_default(),ensure_ascii=False)))
    c.commit(); c.close()

def now(): return datetime.now().isoformat(sep=' ',timespec='seconds')
def local_ip():
    try:
        sock=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.connect(('8.8.8.8',80)); ip=sock.getsockname()[0]; sock.close(); return ip
    except Exception:
        try: return socket.gethostbyname(socket.gethostname())
        except Exception: return '127.0.0.1'


def client_ip(req):
    try:
        return req.client.host or local_ip()
    except Exception:
        return local_ip()

def client_host(req):
    ip=client_ip(req)
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ip
def hp(p):
    s=secrets.token_hex(16); d=hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),120000).hex(); return f'p${s}${d}'
def vp(p,h):
    try:_,s,d=h.split('$'); return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),120000).hex(),d)
    except Exception:return False
def tok(uid):
    exp=int((datetime.now()+timedelta(days=7)).timestamp()); payload=f'{uid}:{exp}'; sig=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest(); return base64.urlsafe_b64encode(f'{payload}:{sig}'.encode()).decode()
def uid_from(t):
    try:
        uid,exp,sig=base64.urlsafe_b64decode(t.encode()).decode().split(':',2); payload=f'{uid}:{exp}'; good=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
        return int(uid) if hmac.compare_digest(sig,good) and int(exp)>int(datetime.now().timestamp()) else None
    except Exception:return None
def safe(p):
    p=(p or '').replace('\\','/').strip('/'); q=Path(p)
    if '..' in q.parts or q.is_absolute(): raise HTTPException(400,'非法路径')
    return p
def user(req):
    uid=uid_from(req.cookies.get('token',''))
    if not uid: raise HTTPException(401)
    c=con(); u=c.execute('select * from users where id=? and active=1',(uid,)).fetchone(); c.close()
    if not u: raise HTTPException(401)
    return u
def admin(req):
    u=user(req)
    if u['role']!='admin': raise HTTPException(403)
    return u
def log(req,u,act,detail):
    c=con(); c.execute('insert into logs(user_id,username,action,detail,ip,created_at) values(?,?,?,?,?,?)',(u['id'] if u else None,u['username'] if u else '',act,detail,req.client.host if req.client else '',now())); c.commit(); c.close()
def theme_default():
    return {'accent':'#1677ff','logo':'局域网共享云','font_size':'16','font_family':'Microsoft YaHei','menus':{'files':'共享文件/文件夹','printers':'共享打印机','users':'用户管理','departments':'部门架构','permissions':'权限管理','spaces':'团队空间','shares':'我的分享','logs':'日志管理','theme':'图标配置','versions':'版本日志','backup':'数据备份','settings':'系统设置'}}
def theme(c):
    t=theme_default(); r=c.execute('select v from settings where k="theme"').fetchone()
    if r:
        try:
            x=json.loads(r['v']); t.update(x); t['menus']={**theme_default()['menus'],**x.get('menus',{})}
        except Exception: pass
    return t
def redirect_back(req, fallback='/files'):
    return RedirectResponse(req.headers.get('referer') or fallback,302)

def render(req,name,ctx):
    c=con(); ctx.update(request=req,theme=theme(c),config=CFG,user=ctx.get('user') or user(req)); c.close(); return tpl.TemplateResponse(req,name,ctx)
def rowlist(sql,args=()):
    c=con(); rows=c.execute(sql,args).fetchall(); c.close(); return rows
def one(sql,args=()):
    c=con(); r=c.execute(sql,args).fetchone(); c.close(); return r

def path_ok(perm_path,target):
    pp=safe(perm_path); tt=safe(target); return not pp or tt==pp or tt.startswith(pp.rstrip('/')+'/')
def perm_value(p, op):
    # Fine-grained permission. can_write is kept for compatibility, but it does not imply share/delete/move/rename/upload.
    m={'read':'can_read','preview':'can_preview','download':'can_download','upload':'can_upload','share':'can_share','rename':'can_rename','move':'can_move','delete':'can_delete','write':'can_write','admin':'can_admin'}
    col=m.get(op,'can_read')
    if op=='admin':
        return p['can_admin']
    if op=='write':
        return p['can_write'] or p['can_admin']
    if op=='read':
        return p['can_read'] or p['can_admin']
    return p[col] or p['can_admin']

def has(u,typ,rid,rel='',write=False,adm=False,op='read'):
    if u['role']=='admin': return True
    if adm: op='admin'
    elif write: op='write'
    # Folder/file permissions are stored as resource_type folder/file with the parent space id and rel_path.
    if typ=='space':
        rows=rowlist('select * from permissions where resource_id=? and resource_type in ("space","folder","file")',(rid,))
    else:
        rows=rowlist('select * from permissions where resource_type=? and resource_id=?',(typ,rid))
    rows=[p for p in rows if (typ!='space' or path_ok(p['rel_path'] or '',rel)) and (p['subject_type']=='all' or (p['subject_type']=='user' and p['subject_id']==u['id']) or (p['subject_type']=='department' and p['subject_id']==u['department_id']))]
    if any(p['effect']=='deny' and perm_value(p,op) for p in rows): return False
    return any(p['effect']=='allow' and perm_value(p,op) for p in rows)

def space_admin_or_owner(u, space_id):
    if u['role']=='admin':
        return True
    s=one('select * from spaces where id=?',(space_id,))
    # 团队空间只能管理员管理；普通用户即使有访问权限，也不能管理团队空间或其权限。
    if not s or s['kind']=='team':
        return False
    return bool(s['owner_id']==u['id'] or has(u,'space',space_id,op='admin'))

def subject_match_perm(u, p):
    return (p['subject_type']=='all' or
            (p['subject_type']=='user' and p['subject_id']==u['id']) or
            (p['subject_type']=='department' and p['subject_id']==u['department_id']))

def spaces_for(u):
    if u['role']=='admin':
        return rowlist('select * from spaces order by id')
    owned=[s for s in rowlist('select * from spaces where owner_id=? order by id',(u['id'],))]
    allowed=[s for s in rowlist('select * from spaces order by id') if has(u,'space',s['id'])]
    # 如果用户只被授权了某个共享文件夹/子文件夹，根空间本身也要出现在空间列表中，
    # 进入后再按 rel_path 过滤可见目录。
    pspaces=[]
    for p in rowlist('select distinct resource_id from permissions where resource_type in ("space","folder","file")'):
        sp=one('select * from spaces where id=?',(p['resource_id'],))
        if not sp:
            continue
        perms=rowlist('select * from permissions where resource_id=? and resource_type in ("space","folder","file")',(sp['id'],))
        if any(subject_match_perm(u, x) and x['effect']=='allow' and (x['can_read'] or x['can_admin']) for x in perms):
            pspaces.append(sp)
    ids=set(); out=[]
    for s in owned+allowed+pspaces:
        if s['id'] not in ids:
            ids.add(s['id']); out.append(s)
    return out

def can_manage_resource(u, resource_type, resource_id, rel_path=''):
    if u['role']=='admin':
        return True
    if resource_type in ('space','folder','file'):
        return space_admin_or_owner(u, resource_id)
    if resource_type=='printer':
        p=one('select * from printers where id=?',(resource_id,))
        return bool(p and p['owner_id']==u['id'])
    return False

def build_resource_items(u):
    items=[]
    # 权限资源下拉必须区分“团队空间”和“共享文件/文件夹”：
    # 1) 团队空间(kind=team)只出现在“空间”；
    # 2) 共享文件夹(kind!=team)只出现在“文件夹”，避免两个功能混在一起；
    # 3) 空间/共享目录下的子目录仍作为“文件夹”，具体文件作为“文件”。
    spaces = rowlist('select * from spaces') if u['role']=='admin' else [s for s in rowlist('select * from spaces') if space_admin_or_owner(u,s['id'])]
    for s in spaces:
        root=Path(s['root_path'])
        if s['kind']=='team':
            items.append({'key':f'space|{s["id"]}|','label':f'空间：{s["name"]}','kind':'space'})
        else:
            items.append({'key':f'folder|{s["id"]}|','label':f'文件夹：{s["name"]}','kind':'folder'})
        if root.exists():
            for p in root.rglob('*'):
                try:
                    rel=str(p.relative_to(root)).replace('\\','/')
                except Exception:
                    continue
                if p.is_dir():
                    items.append({'key':f'folder|{s["id"]}|{rel}','label':f'文件夹：{s["name"]}/{rel}','kind':'folder'})
                elif p.is_file():
                    items.append({'key':f'file|{s["id"]}|{rel}','label':f'文件：{s["name"]}/{rel}','kind':'file'})
    printers = rowlist('select * from printers') if u['role']=='admin' else rowlist('select * from printers where owner_id=?',(u['id'],))
    for p in printers:
        items.append({'key':f'printer|{p["id"]}|','label':f'打印机：{p["name"]}','kind':'printer'})
    return items

def parse_resource_key(resource_key, resource_type, resource_id, rel_path):
    if resource_key:
        parts=resource_key.split('|',2)
        if len(parts)>=2:
            rt=parts[0]; rid=int(parts[1]); rel=parts[2] if len(parts)>2 else ''
            return rt,rid,safe(rel)
    return resource_type,int(resource_id),safe(rel_path)
def recver(c,sid,rel,path,u,act):
    p=Path(path)
    if not p.exists() or not p.is_file(): return
    n=c.execute('select count(*) from file_versions where space_id=? and rel_path=?',(sid,rel)).fetchone()[0]+1
    dst=VERS/str(sid)/(secrets.token_hex(8)+'_'+p.name); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,dst)
    c.execute('insert into file_versions(space_id,rel_path,version_no,stored_path,user_id,action,created_at) values(?,?,?,?,?,?,?)',(sid,rel,n,str(dst),u['id'],act,now()))
init()

@app.exception_handler(401)
async def unauth(req,exc): return RedirectResponse('/login')
@app.get('/login',response_class=HTMLResponse)
def login_page(req:Request): return tpl.TemplateResponse(req,'login.html',{'request':req,'app':APP})
@app.post('/login')
def login(req:Request, username:str=Form(...), password:str=Form(...)):
    u=one('select * from users where username=?',(username,))
    if not u or not vp(password,u['password']): return tpl.TemplateResponse(req,'login.html',{'request':req,'app':APP,'error':'用户名或密码错误'})
    r=RedirectResponse('/',302); r.set_cookie('token',tok(u['id']),httponly=True,samesite='lax'); log(req,u,'login','登录'); return r
@app.get('/logout')
def logout(): r=RedirectResponse('/login'); r.delete_cookie('token'); return r
@app.get('/')
def index(req:Request): user(req); return RedirectResponse('/files')
@app.post('/profile')
def profile(req:Request, display_name:str=Form(''), old_password:str=Form(''), new_password:str=Form('')):
    u=user(req); c=con(); dbu=c.execute('select * from users where id=?',(u['id'],)).fetchone();
    if new_password:
        if not vp(old_password,dbu['password']): raise HTTPException(400,'旧密码错误')
        c.execute('update users set display_name=?,password=? where id=?',(display_name,hp(new_password),u['id']))
    else: c.execute('update users set display_name=? where id=?',(display_name,u['id']))
    c.commit(); c.close(); log(req,u,'profile','修改个人资料'); return RedirectResponse('/files',302)

@app.get('/files',response_class=HTMLResponse)
def files(req:Request, space_id:int=0, path:str=''):
    u=user(req)
    ss=[s for s in spaces_for(u) if s['kind']!='team']
    sp=(one('select * from spaces where id=? and kind<>"team"',(space_id,)) if space_id and space_id>0 else None)
    if sp and not any(x['id']==sp['id'] for x in ss):
        raise HTTPException(403)
    rel=safe(path); items=[]
    if sp:
        cur=Path(sp['root_path'])/rel
        # 共享文件夹登记的是共享端设备上的路径。服务端只有在该路径对当前服务可访问
        # （例如网络共享/映射盘/同机部署）时才列出内容；这里不再自动在服务端创建同名目录，
        # 避免把客户端共享误变成服务端目录。
        if cur.exists() and cur.is_dir():
            for p in sorted(cur.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower())):
                rr=(str(Path(rel)/p.name) if rel else p.name).replace('\\','/')
                if has(u,'space',sp['id'],rr):
                    st=p.stat(); items.append({'name':p.name,'is_dir':p.is_dir(),'size':st.st_size,'mtime':datetime.fromtimestamp(st.st_mtime),'rel':rr,
                        'can_preview':has(u,'space',sp['id'],rr,op='preview'),'can_download':has(u,'space',sp['id'],rr,op='download'),
                        'can_upload':has(u,'space',sp['id'],rr,op='upload'),'can_share':has(u,'space',sp['id'],rr,op='share'),
                        'can_rename':has(u,'space',sp['id'],rr,op='rename'),'can_move':has(u,'space',sp['id'],rr,op='move'),
                        'can_delete':has(u,'space',sp['id'],rr,op='delete'),'can_admin':has(u,'space',sp['id'],rr,op='admin')})
    return render(req,'files.html',{'mode':'shared','spaces':ss,'space':sp,'items':items,'path':rel,'can_upload_current':has(u,'space',sp['id'],rel,op='upload') if sp else False,'all_users':rowlist('select * from users order by username'),'all_deps':rowlist('select * from departments order by name'),'all_spaces':rowlist('select * from spaces order by name')})
@app.get('/team_files',response_class=HTMLResponse)
def team_files(req:Request, space_id:int=0, path:str=''):
    u=user(req)
    ss=[s for s in spaces_for(u) if s['kind']=='team']
    sp=(one('select * from spaces where id=? and kind="team"',(space_id,)) if space_id and space_id>0 else None)
    if sp and not any(x['id']==sp['id'] for x in ss):
        raise HTTPException(403)
    rel=safe(path); items=[]
    if sp:
        cur=Path(sp['root_path'])/rel; cur.mkdir(parents=True,exist_ok=True)
        shared_roots={str(Path(x['root_path']).resolve()).lower() for x in rowlist('select root_path from spaces where kind<>"team"') if x['root_path']}
        shared_names={x['name'] for x in rowlist('select name from spaces where kind<>"team"')}
        for p in sorted(cur.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower())):
            # 团队空间与共享文件夹是两个独立功能。团队空间根目录下如果残留了已登记的共享文件夹，不在团队空间中显示。
            if not rel and p.is_dir() and (p.name in shared_names or str(p.resolve()).lower() in shared_roots):
                continue
            rr=(str(Path(rel)/p.name) if rel else p.name).replace('\\','/')
            if has(u,'space',sp['id'],rr):
                st=p.stat(); items.append({'name':p.name,'is_dir':p.is_dir(),'size':st.st_size,'mtime':datetime.fromtimestamp(st.st_mtime),'rel':rr,
                    'can_preview':has(u,'space',sp['id'],rr,op='preview'),'can_download':has(u,'space',sp['id'],rr,op='download'),
                    'can_upload':has(u,'space',sp['id'],rr,op='upload'),'can_share':has(u,'space',sp['id'],rr,op='share'),
                    'can_rename':has(u,'space',sp['id'],rr,op='rename'),'can_move':has(u,'space',sp['id'],rr,op='move'),
                    'can_delete':has(u,'space',sp['id'],rr,op='delete'),'can_admin':has(u,'space',sp['id'],rr,op='admin')})
    return render(req,'files.html',{'mode':'team','spaces':ss,'space':sp,'items':items,'path':rel,'can_upload_current':has(u,'space',sp['id'],rel,op='upload') if sp else False,'all_users':rowlist('select * from users order by username'),'all_deps':rowlist('select * from departments order by name'),'all_spaces':rowlist('select * from spaces order by name')})

@app.post('/files/mkdir')
def mkdir(req:Request, space_id:int=Form(...), path:str=Form(''), name:str=Form(...), local_path:str=Form('')):
    u=user(req)
    # 如果填写了本机路径，则登记为新的共享文件夹；否则在当前空间中创建普通子文件夹。
    if local_path.strip():
        root=Path(local_path.strip())
        c=con()
        c.execute('insert into spaces(name,kind,root_path,owner_id,note,created_at,host_name,host_ip) values(?,?,?,?,?,?,?,?)',(name,'shared',str(root),u['id'],'由用户登记的本机共享路径；建议填写网络共享路径，服务端可访问时即可浏览。',now(),client_host(req),client_ip(req)))
        sid=c.execute('select last_insert_rowid()').fetchone()[0]
        c.execute('insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin,can_preview,can_download,can_upload,can_share,can_rename,can_move,can_delete,creator_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('user',u['id'],'space',sid,'','allow',1,1,1,1,1,1,1,1,1,1,u['id']))
        c.execute('insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin,can_preview,can_download,can_upload,can_share,can_rename,can_move,can_delete,creator_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('all',None,'folder',sid,'','allow',1,0,0,1,1,0,0,0,0,0,u['id']))
        c.commit(); c.close()
        log(req,u,'add_shared_folder',f'{name}:{root}')
        return RedirectResponse(f'/files?space_id={sid}',302)
    sp=one('select * from spaces where id=?',(space_id,))
    rel=(str(Path(safe(path))/safe(name)) if safe(path) else safe(name)).replace('\\','/')
    if not has(u,'space',space_id,path,op='upload'):
        raise HTTPException(403)
    (Path(sp['root_path'])/rel).mkdir(parents=True,exist_ok=True)
    log(req,u,'mkdir',f'{sp["name"]}/{rel}')
    return redirect_back(req, f'/files?space_id={space_id}&path={safe(path)}')
@app.post('/files/upload')
def upload(req:Request, space_id:int=Form(...), path:str=Form(''), files:list[UploadFile]=File(...)):
    u=user(req); sp=one('select * from spaces where id=?',(space_id,)); relp=safe(path)
    if not has(u,'space',space_id,relp,op='upload'): raise HTTPException(403)
    folder=Path(sp['root_path'])/relp; folder.mkdir(parents=True,exist_ok=True)
    details=[]
    c=con()
    try:
        c.execute('begin immediate')
        for f in files:
            name=Path(f.filename).name
            if not name:
                continue
            rel=(str(Path(relp)/name) if relp else name).replace('\\','/')
            target=folder/name
            if target.exists():
                recver(c,space_id,rel,target,u,'覆盖前归档')
            with open(target,'wb') as out:
                shutil.copyfileobj(f.file,out)
            recver(c,space_id,rel,target,u,'上传')
            details.append(f'{sp["name"]}/{rel}')
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    for d in details:
        log(req,u,'upload',d)
    return redirect_back(req, f'/files?space_id={space_id}&path={relp}')
@app.post('/files/rename')
def rename(req:Request, space_id:int=Form(...), rel_path:str=Form(...), new_name:str=Form(...)):
    u=user(req); sp=one('select * from spaces where id=?',(space_id,)); rel=safe(rel_path)
    if not has(u,'space',space_id,rel,op='rename'): raise HTTPException(403)
    src=Path(sp['root_path'])/rel; dst=src.parent/Path(new_name).name; src.rename(dst); log(req,u,'rename',f'{rel}->{dst.name}'); par=str(Path(rel).parent); par='' if par=='.' else par; return redirect_back(req, f'/files?space_id={space_id}&path={par}')
@app.post('/files/move')
def move(req:Request, space_id:int=Form(...), rel_path:str=Form(...), dest_path:str=Form('')):
    u=user(req); sp=one('select * from spaces where id=?',(space_id,)); rel=safe(rel_path); dest=safe(dest_path)
    if not has(u,'space',space_id,rel,op='move'): raise HTTPException(403)
    dst=Path(sp['root_path'])/dest; dst.mkdir(parents=True,exist_ok=True); shutil.move(str(Path(sp['root_path'])/rel),str(dst/Path(rel).name)); log(req,u,'move',f'{rel}->{dest}'); return redirect_back(req, f'/files?space_id={space_id}&path={dest}')
@app.post('/delete')
def delete(req:Request, space_id:int=Form(...), rel_path:str=Form(...)):
    u=user(req); sp=one('select * from spaces where id=?',(space_id,)); rel=safe(rel_path)
    if not has(u,'space',space_id,rel,op='delete'): raise HTTPException(403)
    p=Path(sp['root_path'])/rel; c=con(); recver(c,space_id,rel,p,u,'删除前归档')
    if p.is_dir(): shutil.rmtree(p)
    elif p.exists(): p.unlink()
    c.commit(); c.close(); log(req,u,'delete',f'{sp["name"]}/{rel}'); par=str(Path(rel).parent); par='' if par=='.' else par; return redirect_back(req, f'/files?space_id={space_id}&path={par}')
@app.get('/download/{space_id}/{rel_path:path}')
def download(req:Request, space_id:int, rel_path:str):
    u=user(req); sp=one('select * from spaces where id=?',(space_id,)); rel=safe(rel_path)
    if not has(u,'space',space_id,rel,op='download'): raise HTTPException(403)
    p=Path(sp['root_path'])/rel
    if not p.exists() or p.is_dir(): raise HTTPException(404)
    return FileResponse(str(p),filename=p.name)
@app.get('/preview/{space_id}/{rel_path:path}',response_class=HTMLResponse)
def preview(req:Request,space_id:int,rel_path:str):
    u=user(req); sp=one('select * from spaces where id=?',(space_id,)); rel=safe(rel_path)
    if not has(u,'space',space_id,rel,op='preview'): raise HTTPException(403)
    p=Path(sp['root_path'])/rel; ext=p.suffix.lower(); preview_url=None; text=None; zip_items=None
    if ext in ['.png','.jpg','.jpeg','.gif','.webp','.svg','.pdf']: preview_url=f'/download/{space_id}/{rel}'
    elif ext in ['.txt','.md','.py','.js','.css','.html','.json','.log','.csv']: text=p.read_text('utf-8',errors='ignore')[:200000]
    elif ext=='.zip':
        with zipfile.ZipFile(p) as z: zip_items=[{'name':i.filename,'size':i.file_size,'is_dir':i.is_dir()} for i in z.infolist()]
    elif ext in ['.doc','.docx','.xls','.xlsx','.ppt','.pptx']:
        out=PREV/str(space_id); out.mkdir(parents=True,exist_ok=True); pdf=out/(p.stem+'.pdf')
        if not pdf.exists():
            try: subprocess.run(['soffice','--headless','--convert-to','pdf','--outdir',str(out),str(p)],timeout=60,check=False)
            except Exception: pass
        if pdf.exists(): preview_url=f'/preview_file/{space_id}/{pdf.name}'
    return render(req,'preview.html',{'space':sp,'rel_path':rel,'preview_url':preview_url,'text':text,'zip_items':zip_items,'ext':ext})
@app.get('/preview_file/{space_id}/{filename}')
def pf(req:Request,space_id:int,filename:str): user(req); return FileResponse(str(PREV/str(space_id)/Path(filename).name),media_type='application/pdf')
@app.get('/zip_download/{space_id}/{rel_path:path}')
def zd(req:Request,space_id:int,rel_path:str,member:str):
    u=user(req); sp=one('select * from spaces where id=?',(space_id,))
    if not has(u,'space',space_id,rel_path,op='download'): raise HTTPException(403)
    with zipfile.ZipFile(Path(sp['root_path'])/safe(rel_path)) as z: data=z.read(member)
    return StreamingResponse(iter([data]),headers={'Content-Disposition':f'attachment; filename={Path(member).name}'})

@app.get('/versions',response_class=HTMLResponse)
def versions(req:Request,q:str=''):
    u=user(req)
    sql='select fv.*,u.username from file_versions fv left join users u on fv.user_id=u.id where 1=1'
    args=[]
    if u['role']!='admin':
        sql+=' and fv.user_id=?'; args.append(u['id'])
    if q:
        sql+=' and fv.rel_path like ?'; args.append(f'%{q}%')
    sql+=' order by fv.id desc limit 500'
    users_sql='select distinct u.username from file_versions fv left join users u on fv.user_id=u.id where u.username is not null'
    users_args=[]
    if u['role']!='admin':
        users_sql+=' and fv.user_id=?'; users_args.append(u['id'])
    users_sql+=' order by u.username'
    return render(req,'versions.html',{'versions':rowlist(sql,args),'q':q,'users':rowlist(users_sql,users_args),'deps':rowlist('select * from departments order by name')})

@app.get('/versions/search',response_class=HTMLResponse)
def versions_search(req:Request,start:str='',end:str='',filename:str='',username:str='',department_id:int=0,keyword:str=''):
    u=user(req)
    sql='select fv.*,u.username,u.department_id from file_versions fv left join users u on fv.user_id=u.id where 1=1'
    args=[]
    if u['role']!='admin':
        sql+=' and fv.user_id=?'; args.append(u['id'])
    if start: sql+=' and fv.created_at>=?'; args.append(start)
    if end: sql+=' and fv.created_at<=?'; args.append(end+' 23:59:59')
    if filename: sql+=' and fv.rel_path like ?'; args.append('%'+filename+'%')
    if username and u['role']=='admin': sql+=' and u.username like ?'; args.append('%'+username+'%')
    if department_id and u['role']=='admin': sql+=' and u.department_id=?'; args.append(department_id)
    if keyword: sql+=' and (fv.rel_path like ? or fv.action like ? or fv.stored_path like ?)'; args += ['%'+keyword+'%']*3
    sql+=' order by fv.id desc limit 1000'
    users_sql='select distinct u.username from file_versions fv left join users u on fv.user_id=u.id where u.username is not null'
    users_args=[]
    if u['role']!='admin':
        users_sql+=' and fv.user_id=?'; users_args.append(u['id'])
    users_sql+=' order by u.username'
    return render(req,'versions.html',{'versions':rowlist(sql,args),'q':keyword,'start':start,'end':end,'filename':filename,'username':username,'department_id':department_id,'users':rowlist(users_sql,users_args),'deps':rowlist('select * from departments order by name')})

@app.post('/versions/restore')
def version_restore(req:Request,version_id:int=Form(...)):
    u=admin(req)
    v=one('select * from file_versions where id=?',(version_id,))
    if not v: raise HTTPException(404)
    sp=one('select * from spaces where id=?',(v['space_id'],))
    src=Path(v['stored_path'])
    dst=Path(sp['root_path'])/safe(v['rel_path'])
    if not src.exists(): raise HTTPException(404)
    dst.parent.mkdir(parents=True,exist_ok=True)
    if dst.exists():
        c=con()
        try:
            recver(c,v['space_id'],v['rel_path'],dst,u,'恢复前归档')
            c.commit()
        finally:
            c.close()
    shutil.copy2(src,dst)
    c=con()
    try:
        recver(c,v['space_id'],v['rel_path'],dst,u,'恢复版本')
        c.commit()
    finally:
        c.close()
    log(req,u,'restore_version',v['rel_path'])
    return RedirectResponse('/versions',302)
@app.get('/shares',response_class=HTMLResponse)
def shares(req:Request):
    u=user(req)
    # “我的分享”只显示我创建的分享，不显示别人分享给我的链接。
    sh=rowlist('select * from share_links where owner_id=? order by id desc',(u['id'],))
    uploads=[]
    for s in spaces_for(u):
        root=Path(s['root_path'])
        for p in root.rglob('*'):
            if p.is_file():
                rr=str(p.relative_to(root)).replace('\\','/')
                # 非管理员只展示自己上传过且拥有“分享”权限的文件；管理员仍可分享可见空间内的文件。
                mine = True if u['role']=='admin' else bool(one('select id from file_versions where user_id=? and space_id=? and rel_path=? limit 1',(u['id'],s['id'],rr)))
                if mine and has(u,'space',s['id'],rr,op='share'):
                    uploads.append({'space':s,'rel':rr})
    return render(req,'shares.html',{'shares':sh,'uploads':uploads[:300],'all_users':rowlist('select * from users order by username'),'all_deps':rowlist('select * from departments order by name'),'all_spaces':rowlist('select * from spaces order by name')})
@app.post('/share')
def share(req:Request,space_id:int=Form(...),rel_path:str=Form(...),target_type:str=Form('link'),target_id:str=Form(''),can_download:str=Form(None),can_upload:str=Form(None),can_write:str=Form(None),days:int=Form(7)):
    u=user(req)
    if not has(u,'space',space_id,rel_path,op='share'): raise HTTPException(403)
    tid=int(target_id) if str(target_id).strip().isdigit() else None
    exp='' if days>=36500 else ((datetime.now()+timedelta(days=days)).isoformat(sep=' ',timespec='seconds') if days>0 else '')
    c=con(); c.execute('insert into share_links(token,space_id,rel_path,owner_id,target_type,target_id,can_download,can_upload,can_write,created_at,expires_at) values(?,?,?,?,?,?,?,?,?,?,?)',(secrets.token_urlsafe(20),space_id,safe(rel_path),u['id'],target_type,tid,1 if can_download else 0,1 if can_upload else 0,1 if can_write else 0,now(),exp)); c.commit(); c.close(); log(req,u,'share',f'{rel_path}->{target_type}:{tid}'); return RedirectResponse('/shares',302)
@app.post('/shares/delete')
def shdel(req:Request,share_id:int=Form(...)):
    u=user(req)
    sh=one('select * from share_links where id=?',(share_id,))
    if not sh or sh['owner_id']!=u['id']:
        raise HTTPException(403)
    c=con(); c.execute('delete from share_links where id=? and owner_id=?',(share_id,u['id'])); c.commit(); c.close()
    log(req,u,'delete_share',str(share_id))
    return RedirectResponse('/shares',302)

@app.post('/shares/edit')
def shedit(req:Request,share_id:int=Form(...),can_download:str=Form(None),can_upload:str=Form(None),can_write:str=Form(None),days:int=Form(7)):
    u=user(req)
    sh=one('select * from share_links where id=?',(share_id,))
    if not sh or sh['owner_id']!=u['id']:
        raise HTTPException(403)
    exp='' if days>=36500 else ((datetime.now()+timedelta(days=days)).isoformat(sep=' ',timespec='seconds') if days>0 else '')
    c=con(); c.execute('update share_links set can_download=?,can_upload=?,can_write=?,expires_at=? where id=? and owner_id=?',(1 if can_download else 0,1 if can_upload else 0,1 if can_write else 0,exp,share_id,u['id'])); c.commit(); c.close()
    log(req,u,'edit_share',str(share_id))
    return RedirectResponse('/shares',302)

@app.get('/s/{token}')
def public(token:str):
    sh=one('select * from share_links where token=?',(token,));
    if not sh or (sh['expires_at'] and sh['expires_at']<now()): raise HTTPException(404)
    sp=one('select * from spaces where id=?',(sh['space_id'],)); p=Path(sp['root_path'])/safe(sh['rel_path']); return FileResponse(str(p),filename=p.name)

# admin crud pages
@app.get('/spaces',response_class=HTMLResponse)
def spaces(req:Request):
    u=user(req)
    ss=[s for s in (rowlist('select * from spaces') if u['role']=='admin' else spaces_for(u)) if s['kind']=='team']
    return render(req,'spaces.html',{'spaces':ss})

@app.post('/spaces')
def spadd(req:Request,name:str=Form(...),kind:str=Form('shared'),root_path:str=Form(''),note:str=Form('')):
    u=user(req)
    # 普通用户只能创建共享文件夹；团队空间只能管理员创建。
    if u['role']!='admin':
        kind='shared'
    root=Path(root_path) if root_path.strip() else STORE/safe(name)
    # 团队空间是服务端/管理员维护的路径，需要确保目录存在；
    # 共享文件夹是用户设备登记的路径，不再自动在服务端创建，避免误把客户端共享建到服务端。
    if kind=='team' or (kind!='team' and not root_path.strip()):
        root.mkdir(parents=True,exist_ok=True)
    c=con()
    c.execute('insert into spaces(name,kind,root_path,owner_id,note,created_at,host_name,host_ip) values(?,?,?,?,?,?,?,?)',(name,kind,str(root),u['id'],note,now(),client_host(req),client_ip(req)))
    sid=c.execute('select last_insert_rowid()').fetchone()[0]
    # 创建者自动拥有完整管理权限；共享文件夹默认所有人可访问，团队空间仅管理员创建后再授权。
    c.execute('insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin,can_preview,can_download,can_upload,can_share,can_rename,can_move,can_delete,creator_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('user',u['id'],'space',sid,'','allow',1,1,1,1,1,1,1,1,1,1,u['id']))
    if kind!='team':
        c.execute('insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin,can_preview,can_download,can_upload,can_share,can_rename,can_move,can_delete,creator_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('all',None,'folder',sid,'','allow',1,0,0,1,1,0,0,0,0,0,u['id']))
    c.commit(); c.close()
    log(req,u,'add_space',name)
    return RedirectResponse('/spaces' if kind=='team' else f'/files?space_id={sid}',302)

@app.post('/spaces/edit')
def spedit(req:Request,space_id:int=Form(...),name:str=Form(...),root_path:str=Form(...),note:str=Form('')):
    u=user(req)
    s=one('select * from spaces where id=?',(space_id,))
    if not s or (u['role']!='admin' and (s['kind']=='team' or s['owner_id']!=u['id'])):
        raise HTTPException(403)
    if s['kind']=='team':
        Path(root_path).mkdir(parents=True,exist_ok=True)
    c=con(); c.execute('update spaces set name=?,root_path=?,note=? where id=?',(name,root_path,note,space_id)); c.commit(); c.close()
    log(req,u,'edit_space',name)
    return RedirectResponse('/spaces' if s['kind']=='team' else '/files?space_id=-1',302)

@app.post('/spaces/delete')
def spdel(req:Request,space_id:int=Form(...)):
    u=user(req)
    s=one('select * from spaces where id=?',(space_id,))
    if not s or (u['role']!='admin' and (s['kind']=='team' or s['owner_id']!=u['id'])):
        raise HTTPException(403)
    c=con(); c.execute('delete from spaces where id=?',(space_id,)); c.execute('delete from permissions where resource_id=? and resource_type in ("space","folder","file")',(space_id,)); c.commit(); c.close()
    log(req,u,'delete_space',str(space_id))
    return RedirectResponse('/spaces' if s['kind']=='team' else '/files?space_id=-1',302)

@app.get('/departments',response_class=HTMLResponse)
def deps(req:Request): admin(req); return render(req,'departments.html',{'deps':rowlist('select * from departments')})
@app.post('/departments')
def depadd(req:Request,name:str=Form(...),parent_id:int=Form(0)):
    u=admin(req); c=con(); c.execute('insert into departments(name,parent_id) values(?,?)',(name,parent_id or None)); c.commit(); c.close(); log(req,u,'add_department',name); return RedirectResponse('/departments',302)
@app.post('/departments/edit')
def depedit(req:Request,dep_id:int=Form(...),name:str=Form(...),parent_id:int=Form(0)):
    u=admin(req); c=con(); c.execute('update departments set name=?,parent_id=? where id=?',(name,parent_id or None,dep_id)); c.commit(); c.close(); log(req,u,'edit_department',name); return RedirectResponse('/departments',302)
@app.post('/departments/delete')
def depdel(req:Request,dep_id:int=Form(...)):
    u=admin(req); c=con(); c.execute('update users set department_id=null where department_id=?',(dep_id,)); c.execute('delete from departments where id=?',(dep_id,)); c.commit(); c.close(); log(req,u,'delete_department',str(dep_id)); return RedirectResponse('/departments',302)
@app.get('/users',response_class=HTMLResponse)
def users(req:Request): admin(req); return render(req,'users.html',{'users':rowlist('select u.*,d.name dep from users u left join departments d on u.department_id=d.id'),'deps':rowlist('select * from departments')})
@app.post('/users')
def uadd(req:Request,username:str=Form(...),display_name:str=Form(''),password:str=Form(...),role:str=Form('user'),department_id:int=Form(0)):
    u=admin(req)
    username=(username or '').strip()
    if not username or not password:
        return RedirectResponse('/users',302)
    c=con()
    try:
        if c.execute('select id from users where username=?',(username,)).fetchone():
            c.close(); return RedirectResponse('/users',302)
        c.execute('insert into users(username,display_name,password,role,department_id,active,created_at) values(?,?,?,?,?,?,?)',(username,display_name,hp(password),role,department_id or None,1,now()))
        c.commit()
    finally:
        c.close()
    log(req,u,'add_user',username)
    return RedirectResponse('/users',302)
@app.post('/users/edit')
def uedit(req:Request,user_id:int=Form(...),username:str=Form(...),display_name:str=Form(''),role:str=Form('user'),department_id:int=Form(0),active:str=Form(None)):
    u=admin(req); c=con(); c.execute('update users set username=?,display_name=?,role=?,department_id=?,active=? where id=?',(username,display_name,role,department_id or None,1 if active else 0,user_id)); c.commit(); c.close(); log(req,u,'edit_user',username); return RedirectResponse('/users',302)
@app.post('/users/reset')
def ureset(req:Request,user_id:int=Form(...),password:str=Form(...)):
    u=admin(req); c=con(); c.execute('update users set password=? where id=?',(hp(password),user_id)); c.commit(); c.close(); log(req,u,'reset_password',str(user_id)); return RedirectResponse('/users',302)
@app.post('/users/delete')
def udel(req:Request,user_id:int=Form(...)):
    u=admin(req); 
    if user_id==u['id']: raise HTTPException(400,'不能删除自己')
    c=con(); c.execute('delete from users where id=?',(user_id,)); c.commit(); c.close(); log(req,u,'delete_user',str(user_id)); return RedirectResponse('/users',302)
@app.get('/permissions',response_class=HTMLResponse)
def perms(req:Request,q:str=''):
    u=user(req)
    all_perms=rowlist('select * from permissions order by id desc')
    visible=[]
    qlow=(q or '').lower()
    for p in all_perms:
        if u['role']!='admin':
            if p['subject_type']=='user' or p['creator_id']!=u['id'] or not can_manage_resource(u,p['resource_type'],p['resource_id'],p['rel_path'] or ''):
                continue
        subject_label=''
        subject_user=''
        if p['subject_type']=='user' and p['subject_id']:
            r=one('select username,display_name from users where id=?',(p['subject_id'],))
            subject_user=(r['username'] if r else '')
            subject_label=((r['username'] if r else '')+' '+(r['display_name'] if r and r['display_name'] else ''))
        elif p['subject_type']=='department' and p['subject_id']:
            r=one('select name from departments where id=?',(p['subject_id'],))
            subject_label=(r['name'] if r else '')
        text=f"{p['subject_type']} {p['subject_id'] or ''} {subject_label} {p['resource_type']} {p['resource_id']} {p['rel_path'] or ''} {p['effect']}".lower()
        if qlow and qlow not in text:
            continue
        d=dict(p); d['subject_label']=subject_label.strip(); d['subject_user']=subject_user; visible.append(d)
    return render(req,'permissions.html',{'perms':visible,'spaces':spaces_for(u),'printers':[p for p in rowlist('select * from printers') if has(u,'printer',p['id']) or p['owner_id']==u['id']],'users':rowlist('select * from users order by username'),'deps':rowlist('select * from departments order by name'),'resources':build_resource_items(u),'q':q})

@app.post('/permissions')
def permadd(req:Request,subject_type:str=Form(...),subject_id:int=Form(0),resource_key:str=Form(''),resource_type:str=Form('space'),resource_id:int=Form(0),rel_path:str=Form(''),effect:str=Form('allow'),can_read:str=Form(None),can_preview:str=Form(None),can_download:str=Form(None),can_upload:str=Form(None),can_share:str=Form(None),can_rename:str=Form(None),can_move:str=Form(None),can_delete:str=Form(None),can_admin:str=Form(None)):
    u=user(req)
    resource_type,resource_id,rel_path=parse_resource_key(resource_key,resource_type,resource_id,rel_path)
    if not can_manage_resource(u,resource_type,resource_id,rel_path):
        raise HTTPException(403)
    # 普通用户不能创建/修改“账号(用户)”主体的权限；账号权限仅管理员可操作。
    if u['role']!='admin' and subject_type=='user':
        raise HTTPException(403)
    c=con()
    c.execute('insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin,can_preview,can_download,can_upload,can_share,can_rename,can_move,can_delete,creator_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (subject_type,subject_id or None,resource_type,resource_id,rel_path,effect,1 if can_read else 0,1 if (can_upload or can_share or can_rename or can_move or can_delete) else 0,1 if can_admin else 0,1 if can_preview else 0,1 if can_download else 0,1 if can_upload else 0,1 if can_share else 0,1 if can_rename else 0,1 if can_move else 0,1 if can_delete else 0,u['id']))
    c.commit(); c.close()
    log(req,u,'add_permission',f'{effect} {subject_type}:{subject_id} {resource_type}:{resource_id}/{rel_path}')
    return RedirectResponse('/permissions',302)

@app.post('/permissions/edit')
def permedit(req:Request,perm_id:int=Form(...),subject_type:str=Form(...),subject_id:int=Form(0),resource_key:str=Form(''),resource_type:str=Form('space'),resource_id:int=Form(0),rel_path:str=Form(''),effect:str=Form('allow'),can_read:str=Form(None),can_preview:str=Form(None),can_download:str=Form(None),can_upload:str=Form(None),can_share:str=Form(None),can_rename:str=Form(None),can_move:str=Form(None),can_delete:str=Form(None),can_admin:str=Form(None)):
    u=user(req)
    old=one('select * from permissions where id=?',(perm_id,))
    if not old or not can_manage_resource(u,old['resource_type'],old['resource_id'],old['rel_path'] or ''):
        raise HTTPException(403)
    if u['role']!='admin' and (old['subject_type']=='user' or old['creator_id']!=u['id']):
        raise HTTPException(403)
    resource_type,resource_id,rel_path=parse_resource_key(resource_key,resource_type,resource_id,rel_path)
    if not can_manage_resource(u,resource_type,resource_id,rel_path):
        raise HTTPException(403)
    if u['role']!='admin' and subject_type=='user':
        raise HTTPException(403)
    c=con()
    c.execute('update permissions set subject_type=?,subject_id=?,resource_type=?,resource_id=?,rel_path=?,effect=?,can_read=?,can_write=?,can_admin=?,can_preview=?,can_download=?,can_upload=?,can_share=?,can_rename=?,can_move=?,can_delete=? where id=?',
              (subject_type,subject_id or None,resource_type,resource_id,rel_path,effect,1 if can_read else 0,1 if (can_upload or can_share or can_rename or can_move or can_delete) else 0,1 if can_admin else 0,1 if can_preview else 0,1 if can_download else 0,1 if can_upload else 0,1 if can_share else 0,1 if can_rename else 0,1 if can_move else 0,1 if can_delete else 0,perm_id))
    c.commit(); c.close()
    log(req,u,'edit_permission',str(perm_id))
    return RedirectResponse('/permissions',302)

@app.post('/permissions/delete')
def permdel(req:Request,perm_id:int=Form(...)):
    u=user(req)
    p=one('select * from permissions where id=?',(perm_id,))
    if not p or not can_manage_resource(u,p['resource_type'],p['resource_id'],p['rel_path'] or ''):
        raise HTTPException(403)
    if u['role']!='admin' and (p['subject_type']=='user' or p['creator_id']!=u['id']):
        raise HTTPException(403)
    c=con(); c.execute('delete from permissions where id=?',(perm_id,)); c.commit(); c.close()
    log(req,u,'delete_permission',str(perm_id))
    return RedirectResponse('/permissions',302)

def local_printers():
    if os.name!='nt': return []
    try:
        out=subprocess.check_output(['powershell','-NoProfile','-Command','Get-Printer | Select Name,ShareName,DriverName,PortName,Shared | ConvertTo-Json'],text=True,timeout=10,stderr=subprocess.DEVNULL)
        data=json.loads(out) if out.strip() else []; return [data] if isinstance(data,dict) else data
    except Exception: return []
@app.get('/printers',response_class=HTMLResponse)
def printers(req:Request):
    u=user(req); ps=[p for p in rowlist('select * from printers') if has(u,'printer',p['id'])]; return render(req,'printers.html',{'printers':ps,'local_printers':local_printers()})
@app.post('/printers')
def pradd(req:Request,name:str=Form(...),address:str=Form(''),share_name:str=Form(''),driver:str=Form(''),note:str=Form(''),do_share:str=Form(None)):
    u=user(req); msg=''
    if do_share and os.name=='nt':
        try:
            subprocess.run(['powershell','-NoProfile','-Command',f"Set-Printer -Name '{name}' -Shared $true -ShareName '{share_name or name}'"],timeout=10,check=False)
            msg='已尝试系统共享'
        except Exception as e:
            msg=f'共享失败:{e}'
    c=con()
    old=c.execute('select id from printers where name=?',(name,)).fetchone()
    if old:
        c.close(); log(req,u,'add_printer_skip',name+' 已存在'); return RedirectResponse('/printers',302)
    c.execute('insert into printers(name,address,share_name,driver,note,active,owner_id) values(?,?,?,?,?,1,?)',(name,address or f'\\\\{socket.gethostname()}\\{share_name or name}',share_name or name,driver,note+' '+msg,u['id']))
    pid=c.execute('select last_insert_rowid()').fetchone()[0]
    if u['role']=='admin':
        c.execute('insert into permissions(subject_type,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin) values(?,?,?,?,?,?,?,?)',('all','printer',pid,'','allow',1,0,0))
    else:
        c.execute('insert into permissions(subject_type,subject_id,resource_type,resource_id,rel_path,effect,can_read,can_write,can_admin) values(?,?,?,?,?,?,?,?,?)',('user',u['id'],'printer',pid,'','allow',1,0,1))
    c.commit(); c.close()
    log(req,u,'add_printer',name+' '+msg)
    return RedirectResponse('/printers',302)
@app.post('/printers/edit')
def predit(req:Request,printer_id:int=Form(...),name:str=Form(...),address:str=Form(''),share_name:str=Form(''),driver:str=Form(''),note:str=Form(''),active:str=Form(None)):
    u=user(req)
    p=one('select * from printers where id=?',(printer_id,))
    if not p or (u['role']!='admin' and p['owner_id']!=u['id']):
        raise HTTPException(403)
    c=con()
    c.execute('update printers set name=?,address=?,share_name=?,driver=?,note=?,active=? where id=?',(name,address,share_name,driver,note,1 if active else 0,printer_id))
    c.commit(); c.close()
    log(req,u,'edit_printer',name)
    return RedirectResponse('/printers',302)
@app.post('/printers/delete')
def prdel(req:Request,printer_id:int=Form(...)):
    u=user(req)
    p=one('select * from printers where id=?',(printer_id,))
    if not p or (u['role']!='admin' and p['owner_id']!=u['id']):
        raise HTTPException(403)
    c=con(); c.execute('delete from printers where id=?',(printer_id,)); c.commit(); c.close()
    log(req,u,'delete_printer',str(printer_id))
    return RedirectResponse('/printers',302)


@app.get('/printers/connect/{printer_id}')
def printer_connect(req:Request,printer_id:int):
    u=user(req)
    p=one('select * from printers where id=?',(printer_id,))
    if not p or not has(u,'printer',printer_id):
        raise HTTPException(403)
    addr=p['address'] or ('\\\\'+socket.gethostname()+'\\'+(p['share_name'] or p['name']))
    bat='@echo off\r\nchcp 65001 >nul\r\necho 正在连接共享打印机: '+addr+'\r\nrundll32 printui.dll,PrintUIEntry /in /n \"'+addr+'\"\r\nif errorlevel 1 echo 添加失败，请确认网络可访问、打印机已共享，并以管理员权限运行。\r\npause\r\n'
    return StreamingResponse(iter([bat.encode('utf-8')]),media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename=connect_printer_{printer_id}.bat'})
@app.get('/logs',response_class=HTMLResponse)
def logs(req:Request,start:str='',end:str='',username:str='',action:str='',keyword:str=''):
    admin(req); sql='select * from logs where 1=1'; args=[]
    if start: sql+=' and created_at>=?'; args.append(start)
    if end: sql+=' and created_at<=?'; args.append(end+' 23:59:59')
    if username: sql+=' and username like ?'; args.append('%'+username+'%')
    if action: sql+=' and action like ?'; args.append('%'+action+'%')
    if keyword: sql+=' and (detail like ? or ip like ? or action like ?)'; args += ['%'+keyword+'%']*3
    sql+=' order by id desc limit 1000'
    return render(req,'logs.html',{'logs':rowlist(sql,args),'start':start,'end':end,'username':username,'action':action,'keyword':keyword,'log_users':rowlist('select distinct username from logs where username<>"" order by username'),'log_actions':rowlist('select distinct action from logs where action<>"" order by action')})

@app.post('/logs/edit')
def log_edit(req:Request,log_id:int=Form(...),action:str=Form(...),detail:str=Form(''),ip:str=Form('')):
    u=admin(req)
    c=con(); c.execute('update logs set action=?,detail=?,ip=? where id=?',(action,detail,ip,log_id)); c.commit(); c.close()
    log(req,u,'edit_log',str(log_id))
    return RedirectResponse('/logs',302)

@app.post('/logs/delete')
def log_delete(req:Request,log_id:int=Form(...)):
    u=admin(req)
    c=con(); c.execute('delete from logs where id=?',(log_id,)); c.commit(); c.close()
    log(req,u,'delete_log',str(log_id))
    return RedirectResponse('/logs',302)

@app.get('/theme',response_class=HTMLResponse)
def th(req:Request):
    admin(req)
    fonts=['Microsoft YaHei','SimSun','SimHei','KaiTi','FangSong','Arial','Segoe UI','Tahoma','Verdana','Times New Roman','Consolas','Courier New']
    return render(req,'theme.html',{'fonts':fonts})
@app.post('/theme/save')
async def thsave(req:Request):
    u=admin(req); f=await req.form(); menus={k[5:]:v for k,v in f.items() if k.startswith('menu_')}; val={'logo':f.get('logo','局域网共享云'),'accent':f.get('accent','#1677ff'),'font_size':f.get('font_size','16'),'font_family':f.get('font_family','Microsoft YaHei'),'menus':menus}
    c=con(); c.execute('insert or replace into settings(k,v) values(?,?)',('theme',json.dumps(val,ensure_ascii=False))); c.commit(); c.close(); log(req,u,'save_theme','保存主题菜单'); return RedirectResponse('/theme',302)
@app.get('/backup',response_class=HTMLResponse)
def backup(req:Request): admin(req); return render(req,'backup.html',{'backups':sorted(BACK.glob('*.zip'),reverse=True)})
@app.post('/backup/create')
def bcreate(req:Request):
    u=admin(req); name=f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'; dst=BACK/name
    tmpdb=BACK/(name+'.dbtmp')
    src=con()
    bak=sqlite3.connect(tmpdb)
    try:
        src.backup(bak)
    finally:
        bak.close(); src.close()
    with zipfile.ZipFile(dst,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(tmpdb,'app.db')
        for p in STORE.rglob('*'):
            if p.is_file():
                z.write(p,Path('storage')/p.relative_to(STORE))
        if CFG_FILE.exists():
            z.write(CFG_FILE,'config.json')
    tmpdb.unlink(missing_ok=True)
    log(req,u,'create_backup',name); return RedirectResponse('/backup',302)
@app.get('/backup/download/{name}')
def bdl(req:Request,name:str): admin(req); p=BACK/Path(name).name; return FileResponse(str(p),filename=p.name)
@app.post('/backup/delete')
def bdel(req:Request,name:str=Form(...)):
    u=admin(req); p=BACK/Path(name).name
    if p.exists(): p.unlink()
    log(req,u,'delete_backup',name); return RedirectResponse('/backup',302)
@app.post('/backup/restore')
def brestore(req:Request,file:UploadFile=File(...)):
    u=admin(req); tmp=DATA/'restore.zip'; open(tmp,'wb').write(file.file.read())
    with zipfile.ZipFile(tmp) as z: z.extractall(DATA)
    tmp.unlink(missing_ok=True); log(req,u,'restore_backup',file.filename); return RedirectResponse('/backup',302)
@app.get('/settings',response_class=HTMLResponse)
def settings(req:Request):
    admin(req); drives=[f'{d}:\\' for d in string.ascii_uppercase if Path(f'{d}:\\').exists()] if os.name=='nt' else ['/']
    return render(req,'settings.html',{'drives':drives})
@app.post('/settings')
async def settings_save(req:Request):
    u=admin(req); f=await req.form(); cfg=load_cfg()
    cfg['storage_root']=f.get('storage_root',cfg.get('storage_root','data/storage'))
    cfg['port']=int(f.get('port',cfg.get('port',8000)))
    cfg['db_backend']=f.get('db_backend',cfg.get('db_backend','sqlite'))
    cfg['db_path']=f.get('db_path',cfg.get('db_path','data/app.db'))
    cfg['db_url']=f.get('db_url',cfg.get('db_url',''))
    cfg['secret_key']=f.get('secret_key',cfg.get('secret_key','change-me'))
    cfg['restart_command']=f.get('restart_command',cfg.get('restart_command',''))
    cfg['onlyoffice_url']=f.get('onlyoffice_url',cfg.get('onlyoffice_url',''))
    cfg['collabora_url']=f.get('collabora_url',cfg.get('collabora_url',''))
    CFG_FILE.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    log(req,u,'save_settings',json.dumps(cfg,ensure_ascii=False)); return RedirectResponse('/settings?saved=1',302)
@app.post('/shutdown')
def shutdown(req:Request): u=admin(req); log(req,u,'shutdown','退出服务'); os._exit(0)

@app.post('/restart')
def restart(req:Request):
    u=admin(req); log(req,u,'restart','请求重启服务')
    cmd=CFG.get('restart_command') or ''
    if cmd.strip():
        try:
            subprocess.Popen(cmd, shell=True, cwd=str(ROOT))
        except Exception:
            pass
    os._exit(0)
@app.get('/favicon.ico')
def fav(): raise HTTPException(404)

if __name__=='__main__':
    import uvicorn; uvicorn.run('main:app',host='0.0.0.0',port=int(CFG.get('port',8000)),reload=False)


@app.get('/office/edit/{space_id}/{rel_path:path}',response_class=HTMLResponse)
def office_edit(req:Request,space_id:int,rel_path:str):
    u=user(req); rel=safe(rel_path)
    if not has(u,'space',space_id,rel,op='preview'): raise HTTPException(403)
    sp=one('select * from spaces where id=?',(space_id,))
    return render(req,'office_edit.html',{'space':sp,'rel_path':rel,'onlyoffice_url':CFG.get('onlyoffice_url',''),'collabora_url':CFG.get('collabora_url','')})
