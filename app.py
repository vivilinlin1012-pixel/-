import io,os,re,json,uuid,sqlite3,zipfile
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI,File,Form,UploadFile,HTTPException
from fastapi.responses import FileResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from docx import Document
from pypdf import PdfReader
from PIL import Image
ROOT=Path(__file__).resolve().parent
STORE=Path(os.getenv("CINEFLOW_STORAGE",str(ROOT/"storage")))
STORE.mkdir(parents=True,exist_ok=True)
app=FastAPI(title="CineFlow Studio")
app.mount("/web",StaticFiles(directory=ROOT/"web"),name="web")
@contextmanager
def db():
 c=sqlite3.connect(STORE/"cineflow.db");c.row_factory=sqlite3.Row
 try: yield c;c.commit()
 finally:c.close()
with db() as c:
 c.executescript("""CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT,script TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,pid TEXT,kind TEXT,label TEXT,path TEXT);
 CREATE TABLE IF NOT EXISTS shots(id TEXT PRIMARY KEY,pid TEXT,ord INTEGER,scene TEXT,plot TEXT,dialogue TEXT,emotion TEXT DEFAULT '',prompt TEXT DEFAULT '',keyframe TEXT DEFAULT '',characters TEXT DEFAULT '[]',environments TEXT DEFAULT '[]',confidence REAL DEFAULT 0,status TEXT DEFAULT 'pending');""")
def uid():return uuid.uuid4().hex[:16]
def need(pid):
 with db() as c:
  if not c.execute("SELECT id FROM projects WHERE id=?",(pid,)).fetchone():raise HTTPException(404,"项目不存在")
def data(pid):
 need(pid)
 with db() as c:
  p=dict(c.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone())
  a=[dict(x) for x in c.execute("SELECT * FROM assets WHERE pid=?",(pid,))]
  s=[dict(x) for x in c.execute("SELECT * FROM shots WHERE pid=? ORDER BY ord",(pid,))]
 for x in s:
  x["characters"]=json.loads(x["characters"]);x["environments"]=json.loads(x["environments"])
 return dict(project=p,assets=a,shots=s)
class Project(BaseModel):title:str
class Script(BaseModel):text:str
class Shot(BaseModel):
 scene:str|None=None;plot:str|None=None;dialogue:str|None=None;emotion:str|None=None;prompt:str|None=None;keyframe:str|None=None;characters:list[str]|None=None;environments:list[str]|None=None;status:str|None=None
@app.get("/")
def index():return FileResponse(ROOT/"web/index.html")
@app.get("/api/health")
def health():return {"ok":True,"ai_configured":False}
@app.get("/api/projects")
def projects():
 with db() as c:return [dict(x) for x in c.execute("SELECT id,title FROM projects")]
@app.post("/api/projects")
def create(p:Project):
 if not p.title.strip():raise HTTPException(422,"需要项目名")
 id=uid()
 with db() as c:c.execute("INSERT INTO projects(id,title) VALUES (?,?)",(id,p.title.strip()))
 return {"id":id}
@app.get("/api/projects/{pid}")
def get(pid:str):return data(pid)
@app.post("/api/projects/{pid}/script")
def setscript(pid:str,s:Script):
 need(pid)
 with db() as c:c.execute("UPDATE projects SET script=? WHERE id=?",(s.text,pid))
 return {"length":len(s.text)}
@app.post("/api/projects/{pid}/script-file")
async def script_file(pid:str,file:UploadFile=File(...)):
 need(pid);ext=Path(file.filename or "").suffix.lower()
 if ext not in (".txt",".docx",".pdf"):raise HTTPException(400,"支持TXT DOCX PDF")
 raw=await file.read(25*1024*1024+1)
 if len(raw)>25*1024*1024:raise HTTPException(413,"文件过大")
 try:
  if ext==".txt":text=raw.decode("utf-8-sig")
  elif ext==".docx":text="\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
  else:text="\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages)
 except Exception:raise HTTPException(422,"无法解析剧本，请转换成UTF-8 TXT")
 if not text.strip():raise HTTPException(422,"没有文字，请先对扫描文件进行OCR")
 return setscript(pid,Script(text=text))
@app.post("/api/projects/{pid}/assets")
async def upload(pid:str,kind:str=Form(...),files:list[UploadFile]=File(...)):
 need(pid)
 if kind not in ("character","environment","keyframe"):raise HTTPException(400,"分类错误")
 out=[]
 for f in files:
  ext=Path(f.filename or "").suffix.lower()
  if ext not in (".jpg",".jpeg",".png",".webp"):raise HTTPException(400,"仅接受图片")
  raw=await f.read(25*1024*1024+1)
  if len(raw)>25*1024*1024:raise HTTPException(413,"图片超过25MB")
  try:
   with Image.open(io.BytesIO(raw)) as im:im.verify()
  except Exception:raise HTTPException(422,"图片损坏")
  id=uid(); rel=id+ext;folder=STORE/pid/"assets";folder.mkdir(parents=True,exist_ok=True);(folder/rel).write_bytes(raw)
  label=Path(f.filename or "图片").stem[:100]
  with db() as c:c.execute("INSERT INTO assets VALUES (?,?,?,?,?)",(id,pid,kind,label,rel))
  out.append(id)
 return {"added":out}
