/* Explicit plan review and delivery. A completed process is not visual acceptance. */
(function(root){
  function mount({api, $, esc, pollTask, mediaURL}){
    let editors = [], snapshot = null, revision = 0, working = false;
    const sheet = $('deliverySheet'), software = $('deliverySoftware'), engine = $('deliveryEngine');
    const confirm = $('deliveryConfirm'), execute = $('deliveryExecute'), review = $('deliveryReview');
    function sync(){
      execute.disabled = working || !snapshot?.ready || !confirm.checked;
      review.disabled = working;
      for(const node of [sheet, software, engine, confirm, $('aePublish')]) node.disabled = working;
      if(!snapshot?.ready) confirm.disabled = true;
      const canPublish = software.value === 'jianying' && engine.value === 'project';
      $('aePublish').disabled = working || !canPublish;
      if(!canPublish) $('aePublish').checked = false;
    }
    function invalidate(){
      revision++; snapshot = null; confirm.checked = false;
      $('deliverySummary').textContent = '请查看当前方案，检查内容后确认交付。';
      $('deliveryDetails').textContent = '';
      sync();
    }
    function setModes(){
      const item = editors.find(e=>e.id===software.value);
      const old = engine.value;
      engine.innerHTML = (item?.project_delivery === 'native_sheet_writer'
        ? '<option value="project">原生草稿（待目标软件验收）</option>' : '') +
        '<option value="portable">直接生成 MP4</option>';
      if([...engine.options].some(o=>o.value===old)) engine.value = old;
      $('deliveryCapability').textContent = item?.project_delivery === 'native_sheet_writer'
        ? '原生草稿可继续编辑；软件内播放和导出仍需验收。直接 MP4 支持范围由方案检查决定。'
        : '当前提供独立 MP4 成片；该软件原生工程和自动导出尚未接通。';
      invalidate();
    }
    async function load(){
      const data = await api('/api/editors');
      editors = data.editors;
      software.innerHTML = editors.map(e=>`<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('');
      setModes();
      const files = await api('/api/files?kind=sheets').catch(()=>({items:[]}));
      $('deliverySheets').innerHTML = files.items.filter(f=>f.name.endsWith('.json'))
        .map(f=>`<option value="${esc(f.path)}">${esc(f.name)}</option>`).join('');
    }
    async function reviewPlan(){
      invalidate();
      const current = revision, path = sheet.value.trim();
      if(!path){ $('deliverySummary').textContent = '请先生成方案，或选择已有决策单。'; return; }
      working = true; sync();
      $('deliverySummary').textContent = '正在检查方案与目标软件的兼容性…';
      try{
        const result = await api('/api/delivery-plan?'+new URLSearchParams({sheet:path, software:software.value, engine:engine.value}));
        if(current !== revision) return;
        snapshot = result;
        const s = result.summary;
        $('deliverySummary').innerHTML = `<div>${esc(s.project)} · ${s.clip_count} 个片段 · ${s.duration==null?'时长无效':Number(s.duration).toFixed(2)+' 秒'} · ${esc(s.width)}×${esc(s.height)}</div>`+
          `<div class="muted">关键帧 ${s.keyframe_count} 个；当前版本 ${esc(result.sheet_sha256.slice(0,12))}</div>`+
          (result.blockers.length?`<div class="verdict err">当前方案不能执行：<ul>${result.blockers.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:'<div class="verdict ok">方案检查通过，请展开详细方案后确认。</div>')+
          (result.warnings.length?`<div class="dim">提示：${result.warnings.map(esc).join('；')}</div>`:'');
        $('deliveryDetails').textContent = JSON.stringify(result.plan, null, 2);
      }catch(error){
        if(current === revision) $('deliverySummary').textContent = error.message;
      }finally{ working = false; sync(); }
    }
    function renderResult(report){
      const rendered = report.status === 'rendered_pending_visual_review';
      const project = report.status === 'project_generated_pending_host_verification';
      const title = rendered ? 'MP4 已生成，待画面复核' : project ? '草稿已生成，待目标软件验收' : '交付未完成';
      const artifacts = report.artifacts || {};
      let html = `<div class="verdict ${rendered||project?'ok':'err'}"><div>${title}</div></div>`;
      if(report.error) html += `<p>${esc(report.error)}</p>`;
      if(rendered && artifacts.video) html += `<video controls preload="metadata" style="width:100%;max-height:520px;background:#080a0e;border-radius:10px" src="${esc(mediaURL(artifacts.video))}"></video>`;
      if(rendered) html += '<p class="dim">已完成文件检查与完整解码。请检查节奏、字幕、动效和声音后再使用。</p>';
      if(project) html += '<p class="dim">文件检查通过；打开、播放与导出效果仍需在目标软件验收。</p>';
      html += Object.entries(artifacts).map(([name,path])=>`<p class="muted">${esc(name)}：<a target="_blank" rel="noopener" href="${esc(mediaURL(path))}">${esc(path)}</a></p>`).join('');
      html += `<details><summary>查看完整交付报告</summary><pre>${esc(JSON.stringify(report,null,2))}</pre></details>`;
      $('deliveryOutput').innerHTML = html;
    }
    async function deliver(){
      if(working || !snapshot?.ready || !confirm.checked) return;
      const approved = snapshot;
      working = true; sync();
      $('deliveryOutput').textContent = '正在交付，页面会显示后台进度…';
      try{
        const job = await api('/api/delivery', {method:'POST', body:{sheet:approved.sheet,
          software:approved.software, engine:approved.engine, approve_plan_sha256:approved.sheet_sha256,
          publish:$('aePublish').checked}});
        await pollTask(job.task, 'deliveryOutput');
        const report = await api(job.report_url);
        renderResult(report);
      }catch(error){ $('deliveryOutput').textContent = error.message; }
      finally{ working = false; invalidate(); }
    }
    sheet.oninput = invalidate;
    software.onchange = setModes;
    engine.onchange = invalidate;
    confirm.onchange = sync;
    review.onclick = reviewPlan;
    execute.onclick = deliver;
    sync();
    return {load, reviewPlan, setSheet(path){sheet.value=path; invalidate();}, renderResult};
  }
  root.AICutDelivery = {mount};
})(globalThis);
