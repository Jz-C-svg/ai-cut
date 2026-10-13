const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "aicut_token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";
let PICKED_STYLE = null, STYLES = [], LAST_SHEET = "";

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function setTokenState(){ $("tokenState").textContent = TOKEN ? "🔑 令牌已设置" : "未设置令牌"; }
function showAuth(message){
  $('authPanel').hidden=false;
  $('authMessage').textContent=message;
  $('authToken').focus();
}

async function api(path, opts={}, retryAuth=true){
  const res = await fetch(path,{method:opts.method||"GET",
    headers:Object.assign({"X-Auth-Token":TOKEN,"Content-Type":"application/json"},opts.headers||{}),
    body:opts.body?JSON.stringify(opts.body):undefined});
  const text = await res.text();
  let data; try{ data = JSON.parse(text); }catch{ data = {output:text}; }
  if(res.status===401 && retryAuth){
    showAuth('访问令牌无效，请输入当前主机服务的令牌。');
    throw new Error('需要重新连接服务');
  }
  if(!res.ok) throw new Error(data.error||("HTTP "+res.status));
  return data;
}
function show(id, text){ const el=$(id); el.style.display="block"; el.innerHTML=text; }
function busy(id, text){ show(id, `<div class="msg">${esc(text)}</div>`); }

// 通用可视化
function scoreRing(score, label=""){
  const r=56,c=2*Math.PI*r,off=c*(1-Math.min(score,100)/100);
  const color = score>=85?"#34d399":score>=70?"#5b8cff":score>=55?"#fbbf24":"#f87171";
  return `<div class="ringwrap"><div class="ring"><svg width="132" height="132" viewBox="0 0 132 132">
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="#1c2230" stroke-width="11"/>
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${off}"/></svg>
    <div class="val" style="color:${color}">${score}<small>${esc(label)}</small></div></div>`;
}
function bars(items){
  return `<div class="bars">` + items.map(i=>{
    const pct=Math.round(i.value/i.max*100);
    const klass = pct>=85?"fill ok":pct>=60?"fill warn":"fill err";
    return `<div class="bar"><span class="dim">${esc(i.name)}</span>
      <span class="track"><span class="${klass}" style="width:${pct}%"></span></span>
      <span style="text-align:right">${i.value}/${i.max}</span></div>`;
  }).join("")+`</div>`;
}
function radar(dims){
  const n=dims.length,cx=150,cy=140,R=100,levels=[0.25,0.5,0.75,1];
  const pt=(i,r)=>{const a=-Math.PI/2+i*2*Math.PI/n;return [cx+Math.cos(a)*r,cy+Math.sin(a)*r];};
  let g="";
  levels.forEach(l=>{ g+=`<polygon points="${dims.map((_,i)=>pt(i,R*l).join(",")).join(" ")}" fill="none" stroke="#232a38"/>`; });
  dims.forEach((_,i)=>{const [x,y]=pt(i,R); g+=`<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#232a38"/>`;});
  g+=`<polygon points="${dims.map((d,i)=>pt(i,R*(d.value/d.max)).join(",")).join(" ")}" fill="rgba(91,140,255,.28)" stroke="#5b8cff" stroke-width="2"/>`;
  dims.forEach((d,i)=>{const [lx,ly]=pt(i,R+18); g+=`<text x="${lx}" y="${ly}" fill="#8b95a7" font-size="11.5" text-anchor="middle">${esc(d.name)}</text>`;});
  return `<svg width="300" height="290" viewBox="0 0 300 290">${g}</svg>`;
}
function shotStrip(durs){
  const colors=["#5b8cff","#7c5bff","#22c55e","#38bdf8","#f59e0b","#f472b6","#14b8a6","#a855f7"];
  const sum=durs.reduce((a,b)=>a+b,0);
  return `<div style="display:flex;gap:3px;margin:10px 0">`+durs.map((d,i)=>
    `<div title="镜头 ${i+1}：${d} 秒" style="flex:${Math.max(d,0.3)};height:36px;border-radius:7px;background:${colors[i%colors.length]};display:grid;place-items:center;font-size:11px;font-weight:700;color:#0b0d12">${d}s</div>`).join("")+
    `</div><div class="muted">共 ${durs.length} 个镜头 · 总长 ${sum.toFixed(1)} 秒 · 平均 ${(sum/Math.max(durs.length,1)).toFixed(1)} 秒/镜头</div>`;
}

