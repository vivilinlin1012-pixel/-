"""Optional multimodal image understanding and shot candidate ranking.

Send only to the provider configured by the user. No credentials in source.
"""
import base64,io,json,re,hashlib
from pathlib import Path
import httpx
from PIL import Image

def unpack(content):
    if isinstance(content,list):
        content="".join(x.get("text","") for x in content if isinstance(x,dict))
    if not isinstance(content,str):
        raise ValueError("模型返回空内容或非文本")
    decoder=json.JSONDecoder()
    for m in re.finditer(r"\{",content):
        try:
            obj,_=decoder.raw_decode(content[m.start():])
            if isinstance(obj,dict):return obj
        except ValueError:continue
    raise ValueError("模型未返回完整JSON对象")

async def call(url,key,model,messages,required=()):
    local=url.startswith(("http://127.0.0.1:","http://localhost:"))
    payload={"model":model,"temperature":0.1,"messages":messages,"response_format":{"type":"json_object"},"max_tokens":2048}
    async with httpx.AsyncClient(timeout=httpx.Timeout(600,connect=15),trust_env=not local) as client:
        for attempt in range(2):
            resp=await client.post(url,headers={"Authorization":"Bearer "+key},json=payload)
            resp.raise_for_status()
            choice=resp.json()["choices"][0]
            try:
                obj=unpack(choice["message"].get("content"))
                if not obj or any(not obj.get(k) for k in required):raise ValueError("缺少有效字段")
                return obj
            except ValueError:
                if attempt:raise ValueError("模型连续两次未返回完整JSON；结束原因："+str(choice.get("finish_reason")))
                payload["messages"]=messages+[{"role":"user","content":"上次输出为空或缺少字段。必须返回完整JSON对象，必填非空字段："+", ".join(required)}]
                payload["temperature"]=0
                payload["max_tokens"]=4096

async def describe(url,key,model,path):
    with Image.open(path) as image:
        image=image.convert("RGB")
        image.thumbnail((768,768))
        out=io.BytesIO()
        image.save(out,format="JPEG",quality=75)
    b64=base64.b64encode(out.getvalue()).decode("ascii")
    return await call(url,key,model,[{"role":"user","content":[
       {"type":"text","text":"你是影视分镜视觉分析师。只依据可见画面输出JSON对象，字段characters(人物外观描述数组), environment(场景布局), action(人物动作), mood(情绪), composition(景别构图)。不要编造不可见事件和真实姓名。"},
       {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}
    ]}],required=("environment","composition"))

async def match(pid,store,shots,assets,base,key,model):
    endpoint=base.rstrip("/")+"/chat/completions"
    descriptions={}
    for asset in assets:
        path=Path(store)/pid/"assets"/asset["path"]
        if path.is_file():
            descriptions[asset["id"]]=await describe(endpoint,key,model,path)
    prompt={"shots":[{"id":s["id"],"scene":s["scene"],"plot":s["plot"][:1300],"dialogue":s["dialogue"][:500]} for s in shots[:100]],
            "assets":[{"id":a["id"],"kind":a["kind"],"label":a["label"],"visual":descriptions.get(a["id"],{})} for a in assets[:150]]}
    instructions=("根据剧情语义、画面人物、动作、场景、上下文、角色服装等，为每个shot分配最合理的keyframe、characters和environments。"
        "禁止杜撰素材ID；同一个关键图一般不应强行分给多个镜头；不确定时留空。"
        "原文剧情和台词不许修改。仅输出JSON对象："
        '{"matches":[{"shot_id":"...","keyframe":"","characters":[],"environments":[],"confidence":0.0}]}。'
        "置信度在0到1之间，保守评分。\n"+json.dumps(prompt,ensure_ascii=False))
    result=await call(endpoint,key,model,[{"role":"user","content":instructions}])
    ids={a["id"]:a["kind"] for a in assets}
    valid_shots={s["id"] for s in shots}
    out=[]
    for m in result.get("matches",[]):
        if m.get("shot_id") not in valid_shots:continue
        k=m.get("keyframe") or ""
        if ids.get(k)!="keyframe":k=""
        cs=[x for x in (m.get("characters") or []) if isinstance(x,str) and ids.get(x)=="character"]
        es=[x for x in (m.get("environments") or []) if isinstance(x,str) and ids.get(x)=="environment"]
        try:score=max(0.,min(1.,float(m.get("confidence",0))))
        except (ValueError,TypeError):score=0.
        out.append({"id":m["shot_id"],"keyframe":k,"characters":cs,"environments":es,"confidence":score})
    return out