@app.get("/api/projects/{pid}/assets/{aid}/file")
def image(pid:str,aid:str):
 with db() as c:a=c.execute("SELECT path FROM assets WHERE id=? AND pid=?",(aid,pid)).fetchone()
 if not a:raise HTTPException(404,"图片不存在")
 return FileResponse(STORE/pid/"assets"/a["path"])
@app.post("/api/projects/{pid}/parse")
def parse(pid:str):
 d=data(pid)
 with db() as c:
  if c.execute("SELECT COUNT(*) FROM shots WHERE pid=?",(pid,)).fetchone()[0]:raise HTTPException(409,"已有镜头，为防止覆盖人工修改，不重复解析")
 text=d["project"]["script"];blocks=[b.strip() for b in re.split(r"\n\s*\n",text) if b.strip()]
 if not blocks:raise HTTPException(400,"请先导入剧本")
 scene="场景1";rows=[]
 for b in blocks[:300]:
  lines=b.splitlines()
  if re.match(r"^(第.{1,8}[场幕镜]|场景|镜头|SCENE|SHOT)",lines[0],re.I):
   scene=lines.pop(0)
  if not lines:continue
  dialog=[x for x in lines if re.match(r"^[\u4e00-\u9fff\w·]{1,12}[：:].+",x)]
  plot=[x for x in lines if x not in dialog]
  rows.append((uid(),pid,len(rows),scene,"\n".join(plot) or b,"\n".join(dialog)))
 with db() as c:c.executemany("INSERT INTO shots(id,pid,ord,scene,plot,dialogue) VALUES (?,?,?,?,?,?)",rows)
 return {"shots_created":len(rows)}
@app.post("/api/projects/{pid}/match")
def match(pid:str):
 d=data(pid);shots=d["shots"];assets=d["assets"]
 if not shots:raise HTTPException(400,"请先解析剧本")
 keys=[a for a in assets if a["kind"]=="keyframe"]
 chars=[a for a in assets if a["kind"]=="character"]
 envs=[a for a in assets if a["kind"]=="environment"]
 count=0
 with db() as c:
  for i,s in enumerate(shots):
   if s["status"]=="approved":continue
   words=(s["scene"]+s["plot"]+s["dialogue"]).lower()
   cs=[a["id"] for a in chars if len(a["label"])>=2 and a["label"].lower() in words]
   es=[a["id"] for a in envs if len(a["label"])>=2 and a["label"].lower() in words]
   key=keys[i]["id"] if i<len(keys) else ""
   c.execute("UPDATE shots SET keyframe=?,characters=?,environments=?,confidence=?,status='review' WHERE id=?",(key,json.dumps(cs),json.dumps(es),.2 if key else 0,s["id"]));count+=1
 return {"matched":count,"mode":"heuristic","message":"按上传顺序与文字标签生成低置信度候选，尚未进行视觉AI识别"}
@app.patch("/api/projects/{pid}/shots/{sid}")
def edit(pid:str,sid:str,body:Shot):
 d=data(pid);fields=body.model_dump(exclude_unset=True)
 if not any(s["id"]==sid for s in d["shots"]):raise HTTPException(404,"镜头不存在")
 allowed={a["id"]:a["kind"] for a in d["assets"]}
 for name,kind in (("keyframe","keyframe"),("characters","character"),("environments","environment")):
  if name not in fields:continue
  values=fields[name] if isinstance(fields[name],list) else ([fields[name]] if fields[name] else [])
  if any(allowed.get(x)!=kind for x in values):raise HTTPException(400,"素材引用无效")
 if fields.get("status") not in (None,"pending","review","approved"):raise HTTPException(400,"状态无效")
 for name in ("characters","environments"):
  if name in fields:fields[name]=json.dumps(fields[name])
 if fields:
  with db() as c:c.execute("UPDATE shots SET "+",".join(k+"=?" for k in fields)+" WHERE id=? AND pid=?",(*fields.values(),sid,pid))
 return {"updated":True}
@app.get("/api/projects/{pid}/export")
def export(pid:str):
 d=data(pid);assets={a["id"]:a for a in d["assets"]};out=io.BytesIO()
 with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
  z.writestr("storyboard.json",json.dumps(d,ensure_ascii=False,indent=2))
  for i,s in enumerate(d["shots"],1):
   folder=f"shots/{i:03d}/"
   z.writestr(folder+"剧情与视频指令.txt",f'场景：{s["scene"]}\n剧情：{s["plot"]}\n台词：{s["dialogue"]}\n情绪：{s["emotion"]}\n指令：{s["prompt"]}\n审核：{s["status"]}')
   for prefix,ids in (("关键图",[s["keyframe"]] if s["keyframe"] else []),("人物",s["characters"]),("环境",s["environments"])):
    for j,id in enumerate(ids,1):
     if id in assets:
      a=assets[id];p=STORE/pid/"assets"/a["path"]
      if p.is_file():z.write(p,folder+prefix+f"_{j:02d}"+Path(p).suffix)
 out.seek(0)
 return StreamingResponse(out,media_type="application/zip",headers={"Content-Disposition":'attachment; filename="cineflow-storyboard.zip"'})