// 解析脚本输出
function parsePre(text){
  const t=text||"";
  const m=t.match(/总分：\*\*(\d+)\/100\*\*\s*[—-]\s*(.+)/);
  const dims=[...t.matchAll(/^\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|$/gm)].map(x=>({name:x[1],value:+x[2],max:+x[3]}));
  const sec=(name)=>{const i=t.indexOf(name); if(i<0) return ""; const rest=t.slice(i); const j=rest.indexOf("\n## "); return j<0?rest:rest.slice(0,j);};
  const notes=[...sec("## 观察到的短板").matchAll(/^-\s+(.+)$/gm)].map(x=>x[1]);
  const comp=[...sec("## 合规检查").matchAll(/^-\s+(.+)$/gm)].map(x=>x[1]).filter(x=>!x.includes("未命中"));
  const next=(t.match(/本轮建议只改：\*\*(.+?)\*\*/)||[])[1];
  return {total:m?+m[1]:null, grade:m?m[2]:"", dims, notes, comp, next};
}
function parseCompliance(text){
  const t=text||""; const m=t.match(/扫描\s*(\d+)\s*个文件，命中\s*(\d+)\s*处/);
  const hits=[]; const lines=t.split("\n");
  lines.forEach((ln,i)=>{
    const h=ln.match(/^\s*\[(.+?)\]\s*(.+?)\s*←\s*(.*)$/);
    if(h){ const sug=(lines[i+1]||"").match(/建议:\s*(.+)/); hits.push({cat:h[1],word:h[2],ctx:h[3],sug:sug?sug[1]:""}); }
  });
  return {scanned:m?+m[1]:0,count:m?+m[2]:0,hits};
}
function parsePost(text){
  const t=text||"";
  const base=[...t.matchAll(/^\|\s*(播放|完播|点赞|收藏|评论|涨粉)\s*\|\s*([\d.]+)\s*\|$/gm)].map(x=>({name:x[1],value:+x[2]}));
  const rows=[...t.matchAll(/^-\s+\*\*(.+?)\*\*(?::|：)(.*)$/gm)].map(x=>({title:x[1],tags:x[2].split(/[；;]/).map(s=>s.trim()).filter(Boolean)}));
  return {base,rows};
}

// 标签切换
const TABS=["overview","console","library","review","assets","research"];
document.querySelectorAll("nav button").forEach(btn=>{
  btn.onclick=()=>{
    document.querySelectorAll("nav button").forEach(b=>b.classList.remove("active"));
    document.querySelectorAll("section").forEach(s=>s.classList.remove("active"));
    btn.classList.add("active"); $(btn.dataset.tab).classList.add("active");
    const t=btn.dataset.tab;
    if(t==="console") loadConsole();
    if(t==="review") loadSelectors();
    if(t==="assets") loadAssets();
  };
});
function goTab(name){ document.querySelector(`nav button[data-tab="${name}"]`).click(); }

// 概览
const FLOW=[["识别素材","console"],["选风格","console"],["生成方案","console"],["确认交付","console"],["评估","review"],["联网比对","research"]];
async function loadOverview(){
  try{
    const h=await api("/api/health");
    $("health").textContent=(h.python_ok?"● 服务正常":"● 缺少运行环境")+(h.version?(" · "+String(h.version).slice(-6)):"");
    $("health").className="badge "+(h.python_ok?"ok":"err");
    const [styles,sheets,drafts,out,sys]=await Promise.all([
      api("/api/library?kind=styles").catch(()=>({styles:[]})),
      api("/api/files?kind=sheets").catch(()=>({items:[]})),
      api("/api/files?kind=drafts").catch(()=>({items:[]})),
      api("/api/files?kind=output").catch(()=>({items:[]})),
      api("/api/system").catch(()=>({})),
    ]);
    $("stats").innerHTML=[
      {n:styles.styles.length,l:"风格卡"},
      {n:sheets.items.filter(i=>i.name.endsWith(".json")).length,l:"决策单"},
      {n:drafts.items.length,l:"剪映草稿"},
      {n:out.items.length,l:"导出产物"},
      {n:(sys.disk_free_gb??"-"),l:"剩余空间 GB"},
    ].map(s=>`<div class="stat"><div class="n">${esc(s.n)}</div><div class="l">${esc(s.l)}</div></div>`).join("");
    const ollama=(sys.ollama||"").trim();
    $("sysinfo").innerHTML=`<div class="muted">项目</div><div>${esc(sys.project||"-")}</div>
      <div class="muted" style="margin-top:6px">本地模型</div><div>${ollama && !ollama.includes("不可用")?"运行中":"未运行"}</div>
      <div class="muted" style="margin-top:6px">磁盘可用</div><div>${esc(sys.disk_free_gb??"-")} GB</div>`;
  }catch(e){
    $("health").textContent=e.message.includes("令牌")?"● 令牌错误":"● 连接失败";
    $("health").className="badge err";
  }
  loadTasks();
}
$("flow").innerHTML=FLOW.map((f,i)=>`<span class="node" data-go="${f[1]}">${f[0]}</span>`+(i<FLOW.length-1?`<span class="arrow">→</span>`:"")).join("");
document.querySelectorAll("[data-go]").forEach(n=>n.onclick=()=>goTab(n.dataset.go));