def indices(value,size):
    if not isinstance(value,list):value=[value]
    out=[]
    for n in value:
        if isinstance(n,str) and n.strip().isdigit():n=int(n.strip())
        if type(n)==int and 1<=n<=size and n not in out:out.append(n)
    return out

async def match_keyframes(pid,store,script,assets,base,key,model,target=None,progress=lambda x:None):
    endpoint=base.rstrip("/")+"/chat/completions"
    cache=Path(store)/pid/"vision-cache"
    cache.mkdir(exist_ok=True)
    async def indexed(a):
        path=Path(store)/pid/"assets"/a["path"]
        p=cache/(hashlib.sha256((base+model+a["id"]+"v5").encode()).hexdigest()+".json")
        if p.exists():return json.loads(p.read_text())
        progress("建立素材索引："+a["label"])
        v=await describe(endpoint,key,model,path)
        p.write_text(json.dumps(v,ensure_ascii=False))
        return v
    pools={"character":[],"environment":[]}
    for a in assets:
        if a["kind"] in pools:
            try:v=await indexed(a)
            except Exception:continue
            pools[a["kind"]].append({"id":a["id"],"name":a["label"],"visual":v})
    # Preserve original lines; never invent dialogue or split by arbitrary line counts.
    lines=[x.strip() for x in script.splitlines() if x.strip()]
    def grams(x):
        x=re.sub(r"[^\w\u4e00-\u9fff]","",x.lower())
        return set(x[i:i+2] for i in range(len(x)-1))
    def shortlist(visual):
        q=grams(json.dumps(visual,ensure_ascii=False))
        scores=[len(q & grams(line)) for line in lines]
        if len(lines)<=80:return list(range(len(lines)))
        best=sorted(range(len(lines)),key=lambda i:scores[i],reverse=True)[:24]
        return sorted({j for i in best for j in range(max(0,i-1),min(len(lines),i+2))})
    out=[]
    for a in assets:
        if a["kind"]!="keyframe" or (target and a["id"]!=target):continue
        visual=await indexed(a)
        progress("连接三个素材池："+a["label"])
        chosen=shortlist(visual)
        task={"keyframe":visual,"script":[{"n":i+1,"text":lines[i]} for i in chosen]}
        for kind,pool in pools.items():
            task[kind]=[{"n":i+1,"name":x["name"],"visual":x["visual"]} for i,x in enumerate(pool)]
        m=await call(endpoint,key,model,[{"role":"user","content":"你只负责匹配三个独立素材池。根据关键图选择真实对应的剧情原文编号、对白编号、人物定妆照编号、环境编号。各池编号独立，不确定返回空数组。不要生成视频指令。返回JSON：{\"scene\":\"场次\",\"plot\":[1],\"dialogue\":[],\"character\":[1],\"environment\":[1],\"emotion\":\"可见人物情绪\"}。"+json.dumps(task,ensure_ascii=False)}])
        diagnostic=Path(store)/pid/"match-diagnostics"
        diagnostic.mkdir(exist_ok=True)
        (diagnostic/(a["id"]+".json")).write_text(json.dumps({"keyframe":a["label"],"input":task,"model_response":m},ensure_ascii=False,indent=2))
        def original(k):
            return "\n".join(lines[n-1] for n in indices(m.get(k,[]),len(lines)) if n-1 in chosen)
        def refs(k):
            return [pools[k][n-1]["id"] for n in indices(m.get(k,[]),len(pools[k]))]
        plot=original("plot"); cs=refs("character"); es=refs("environment")
        errors=[]
        if not plot:errors.append("剧情未确认")
        if pools["character"] and not cs:errors.append("人物未确认")
        if pools["environment"] and not es:errors.append("环境未确认")
        out.append(dict(keyframe=a["id"],scene=str(m.get("scene") or "待核对"),plot=plot,dialogue=original("dialogue"),emotion=str(m.get("emotion") or visual.get("mood") or ""),prompt="",characters=cs,environments=es,confidence=0,errors=errors,order=assets.index(a)))
    return out
