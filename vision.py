"""Optional multimodal image understanding and shot candidate ranking.

Send only to the provider configured by the user. No credentials in source.
"""
import base64,io,json,re
from pathlib import Path
import httpx
from PIL import Image

def unpack(content):
    if isinstance(content,list):
        content="".join(x.get("text","") for x in content if isinstance(x,dict))
    m=re.search(r"\{.*\}",content,re.S)
    if not m:raise ValueError("模型未返回 JSON 对象")
    return json.loads(m.group())

async def call(url,key,model,messages):
    async with httpx.AsyncClient(timeout=90) as client:
        resp=await client.post(url,headers={"Authorization":"Bearer "+key},json={"model":model,"temperature":0.1,"messages":messages})
    resp.raise_for_status()
    return unpack(resp.json()["choices"][0]["message"]["content"])

async def describe(url,key,model,path):
    with Image.open(path) as image:
        image=image.convert("RGB")
        image.thumbnail((1200,1200))
        out=io.BytesIO()
        image.save(out,format="JPEG",quality=75)
    b64=base64.b64encode(out.getvalue()).decode("ascii")
    return await call(url,key,model,[{"role":"user","content":[
       {"type":"text","text":"你是影视分镜视觉分析师。只依据可见画面输出JSON对象，字段characters(人物外观描述数组), environment(场景布局), action(人物动作), mood(情绪), composition(景别构图)。不要编造不可见事件和真实姓名。"},
       {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}
    ]}])

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
        cs=[x for x in m.get("characters",[]) if ids.get(x)=="character"]
        es=[x for x in m.get("environments",[]) if ids.get(x)=="environment"]
        try:score=max(0.,min(1.,float(m.get("confidence",0))))
        except (ValueError,TypeError):score=0.
        out.append({"id":m["shot_id"],"keyframe":k,"characters":cs,"environments":es,"confidence":score})
    return out