async function loadTasks(){
  try{
    const d=await api("/api/tasks");
    if(!d.items.length){ $("tasklist").innerHTML=`<div class="msg">暂无任务</div>`; return; }
    $("tasklist").innerHTML=d.items.map(t=>{
      const cls=t.status==="done"?"ok":t.status==="failed"?"err":"warn";
      return `<div class="fileitem" data-task="${t.id}" data-target="tasklist"><div class="ico">${t.status==="done"?"✔":t.status==="failed"?"✖":"⏳"}</div>
        <div style="flex:1"><div>${esc(t.name)}</div><div class="muted">${t.status}</div></div><span class="badge ${cls}">${t.status}</span></div>`;
    }).join("");
    document.querySelectorAll("[data-task]").forEach(el=>el.onclick=()=>pollTask(el.dataset.task, el.dataset.target));
  }catch{}
}
async function pollTask(id, target){
  if(!id) throw new Error('后台未返回任务编号，请刷新服务后重试');
  const el=$(target);
  for(;;){
    const d=await api("/api/tasks/"+encodeURIComponent(id));
    el.innerHTML=`<div class="row" style="margin-bottom:10px"><span class="badge ${d.status==='done'?'ok':['failed','needs_review','cancelled'].includes(d.status)?'err':'warn'}">${esc(d.name)} · ${esc(d.status)}</span></div>
      <pre>${esc((d.log||[]).join("\n"))}</pre>`;
    if(d.status!=="running") return d;
    await new Promise(resolve=>setTimeout(resolve,1500));
  }
}
const deliveryUI = AICutDelivery.mount({api, $, esc, pollTask:(...args)=>pollTask(...args),
  mediaURL:path=>'/api/media?'+new URLSearchParams({path,token:TOKEN})});
$('connectService').onclick=()=>showAuth('访问令牌用于连接本机服务。');
$('authForm').onsubmit=async event=>{
  event.preventDefault();
  TOKEN=$('authToken').value.trim();
  if(!TOKEN) return;
  if($('rememberToken').checked) localStorage.setItem(TOKEN_KEY,TOKEN);
  else localStorage.removeItem(TOKEN_KEY);
  setTokenState(); $('authPanel').hidden=true;
  await loadOverview();
  await deliveryUI.load().catch(error=>{$('deliverySummary').textContent=error.message;});
  if($('authPanel').hidden) $('authToken').value='';
};
// ---------- AI 剪辑台 ----------
const A=["linear-gradient(90deg,#5b8cff,#7c5bff)","linear-gradient(90deg,#22c55e,#34d399)",
         "linear-gradient(90deg,#f59e0b,#fbbf24)","linear-gradient(90deg,#38bdf8,#5b8cff)",
         "linear-gradient(90deg,#f472b6,#a855f7)","linear-gradient(90deg,#14b8a6,#34d399)",
         "linear-gradient(90deg,#f97316,#fbbf24)","linear-gradient(90deg,#8b95a7,#cbd5e1)"];
function stepState(active){ ["step1","step2","step3","step4"].forEach((s,i)=>$(s).className="step"+(i<active?" done":i===active?" run":"")); }

