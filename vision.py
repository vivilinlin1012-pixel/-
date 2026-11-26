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

async def match_keyframes(pid,store,script,assets,base,key,model,target=None,progress=lambda x:None):
    endpoint=base.rstrip("/")+"/chat/completions"
    async def cached(a):
        path=Path(store)/pid/"assets"/a["path"]
        cache=Path(store)/pid/"vision-cache"
        cache.mkdir(exist_ok=True)
        name=hashlib.sha256((base+model+a["id"]+"v3").encode()).hexdigest()
        p=cache/(name+".json")
        if p.exists():return json.loads(p.read_text())
        progress("识别素材："+a["label"])
        value=await describe(endpoint,key,model,path)
        p.write_text(json.dumps(value,ensure_ascii=False))
        return value
    refs=[x for x in assets if x["kind"] in ("character","environment")]
    out=[]
    lines=[line for line in script.splitlines() if line.strip()]
    for a in assets:
        if a["kind"]!="keyframe" or (target and a["id"]!=target):continue
        progress("识别当前关键图："+a["label"])
        visual=await cached(a)
        progress("匹配剧本原文："+a["label"])
        task={"script_lines":[{"n":i+1,"text":line} for i,line in enumerate(lines)],"keyframe":visual}
        m=await call(endpoint,key,model,[{"role":"user","content":'根据画面匹配剧本。只返回JSON：{"scene":"场次","plot_lines":[1],"dialogue_lines":[],"emotion":"人物情绪","confidence":0.8}。剧情行号必须来自输入，不选标题或元数据。无对白返回空数组。'+json.dumps(task,ensure_ascii=False)}],required=("scene","plot_lines","emotion"))
        def source(k):
            values=m.get(k,[])
            return "\n".join(lines[n-1] for n in values if type(n)==int and 1<=n<=len(lines)) if isinstance(values,list) else ""
        plot=source("plot_lines");dialogue=source("dialogue_lines")
        if not plot or not isinstance(m["emotion"],str):raise ValueError("未找到有效剧情")
        progress("匹配人物与环境："+a["label"])
        candidates=[{"n":i+1,"kind":x["kind"],"label":x["label"]} for i,x in enumerate(refs)]
        matchrefs=await call(endpoint,key,model,[{"role":"user","content":'根据画面描述、原文剧情及素材名称选出对应角色造型和环境。仅返回JSON：{"characters":[1],"environments":[2]}。编号必须来自候选列表且类别一致；不确定返回空数组。'+json.dumps({"visual":visual,"plot":plot,"scene":m["scene"],"refs":candidates},ensure_ascii=False)}]) if refs else {}
        def pick(k,kind):
            v=matchrefs.get(k,[])
            return [refs[n-1]["id"] for n in v if type(n)==int and 1<=n<=len(refs) and refs[n-1]["kind"]==kind] if isinstance(v,list) else []
        try:score=max(0,min(1,float(m.get("confidence",0))))
        except (ValueError,TypeError):score=0
        out.append(dict(keyframe=a["id"],scene=str(m["scene"]),plot=plot,dialogue=dialogue,emotion=m["emotion"],prompt="",characters=pick("characters","character"),environments=pick("environments","environment"),confidence=score,order=assets.index(a)))
    return out
