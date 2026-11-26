import io,os,re,json,uuid,sqlite3,zipfile
import httpx,asyncio,logging
from vision import match_keyframes,call
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
def health():return {"ok":True,"ai_configured":True}
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
class AIConfig(BaseModel):
 base:str;key:str;model:str
@app.post("/api/ai-config")
def configure(c:AIConfig):
 if not c.base.startswith(("https://","http://127.0.0.1:","http://localhost:")) or not c.model.strip():raise HTTPException(400,"请输入有效模型地址和名称")
 os.environ["CINEFLOW_AI_BASE_URL"]=c.base.strip()
 os.environ["CINEFLOW_AI_API_KEY"]=c.key.strip() or "local"
 os.environ["CINEFLOW_AI_MODEL"]=c.model.strip()
 return {"ok":True}
@app.post("/api/projects/{pid}/parse")
def parse(pid:str):
 d=data(pid);keys=[a for a in d["assets"] if a["kind"]=="keyframe"]
 if not keys:raise HTTPException(400,"请先导入关键图")
 with db() as c:
  for i,a in enumerate(keys):
   if not c.execute("SELECT id FROM shots WHERE pid=? AND keyframe=?",(pid,a["id"])).fetchone():
    c.execute("INSERT INTO shots(id,pid,ord,scene,plot,dialogue,keyframe) VALUES (?,?,?,?,?,?,?)",(uid(),pid,i,"待分析","","",a["id"]))
 return {"shots_created":len(keys)}
jobs={}
tasks=set()
def settings():
 return (os.getenv("CINEFLOW_AI_BASE_URL","http://127.0.0.1:11434/v1"),os.getenv("CINEFLOW_AI_API_KEY","local"),os.getenv("CINEFLOW_AI_MODEL","qwen2.5vl:7b"))
@app.post("/api/ai-test")
async def ai_test():
 base,key,model=settings()
 try:
  await call(base.rstrip("/")+"/chat/completions",key,model,[{"role":"user","content":"返回JSON对象：{\"ok\":true}"}],required=("ok",))
 except Exception as e:raise HTTPException(502,"模型测试失败："+str(e)) from e
 return {"ok":True}
@app.get("/api/projects/{pid}/job")
def job_status(pid:str):
 need(pid)
 return jobs.get(pid,{"state":"idle"})
@app.post("/api/projects/{pid}/match")
async def match(pid:str,target:str="",retry:bool=False):
 d=data(pid)
 if any(j["state"]=="running" for j in jobs.values()):raise HTTPException(409,"已有分析任务运行中")
 if not d["project"]["script"].strip():raise HTTPException(400,"请先导入剧本")
 existing={s["keyframe"]:s for s in d["shots"]}
 keys=[a for a in d["assets"] if a["kind"]=="keyframe" and (not target or a["id"]==target) and existing.get(a["id"],{}).get("status")!="approved"]
 if retry:keys=[a for a in keys if not existing.get(a["id"],{}).get("plot") or not existing.get(a["id"],{}).get("emotion")]
 if not keys:raise HTTPException(400,"没有需要分析的关键图")
 base,token,model=settings()
 j=jobs[pid]={"state":"running","total":len(keys),"done":0,"success":0,"errors":[],"message":"准备分析"}
 async def worker():
  try:
   for a in keys:
    try:
     results=await match_keyframes(pid,STORE,d["project"]["script"],d["assets"],base,token,model,target=a["id"],progress=lambda msg:j.update(message=msg))
     m=results[0]
     with db() as c:
      if not c.execute("SELECT id FROM shots WHERE pid=? AND keyframe=? AND status='approved'",(pid,a["id"])).fetchone():
       c.execute("DELETE FROM shots WHERE pid=? AND keyframe=? AND status!='approved'",(pid,a["id"]))
       c.execute("INSERT INTO shots(id,pid,ord,scene,plot,dialogue,emotion,prompt,keyframe,characters,environments,confidence,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(uid(),pid,int(m["order"]),m["scene"],m["plot"],m["dialogue"],m["emotion"],m["prompt"],m["keyframe"],json.dumps(m["characters"]),json.dumps(m["environments"]),m["confidence"],"review"))
     j["success"]+=1
    except Exception as e:
     logging.exception("Frame analysis failed")
     j["errors"].append({"id":a["id"],"label":a["label"],"error":str(e)[:500]})
    finally:j["done"]+=1
  finally:j.update(state="finished",message=f'完成：成功 {j["success"]}，失败 {len(j["errors"])}')
 t=asyncio.create_task(worker());tasks.add(t);t.add_done_callback(tasks.discard)
 return j
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