async function loadConsole(){
  try{
    const [styles,vids,tr,bgm,all]=await Promise.all([
      api("/api/library?kind=styles").catch(()=>({styles:[]})),
      api("/api/files?kind=normalized").catch(()=>({items:[]})),
      api("/api/files?kind=transcripts").catch(()=>({items:[]})),
      api("/api/files?kind=work").catch(()=>({items:[]})),
      api("/api/files?kind=data").catch(()=>({items:[]})),
    ]);
    STYLES=styles.styles;
    $('aeStyleInfo').textContent = styles.warnings?.length
      ? `选一个风格看它会怎么剪。有 ${styles.warnings.length} 张风格卡缺少有效执行参数，已暂时跳过。`
      : '选一个风格看它会怎么剪。';
    $("aeVideo").innerHTML=`<option value="">（用手动路径）</option>`+
      vids.items.filter(i=>/\.(mp4|mov)$/i.test(i.name)).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
    $("aeTopicTr").innerHTML=`<option value="">（不提供）</option>`+
      tr.items.filter(i=>/\.json$/i.test(i.name)).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
    const beatsFiles=all.items.filter(i=>/^beats-.*\.json$/i.test(i.name));
    $("aeBeats").innerHTML=`<option value="">（不用节拍）</option>`+
      beatsFiles.map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
    const brollFiles=all.items.filter(i=>/broll-index/i.test(i.name));
    $("aeBroll").innerHTML=`<option value="">（不配 B-roll）</option>`+
      brollFiles.map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
    $("aeBgm").innerHTML=`<option value="">（无背景音乐）</option>`+
      all.items.filter(i=>/\.(mp3|wav|m4a)$/i.test(i.name)).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
    $("beatsAudio").innerHTML=`<option value="">（选素材）</option>`+
      vids.items.filter(i=>/\.(mp4|mov)$/i.test(i.name)).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
    // 平台选择按钮
    const PLATS=[["douyin","抖音"],["bilibili","B站"],["xiaohongshu","小红书"],["tiktok","TikTok"],["kuaishou","快手"],["wechat_channels","视频号"]];
    if(!window.PLAT) window.PLAT="douyin";
    $("platformRow").innerHTML=PLATS.map(([k,v])=>`<button class="ghost platbtn ${window.PLAT===k?"":""}" data-plat="${k}" style="${window.PLAT===k?"border-color:var(--brand);color:#fff":""}">${v}</button>`).join("");
    document.querySelectorAll(".platbtn").forEach(b=>b.onclick=()=>{
      window.PLAT=b.dataset.plat;
      document.querySelectorAll(".platbtn").forEach(x=>x.style.borderColor=x.dataset.plat===window.PLAT?"var(--brand)":"");
      document.querySelectorAll(".platbtn").forEach(x=>x.style.color=x.dataset.plat===window.PLAT?"#fff":"");
    });
    $("styleCards").innerHTML=STYLES.map((s,i)=>`
      <div class="stylecard ${PICKED_STYLE===s.id?"sel":""}" data-style="${s.id}" style="--accent:${A[i%A.length]}">
        <h4>${esc(s.名称)}</h4>
        <div class="chips">${(s.目标||[]).map(t=>`<span class="chip b">${esc(t)}</span>`).join("")}</div>
        <div class="chips"><span class="chip b">滤镜 ${esc(s.剪辑参数.滤镜||"自动")}</span><span class="chip g">花字 ${esc(s.剪辑参数.花字||"标准")}</span></div>
        <div class="muted">镜头 ${esc(s.剪辑参数.镜头最短秒)}-${esc(s.剪辑参数.镜头最长秒)}s · 转场 ${esc(s.剪辑参数.转场调用名||"硬切")}</div>
      </div>`).join("");
    document.querySelectorAll("[data-style]").forEach(c=>c.onclick=()=>{
      PICKED_STYLE=c.dataset.style;
      document.querySelectorAll("[data-style]").forEach(x=>x.classList.toggle("sel",x.dataset.style===PICKED_STYLE));
      showStyleInfo();
    });
  }catch(e){ console.warn(e); $('aeStyleInfo').textContent='风格加载失败：'+e.message; }
}
function showStyleInfo(){
  const s=STYLES.find(x=>x.id===PICKED_STYLE);
  if(!s){ $("aeStyleInfo").textContent="选一个风格看它会怎么剪。"; return; }
  const p=s.剪辑参数||{};
  $("aeStyleInfo").innerHTML=`<div class="chips">
    <span class="chip b">镜头 ${esc(p.镜头最短秒??"-")}-${esc(p.镜头最长秒??"-")} 秒</span>
    <span class="chip">转场 ${esc(p.转场调用名||"硬切")}</span>
    <span class="chip">滤镜 ${esc(p.滤镜||"无")} ${esc(p.滤镜强度||"")}</span>
    <span class="chip">开场特效 ${esc(p.开场特效||"无")}</span>
    <span class="chip">字幕 ≤${esc(p.字幕每行最多字??"-")} 字</span>
    <span class="chip">BGM ${esc(p.BGM音量??"-")}</span></div>`;
}
function currentVideo(){ return $("aePath").value.trim() || $("aeVideo").value; }

