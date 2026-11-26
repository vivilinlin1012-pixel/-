"use strict";
const $=s=>document.querySelector(s),esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let pid="",data=null,page="board",selected="";
const pic=id=>"/api/projects/"+pid+"/assets/"+id+"/file";
async function req(path,opts={}){let r=await fetch(path,opts);if(!r.ok){let x=await r.json().catch(()=>({detail:r.statusText}));throw Error(x.detail||"请求失败")}return r.json()}
function info(s){$("#notice").textContent=s;$("#status").textContent=s}
function json(body){return {headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}}
async function projects(){let ps=await req("/api/projects");$("#projects").innerHTML=ps.map(p=>'<button class="project '+(p.id===pid?"active":"")+'" data-id="'+p.id+'">'+esc(p.title)+"</button>").join("");document.querySelectorAll(".project").forEach(b=>b.onclick=()=>load(b.dataset.id));if(!pid&&ps.length)await load(ps[0].id)}
async function load(id){pid=id;selected="";data=await req("/api/projects/"+pid);render();await projectsList();watch(id)}
async function projectsList(){let ps=await req("/api/projects");$("#projects").innerHTML=ps.map(p=>'<button class="project '+(p.id===pid?"active":"")+'" data-id="'+p.id+'">'+esc(p.title)+"</button>").join("");document.querySelectorAll(".project").forEach(b=>b.onclick=()=>load(b.dataset.id))}
async function reload(){data=await req("/api/projects/"+pid);render()}
function setPage(p){page=p;document.querySelectorAll("nav button").forEach(b=>b.classList.toggle("active",b.dataset.page===p));render()}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>setPage(b.dataset.page));
$("#addProject").onclick=async()=>{let title=prompt("请输入新短片名称");if(!title?.trim())return;try{let p=await req("/api/projects",{method:"POST",...json({title:title.trim()})});await load(p.id);info("已创建项目，请导入剧本和图片")}catch(e){info(e.message)}};
$("#parse").onclick=async()=>{if(!pid)return;try{let x=await req("/api/projects/"+pid+"/parse",{method:"POST"});await reload();setPage("board");info("已创建 "+x.shots_created+" 个剧情镜头")}catch(e){info(e.message)}};
let polling=false;
async function watch(project){
 if(polling)return;polling=true;
 try{while(pid===project){
 let j=await req("/api/projects/"+project+"/job");
 if(j.state==="idle")break;
 info(j.done+"/"+j.total+" · "+j.message+(j.errors?.length?" · "+j.errors.map(e=>e.label+": "+e.error).join("；"):""));
 if(j.state!=="running"){await reload();break}
 await new Promise(r=>setTimeout(r,2000));
 }}catch(e){info("进度连接中断，可刷新恢复："+e.message)}finally{polling=false;$("#match").disabled=false}
}
async function start(target="",retry=false){
 if(!pid)return;$("#match").disabled=true;
 try{await req("/api/projects/"+pid+"/match?target="+encodeURIComponent(target)+"&retry="+retry,{method:"POST"});await watch(pid)}catch(e){info(e.message);$("#match").disabled=false}
}
$("#match").onclick=()=>start();
$("#retry").onclick=()=>start("",true);
$("#testModel").onclick=async()=>{info("正在测试模型");try{await req("/api/ai-test",{method:"POST"});info("模型文本接口测试通过")}catch(e){info(e.message)}};
$("#export").onclick=()=>{if(pid)location.href="/api/projects/"+pid+"/export"};
function render(){document.querySelector(".app").classList.toggle("boardmode",page==="board");let names={board:"智能故事板",script:"剧本管理",character:"人物定妆照",environment:"环境素材库",keyframe:"分镜关键图"};$("#title").textContent=(data?.project.title||"未创建项目")+" / "+names[page];if(!data){$("#content").innerHTML='<p class="muted">点击右上角创建项目，开始管理短片。</p>';return}if(page==="board")board();else if(page==="script")script();else assets(page)}
function board(){let s=data.assets.filter(a=>a.kind==="keyframe").map(a=>data.shots.find(x=>x.keyframe===a.id)||{id:"pending-"+a.id,keyframe:a.id,scene:a.label,plot:"等待AI分析",status:"pending",confidence:0});s.sort((a,b)=>(a.ord??999999)-(b.ord??999999));if(!s.some(x=>x.id===selected))selected=s[0]?.id||"";$("#content").innerHTML=s.length?'<p class="muted">每张关键图对应一个制作镜头。点击查看自动分析结果。</p><div class="grid">'+s.map((x,i)=>'<div class="tile '+(selected===x.id?"selected":"")+'" data-shot="'+x.id+'">'+(x.keyframe?'<img class="preview" src="'+pic(x.keyframe)+'">':'<div class="preview">▧</div>')+'<article><div class="label">SHOT '+String(i+1).padStart(3,"0")+' · '+esc(x.plot&&x.emotion?x.status:"待分析")+'</div><b>'+esc(x.scene.slice(0,35))+'</b><p>'+esc(x.plot.slice(0,100))+'</p><small class="muted">'+(x.confidence?Math.round(x.confidence*100)+"% 候选置信度":"尚未匹配")+'</small></article></div>').join("")+"</div>":'<p class="muted">请先导入关键图，再点击智能匹配。</p>';document.querySelectorAll("[data-shot]").forEach(b=>b.onclick=()=>{selected=b.dataset.shot;board()});inspector()}
function script(){$("#inspector").innerHTML='<h2>剧本导入</h2><p class="muted">支持 TXT、DOCX 和带文本层的 PDF。扫描版需先 OCR。剧情和台词将在故事板中分开编辑。</p>';$("#content").innerHTML='<h2>剧本文字</h2><input id="scriptUpload" type="file" accept=".txt,.docx,.pdf"><button id="uploadScript">导入文件</button><p class="muted">或直接在下面粘贴、编辑原文：</p><textarea class="large" id="text"></textarea><p><button class="accent" id="saveText">保存剧本</button></p>';$("#text").value=data.project.script||"";$("#saveText").onclick=async()=>{try{await req("/api/projects/"+pid+"/script",{method:"POST",...json({text:$("#text").value})});await reload();info("剧本已保存")}catch(e){info(e.message)}};$("#uploadScript").onclick=async()=>{const f=$("#scriptUpload").files[0];if(!f)return;let form=new FormData();form.append("file",f);try{await req("/api/projects/"+pid+"/script-file",{method:"POST",body:form});await reload();info("剧本文件导入成功")}catch(e){info(e.message)}}}
function assets(kind){
const list=data.assets.filter(a=>a.kind===kind);
$("#inspector").innerHTML='<h2>素材管理</h2><p class="muted">支持批量图片和整个文件夹（含子文件夹）。自动跳过非图片文件。素材保存在本地。</p>';
$("#content").innerHTML='<h2>导入素材</h2><div class="notice"><button id="pickFiles">＋ 选择图片</button> <button class="accent" id="pickFolder">＋ 导入文件夹</button><input id="files" type="file" accept=".jpg,.jpeg,.png,.webp" multiple hidden><input id="folder" type="file" webkitdirectory multiple hidden><p id="selection" class="muted">尚未选择素材</p><button id="uploadAssets" disabled>确认导入</button></div><p class="muted">已导入 '+list.length+' 张</p><div class="grid">'+list.map(a=>'<div class="tile"><img class="thumb" loading="lazy" src="'+pic(a.id)+'"><article>'+esc(a.label)+'</article></div>').join("")+"</div>";
let pending=[];const projectId=pid;
function choose(files){const all=Array.from(files);pending=all.filter(f=>/\.(jpe?g|png|webp)$/i.test(f.name)&&f.size<=25*1024*1024);pending.sort((a,b)=>(a.webkitRelativePath||a.name).localeCompare(b.webkitRelativePath||b.name,"zh-CN",{numeric:true}));$("#selection").textContent="已选择 "+pending.length+" 张图片，跳过 "+(all.length-pending.length)+" 个不支持或超过25MB的文件。";$("#uploadAssets").disabled=!pending.length}
$("#pickFiles").onclick=()=>$("#files").click();$("#pickFolder").onclick=()=>$("#folder").click();
$("#files").onchange=e=>choose(e.target.files);$("#folder").onchange=e=>choose(e.target.files);
$("#uploadAssets").onclick=async()=>{const batch=pending.slice();if(!batch.length)return;let done=0,failed=0;for(const id of ["pickFiles","pickFolder","uploadAssets"])$("#"+id).disabled=true;
for(const f of batch){let form=new FormData();form.append("kind",kind);form.append("files",f);try{await req("/api/projects/"+projectId+"/assets",{method:"POST",body:form});done++}catch(e){failed++}info("正在导入 "+(done+failed)+"/"+batch.length+" 张，失败 "+failed+" 张")}
if(pid===projectId)await reload();info("导入完成：成功 "+done+" 张，失败 "+failed+" 张");};
}
function choices(kind,values){return '<div class="refgrid" data-kind="'+kind+'">'+data.assets.filter(a=>a.kind===kind).sort((a,b)=>Number(values.includes(b.id))-Number(values.includes(a.id))).map(a=>'<label class="'+(values.includes(a.id)?"matched":"unmatched")+'"><input type="checkbox" value="'+a.id+'" '+(values.includes(a.id)?"checked":"")+'> <img src="'+pic(a.id)+'"> '+esc(a.label)+'</label>').join("")+"</div>"}
function inspector(){let s=data.shots.find(x=>x.id===selected);if(!s&&selected.startsWith("pending-")){$("#inspector").innerHTML='<h2>关键图制作工作区</h2><img class="img" src="'+pic(selected.slice(8))+'"><p class="muted">点击下方按钮测试当前关键图。</p><button id="single" class="accent">分析当前关键图</button>';$("#single").onclick=()=>start(selected.slice(8));return}if(!s){$("#inspector").innerHTML='<h2>镜头检查器</h2><p class="muted">选择一个镜头，编辑剧情、台词、情绪和参考素材。</p>';return}let keys=data.assets.filter(a=>a.kind==="keyframe");$("#inspector").innerHTML='<h2>镜头制作工作区</h2><button id="single">重新分析当前图</button> <button id="toggleRefs">显示/隐藏其他参考图</button>'+(s.keyframe?'<img class="img" src="'+pic(s.keyframe)+'">':"")+'<label class="field">所属场次</label><input id="scene"><label class="field">剧情</label><textarea id="plot"></textarea><label class="field">原文台词</label><textarea id="dialogue"></textarea><label class="field">人物情绪</label><textarea id="emotion"></textarea><label class="field">关联关键图</label><select id="key" disabled><option value="">未关联</option>'+keys.map(a=>'<option value="'+a.id+'">'+esc(a.label)+'</option>').join("")+'</select><label class="field">人物定妆照</label>'+choices("character",s.characters)+'<label class="field">环境参考图</label>'+choices("environment",s.environments)+'<label class="field">视频生成指令</label><textarea id="videoPrompt"></textarea><p><button id="promptBtn">复制视频指令</button> <button class="accent" id="save">保存</button></p><button id="approve">✓ 保存并确认</button>';
$("#single").onclick=()=>start(s.keyframe);$("#toggleRefs").onclick=()=>$("#inspector").classList.toggle("showAll");
for(let k of ["scene","plot","dialogue","emotion"])$("#"+k).value=s[k];$("#key").value=s.keyframe;$("#videoPrompt").value=s.prompt;
$("#promptBtn").onclick=async()=>{try{await navigator.clipboard.writeText($("#videoPrompt").value);info("已复制视频指令")}catch(e){info("复制失败，请手动复制")}};
async function save(approved){let refs=kind=>[...document.querySelectorAll('[data-kind="'+kind+'"] input:checked')].map(x=>x.value);let body={scene:$("#scene").value,plot:$("#plot").value,dialogue:$("#dialogue").value,emotion:$("#emotion").value,prompt:$("#videoPrompt").value,keyframe:$("#key").value,characters:refs("character"),environments:refs("environment"),status:approved?"approved":"review"};try{await req("/api/projects/"+pid+"/shots/"+s.id,{method:"PATCH",...json(body)});await reload();info(approved?"镜头已确认":"镜头已保存")}catch(e){info(e.message)}}
$("#save").onclick=()=>save(false);$("#approve").onclick=()=>save(true)}
$("#config").onclick=()=>{const dialog=document.createElement("dialog");dialog.style.cssText="width:min(500px,90vw);background:#20212a;color:#eee;border:1px solid #555;border-radius:10px;padding:24px";dialog.innerHTML='<h2>视觉AI模型设置</h2><p class="muted">支持 OpenAI 兼容视觉接口。图片和剧本会发送到所选模型服务。默认使用本地 Qwen；自定义设置仅在本次服务进程内保存。</p><label class="field">API Base URL</label><input id="aiBase" value="http://127.0.0.1:11434/v1"><label class="field">模型名称</label><input id="aiModel" value="qwen2.5vl:7b"><label class="field">API Key（本地模型可留空）</label><input id="aiKey" type="password"><p><button id="aiSave" class="accent">保存设置</button> <button id="aiClose">取消</button></p>';document.body.append(dialog);dialog.showModal();dialog.querySelector("#aiClose").onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();dialog.querySelector("#aiSave").onclick=async()=>{try{await req("/api/ai-config",{method:"POST",...json({base:dialog.querySelector("#aiBase").value,model:dialog.querySelector("#aiModel").value,key:dialog.querySelector("#aiKey").value})});dialog.close();info("模型设置已保存，尚未验证连接")}catch(e){info(e.message)}}};
projects().catch(e=>info("连接出错："+e.message));render();