let BRAIN_ID="", BRAIN_CURSOR=0, BRAIN_TIMER=0, BRAIN_BUSY=false;
let BRAIN_FRAME_URL="";
let BRAIN_FRAME_AT=0;
function brainLog(ev){
  const box=$("brainEvents"), row=document.createElement("div");
  row.style.cssText="padding:6px 2px;border-bottom:1px solid var(--line)";
  const detail=(ev.kind==="vision"||ev.kind==="screen_vision")?JSON.stringify(ev.tag||ev.result||{}):ev.reason||ev.note||ev.value||"";
  if(ev.kind==="screen_frame") return;
  row.textContent=`${Number(ev.t||0).toFixed(2)}s · ${ev.kind}${detail?" · "+detail:""}`;
  if(box.textContent==="尚无观察事件") box.textContent="";
  box.prepend(row);
  while(box.children.length>80) box.lastElementChild.remove();
}
async function brainPoll(){
  if(!BRAIN_ID||BRAIN_BUSY)return;
  BRAIN_BUSY=true;
  try{
    const [s,e]=await Promise.all([
      api(`/api/video-brain/${BRAIN_ID}/status`),
      api(`/api/video-brain/${BRAIN_ID}/events?after=${BRAIN_CURSOR}`)
    ]);
    $("brainStatus").textContent=s.running?"观察中":"分析已停止";
    $("brainStatus").className="badge "+(s.running?"ok":"");
    $("brainProgress").textContent=s.source==="jianying_window"
      ?`剪映窗口 ${s.window?.title||"等待窗口"} · ${s.event_count||0} 个事件 · ${s.vision_pending?"本地视觉模型分析中":"视觉队列空闲"}${s.error?" · "+s.error:""}`
      :`已分析 ${Number(s.parsed_to||0).toFixed(1)} 秒素材 · ${s.event_count||0} 个事件 · 视觉标签 ${s.vision_tags||0} · ${s.vision_pending?"视觉模型分析中":"视觉队列空闲"}`;
    for(const ev of e.events||[]){ brainLog(ev); BRAIN_CURSOR=Math.max(BRAIN_CURSOR,Number(ev.seq||0)); }
    if(s.latest_frame&&Date.now()-BRAIN_FRAME_AT>2500){
      const img=$("brainFrame");
      const fr=await fetch(`/api/video-brain/${BRAIN_ID}/frame`,{headers:{"X-Auth-Token":TOKEN}});
      if(fr.ok){ const next=URL.createObjectURL(await fr.blob()); img.src=next; img.style.display="block"; if(BRAIN_FRAME_URL)URL.revokeObjectURL(BRAIN_FRAME_URL); BRAIN_FRAME_URL=next; BRAIN_FRAME_AT=Date.now(); }
    }
    $("brainStop").disabled=!s.running;
    $("brainStart").disabled=s.running;
    $("brainScreenStart").disabled=s.running;
    if(s.running) BRAIN_TIMER=setTimeout(brainPoll,800);
  }catch(err){
    $("brainStatus").textContent="连接中断";
    $("brainProgress").textContent=err.message;
    BRAIN_TIMER=setTimeout(brainPoll,2500);
  }finally{ BRAIN_BUSY=false; }
}
$("brainStart").onclick=async ()=>{
  const video=currentVideo();
  if(!video){ $("brainProgress").textContent="请先选择素材或填写允许目录内的视频路径。"; return; }
  clearTimeout(BRAIN_TIMER); BRAIN_ID=""; BRAIN_CURSOR=0; $("brainEvents").textContent="尚无观察事件";
  if(BRAIN_FRAME_URL){ URL.revokeObjectURL(BRAIN_FRAME_URL); BRAIN_FRAME_URL=""; } $("brainFrame").style.display="none";
  $("brainStatus").textContent="正在启动…"; $("brainStart").disabled=true;
  try{
    const d=await api("/api/video-brain/watch",{method:"POST",body:{video,interval:Number($("brainInterval").value||2),vision:$("brainVision").checked}});
    BRAIN_ID=d.id; $("brainStatus").textContent=`观察中 · ${d.video}`; $("brainStop").disabled=false; brainPoll();
  }catch(e){ $("brainStatus").textContent="启动失败"; $("brainProgress").textContent=e.message; }
  finally{ $("brainStart").disabled=!!BRAIN_ID; $("brainScreenStart").disabled=!!BRAIN_ID; }
};
$("brainScreenStart").onclick=async ()=>{
  clearTimeout(BRAIN_TIMER); BRAIN_ID=""; BRAIN_CURSOR=0; $("brainEvents").textContent="尚无观察事件";
  $("brainStatus").textContent="正在连接剪映…"; $("brainScreenStart").disabled=true;
  try{
    const d=await api("/api/video-brain/watch-screen",{method:"POST",body:{interval:1.5,vision:$("brainVision").checked}});
    BRAIN_ID=d.id; $("brainStatus").textContent="观察剪映窗口中"; $("brainStop").disabled=false; brainPoll();
  }catch(e){ $("brainStatus").textContent="启动失败"; $("brainProgress").textContent=e.message; }
  finally{ $("brainStart").disabled=!!BRAIN_ID; $("brainScreenStart").disabled=!!BRAIN_ID; }
};
$("brainStop").onclick=async ()=>{
  if(!BRAIN_ID)return;
  clearTimeout(BRAIN_TIMER);
  $("brainStop").disabled=true;
  try{ await api("/api/video-brain/stop",{method:"POST",body:{id:BRAIN_ID}}); await brainPoll(); $("brainStart").disabled=false; $("brainScreenStart").disabled=false; }
  catch(e){ $("brainProgress").textContent=e.message; $("brainStop").disabled=false; }
};

async function runAutoEdit(planOnly){
  const box=$("aeOut");
  const src=currentVideo();
  if(!src){ box.innerHTML=`<div class="verdict err"><div class="big">✖</div><div>请先选素材或填路径</div></div>`; return; }
  if(!PICKED_STYLE){ box.innerHTML=`<div class="verdict err"><div class="big">✖</div><div>请先选一个风格</div></div>`; return; }
  stepState(1);
  busy("aeOut","识别素材并生成待确认方案…");
  $("aeRun").disabled=true; $("aePlan").disabled=true;
  try{
    const d=await api("/api/autoedit",{method:"POST",body:{
      video:src, style:PICKED_STYLE,
      cover:$("aeCover").value.trim()||null, cta:$("aeCta").value.trim()||null,
      bgm:($("aeBgmPath").value.trim()||$("aeBgm").value)||null,
      beats:$("aeBeats").value||null, broll_index:$("aeBroll").value||null,
      smart:$("aeSmart").checked, drop_blurry:$("aeDrop").checked,
      keep_pauses:$("aeKeep").checked, publish:false, plan_only:true, async:true}});
    const t=await pollTask(d.task,"aeOut");
    if(t.status!=="done") throw new Error('方案生成未完成，请查看后台日志');
    const report=await api(d.report_url);
    if(report.state!=="planned" || !report.artifacts?.working_sheet) throw new Error('方案尚未就绪，请查看任务报告');
    LAST_SHEET=report.artifacts.working_sheet;
    deliveryUI.setSheet(LAST_SHEET);
    stepState(2);
    if(!planOnly) await deliveryUI.reviewPlan();
  }catch(e){ box.innerHTML+=`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`; }
  finally{ $("aeRun").disabled=false; $("aePlan").disabled=false; }
}
$("aeRun").onclick=()=>runAutoEdit(false);
$("aePlan").onclick=()=>runAutoEdit(true);
$("aeUnderstand").onclick=async ()=>{
  const src=currentVideo();
  if(!src){ show("aeOut",`<div class="verdict err"><div class="big">✖</div><div>请先选素材</div></div>`); return; }
  stepState(0); busy("aeOut","识别中（信号 + 文本 + 视觉）…");
  try{ const d=await api("/api/understand",{method:"POST",body:{video:src,frames:6}}); await pollTask(d.task,"aeOut"); }
  catch(e){ show("aeOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); }
};
// 包装任务轮询：从日志里记住最新决策单，供导出使用
const _poll = pollTask;
pollTask = async function(id, target){
  const d = await _poll(id, target);
  const log = (d.log||[]).join("\n");
  const m = log.match(/\[决策单\]\s+([^\r\n]+)/);
  if(m){ LAST_SHEET = m[1].trim(); }
  return d;
};

// ---------- 内容库 ----------
async function loadLibrary(kind){
  const box=$("libview"); box.innerHTML=`<div class="msg">加载中…</div>`;
  const data=await api("/api/library?kind="+kind);
  if(kind==="styles"){
    box.innerHTML=`<div class="stylegrid">`+data.styles.map((s,i)=>`
      <div class="stylecard" style="--accent:${A[i%A.length]}"><h4>${esc(s.名称)}</h4>
        <div class="muted">${esc(s.id)}</div>
        <div class="chips">${(s.目标||[]).map(t=>`<span class="chip b">${esc(t)}</span>`).join("")}</div>
        <div class="muted">镜头 ${esc(s.剪辑参数.镜头最短秒)}-${esc(s.剪辑参数.镜头最长秒)}s · 转场 ${esc(s.剪辑参数.转场调用名||"硬切")} · 字幕≤${esc(s.剪辑参数.字幕每行最多字)}字</div>
        <div class="chips" style="margin-top:8px">${(s.钩子类型||[]).map(h=>`<span class="chip g">${esc(h)}</span>`).join("")}</div>
        <div class="muted">注意：${esc((s.常见失误||[]).slice(0,2).join("；"))}</div></div>`).join("")+`</div>`;
  } else if(kind==="hooks"){
    box.innerHTML=`<div class="card"><h3><span class="dot"></span>黄金开头</h3><div class="stylegrid">`+
      data.黄金开头模板.map((h,i)=>`<div class="stylecard" style="--accent:${A[i%A.length]}"><h4>${esc(h.名称)}</h4>
        <div class="dim">${esc(h.原理)}</div>
        <div style="margin-top:8px;padding:10px;border:1px dashed var(--line);border-radius:10px">${esc(h.模板)}</div></div>`).join("")+
      `</div></div><div class="card" style="margin-top:14px"><h3><span class="dot"></span>钩子大类</h3>
      <table><tr><th>类别</th><th>模板</th><th>注意</th></tr>`+
      data.钩子大类.map(h=>`<tr><td>${esc(h.类别)}</td><td>${esc((h.模板||[]).join(" / "))}</td><td class="muted">${esc(h.注意)}</td></tr>`).join("")+`</table></div>`;
  } else if(kind==="structures"){
    box.innerHTML=`<div class="card"><h3><span class="dot"></span>最小模型</h3>
      <div class="flow">${["钩","展","收"].map(k=>`<span class="node">${k}：${esc(data.最小模型[k])}</span>`).join('<span class="arrow">→</span>')}</div></div>
      <div class="card" style="margin-top:14px"><h3><span class="dot"></span>结构骨架</h3><div class="stylegrid">`+
      data.骨架库.map((s,i)=>`<div class="stylecard" style="--accent:${A[i%A.length]}"><h4>${esc(s.名称)}</h4>
        <div class="chips">${(s.适用||[]).map(a=>`<span class="chip b">${esc(a)}</span>`).join("")}</div>
        <div class="flow">${(s.步骤||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('<span class="arrow">→</span>')}</div></div>`).join("")+`</div></div>`;
  } else {
    box.innerHTML=`<div class="stylegrid">`+data.选题方法.map((t,i)=>`
      <div class="stylecard" style="--accent:${A[i%A.length]}"><h4>${esc(t.方法)}</h4><div class="dim">${esc(t.做法)}</div>
      <div class="chips">${(t.适配||[]).map(a=>`<span class="chip y">${esc(a)}</span>`).join("")}</div></div>`).join("")+
      `</div><div class="card" style="margin-top:14px"><h3><span class="dot"></span>筛选标准</h3>
      <ul class="dim" style="margin:0;padding-left:20px">${data.筛选标准.map(s=>`<li>${esc(s)}</li>`).join("")}</ul></div>`;
  }
}
document.querySelectorAll("[data-lib]").forEach(b=>b.onclick=()=>loadLibrary(b.dataset.lib));

// ---------- 评估与合规 ----------
async function loadSelectors(){
  const [sheets,tr,data]=await Promise.all([
    api("/api/files?kind=sheets").catch(()=>({items:[]})),
    api("/api/files?kind=transcripts").catch(()=>({items:[]})),
    api("/api/files?kind=data").catch(()=>({items:[]})),
  ]);
  $("evalSheet").innerHTML=sheets.items.filter(i=>i.name.endsWith(".json")).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
  $("cmpFile").innerHTML=tr.items.filter(i=>/\.(json|txt|srt)$/.test(i.name)).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
  $("evalData").innerHTML=data.items.filter(i=>/\.(csv|xlsx)$/.test(i.name)).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
}
function renderCompliance(out){
  const r=parseCompliance(out);
  const byCat={}; r.hits.forEach(h=>byCat[h.cat]=(byCat[h.cat]||0)+1);
  const max=Math.max(1,...Object.values(byCat));
  const head=r.count===0
    ? `<div class="verdict ok"><div class="big">✔</div><div><div>未命中已知风险表达</div><div class="muted">扫描 ${r.scanned} 个文件</div></div></div>`
    : `<div class="verdict err"><div class="big">⚠</div><div><div>命中 ${r.count} 处风险表达</div><div class="muted">扫描 ${r.scanned} 个文件</div></div></div>`;
  const catBars=Object.entries(byCat).map(([k,v])=>`<div class="bar"><span class="dim">${esc(k)}</span>
      <span class="track"><span class="fill err" style="width:${Math.round(v/max*100)}%"></span></span>
      <span style="text-align:right">${v}</span></div>`).join("");
  const hits=r.hits.map(h=>`<div class="hit"><span class="cat">${esc(h.cat)}</span>
      <div style="margin-top:6px"><b>${esc(h.word)}</b> <span class="muted">${esc(h.ctx)}</span>
      <div class="muted" style="margin-top:4px">建议：${esc(h.sug)}</div></div></div>`).join("");
  return head + (catBars?`<div class="bars" style="margin-top:12px">${catBars}</div>`:"") + hits;
}
$("cmpRun").onclick=async ()=>{ busy("cmpOut","检测中…"); try{ const d=await api("/api/compliance",{method:"POST",body:{text:$("cmpText").value}}); show("cmpOut",renderCompliance(d.output)); }catch(e){ show("cmpOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); } };
$("cmpFileRun").onclick=async ()=>{ busy("cmpOut","检测中…"); try{ const d=await api("/api/compliance",{method:"POST",body:{path:$("cmpFile").value}}); show("cmpOut",renderCompliance(d.output)); }catch(e){ show("cmpOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); } };
$("evalPre").onclick=async ()=>{
  busy("evalPreOut","评分中…");
  try{
    const d=await api("/api/evaluate/pre",{method:"POST",body:{sheet:$("evalSheet").value}});
    const p=parsePre(d.output);
    show("evalPreOut", `<div class="card">${scoreRing(p.total??0,p.grade)}${bars(p.dims)}</div>
      <div class="card" style="margin-top:12px">${radar(p.dims)}</div>
      <div class="card" style="margin-top:12px"><h3><span class="dot"></span>短板</h3>
      ${p.notes.length?`<ul class="dim" style="margin:0;padding-left:20px">${p.notes.map(n=>`<li>${esc(n)}</li>`).join("")}</ul>`:`<div class="msg">未发现明显短板</div>`}
      ${p.next?`<div class="chips" style="margin-top:10px"><span class="chip y">本轮只改：${esc(p.next)}</span></div>`:""}</div>`);
  }catch(e){ show("evalPreOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); }
};
$("evalPost").onclick=async ()=>{
  busy("evalPostOut","诊断中…");
  try{
    const d=await api("/api/evaluate/post",{method:"POST",body:{data:$("evalData").value}});
    const p=parsePost(d.output); const max=Math.max(1,...p.base.map(b=>b.value));
    show("evalPostOut", `<div class="card"><h3><span class="dot"></span>自身基线</h3><div class="bars">`+
      p.base.map(b=>`<div class="bar"><span class="dim">${esc(b.name)}</span><span class="track"><span class="fill" style="width:${Math.round(b.value/max*100)}%"></span></span><span style="text-align:right">${b.value}</span></div>`).join("")+
      `</div></div><div class="card" style="margin-top:12px"><h3><span class="dot"></span>逐条诊断</h3>`+
      p.rows.map(r=>`<div class="fileitem" style="cursor:default"><div class="ico">${r.tags.length?"⚠":"✔"}</div>
        <div style="flex:1"><div>${esc(r.title)}</div><div class="chips">${r.tags.map(t=>`<span class="chip y">${esc(t)}</span>`).join("")}</div></div></div>`).join("")+`</div>`);
  }catch(e){ show("evalPostOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); }
};

// ---------- 素材与产物 ----------
const ICON=(n)=>/\.json$/.test(n)?"🧾":/\.(srt|txt)$/.test(n)?"📄":/\.(csv|xlsx)$/.test(n)?"📊":/\.(mp4|mov)$/.test(n)?"🎬":"📁";
async function fillAssetSelects(){
  try{
    const [vids, all] = await Promise.all([
      api("/api/files?kind=normalized").catch(()=>({items:[]})),
      api("/api/files?kind=work").catch(()=>({items:[]})),
    ]);
    const cur = $("beatsAudio").value;
    $("beatsAudio").innerHTML = `<option value="">（选素材）</option>` +
      vids.items.filter(i=>/\.(mp4|mov)$/i.test(i.name)).map(i=>`<option value="${esc(i.path)}">${esc(i.name)}</option>`).join("");
    if(cur) $("beatsAudio").value = cur;
  }catch(e){}
}

const MEDIA_RE=/\.(mp4|mov|webm|m4v|mp3|wav|m4a|jpg|jpeg|png|gif)$/i;
async function loadAssets(kind, sub){
  fillAssetSelects();
  const k = kind || window.__ASSET_KIND || "sheets";
  window.__ASSET_KIND = k;
  const d=await api("/api/files?kind="+k+(sub?("&sub="+encodeURIComponent(sub)):""));
  if(!d.items.length){ $("filelist").innerHTML=`<div class="msg">这一类还没有文件</div>`; return; }
  $("filelist").innerHTML=d.items.map(i=>`<div class="fileitem" data-open="${esc(i.path)}" data-dir="${i.dir?1:0}" data-name="${esc(i.name)}">
    <div class="ico">${i.dir?"📁":ICON(i.name)}</div>
    <div style="flex:1"><div>${esc(i.name)}</div><div class="muted">${i.mtime||""} ${i.size?`· ${Math.round(i.size/1024)} KB`:""}</div></div></div>`).join("");
  document.querySelectorAll("[data-open]").forEach(el=>el.onclick=async ()=>{
    const path=el.dataset.open, name=el.dataset.name;
    if(el.dataset.dir==="1"){ loadAssets(window.__ASSET_KIND, name); return; }
    if(MEDIA_RE.test(name)){
      const url="/api/media?path="+encodeURIComponent(path)+"&token="+encodeURIComponent(TOKEN);
      if(/\.(mp4|mov|webm|m4v)$/i.test(name)){
        $("fileview").innerHTML=`<video src="${url}" controls style="width:100%;max-height:520px;border-radius:10px;background:#000"></video>
          <div class="muted" style="margin-top:8px">${esc(name)} · <a href="${url}" target="_blank">在新窗口打开</a></div>`;
      } else if(/\.(jpg|jpeg|png|gif)$/i.test(name)){
        $("fileview").innerHTML=`<img src="${url}" style="width:100%;border-radius:10px">`;
      } else {
        $("fileview").innerHTML=`<audio src="${url}" controls style="width:100%"></audio>`;
      }
      return;
    }
    try{ const f=await api("/api/file?path="+encodeURIComponent(path)); $("fileview").textContent=f.text; }
    catch(e){ $("fileview").textContent="读取失败："+e.message; }
  });
}
document.querySelectorAll("[data-file]").forEach(b=>b.onclick=()=>loadAssets(b.dataset.file));
$("biRun").onclick=async ()=>{
  const dir=$("biDir").value.trim();
  if(!dir){ busy("assetOut","请先填素材目录"); return; }
  busy("assetOut","建立 B-roll 索引（逐个镜头调用视觉模型）…");
  try{ const d=await api("/api/broll-index",{method:"POST",body:{dir}}); await pollTask(d.task,"assetOut"); }
  catch(e){ show("assetOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); }
};
$("beatsRun").onclick=async ()=>{
  const audio=$("beatsAudio").value;
  if(!audio){ busy("assetOut","请先选音频或视频"); return; }
  busy("assetOut","分析节拍…");
  try{ const d=await api("/api/beats",{method:"POST",body:{audio}}); await pollTask(d.task,"assetOut"); }
  catch(e){ show("assetOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); }
};

// ---------- 联网比对 ----------
$("aeResearch").onclick=async ()=>{
  const topic=$("aeTopic").value.trim();
  if(!topic){ busy("aeResearchOut","请先填本片主题"); return; }
  busy("aeResearchOut","联网检索中（通常 1-3 分钟）…");
  try{ const d=await api("/api/research",{method:"POST",body:{topic,transcript:$("aeTopicTr").value||null}}); await pollTask(d.task,"aeResearchOut"); }
  catch(e){ show("aeResearchOut",`<div class="verdict err"><div class="big">✖</div><div>${esc(e.message)}</div></div>`); }
};

// ---------- 启动 ----------
(async function boot(){
  setTokenState();
  stepState(-1);
  if(!TOKEN) showAuth('请输入主机服务的访问令牌，连接后开始工作。');
  else{
    await loadOverview();
    await deliveryUI.load().catch(e=>{$('deliverySummary').textContent=e.message;});
  }
  setInterval(()=>{if(TOKEN) loadOverview();},30000);
})();
