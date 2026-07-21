PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Artefact Graph</title>
<script src="https://unpkg.com/cytoscape@3.30.4/dist/cytoscape.min.js"></script>
<style>html,body{height:100%;margin:0;font:14px system-ui;background:#0d1117;color:#e6edf3}header{height:58px;display:flex;align-items:center;gap:12px;padding:0 18px;border-bottom:1px solid #30363d}input{width:340px;padding:9px 12px;border:1px solid #30363d;border-radius:7px;background:#161b22;color:white}button{padding:9px 12px;background:#238636;border:0;border-radius:7px;color:white;cursor:pointer}.graph{height:calc(100% - 59px);position:relative}.columns{position:absolute;top:10px;left:0;right:0;display:grid;grid-template-columns:34% 33% 33%;z-index:1;pointer-events:none;color:#8b949e;text-align:center;font-size:12px;text-transform:uppercase;letter-spacing:.08em}.columns span{padding-bottom:8px;border-bottom:1px solid #21262d}#cy{height:100%}#detail{position:absolute;right:16px;top:75px;width:300px;max-height:70%;overflow:auto;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;display:none;z-index:2}pre{white-space:pre-wrap;word-break:break-word;color:#9da7b3}.status{margin-left:auto;color:#9da7b3}</style></head>
<body><header><strong>Artefact Graph</strong><input id="q" placeholder="Поиск по имени репозитория"><button onclick="load()">Найти</button><button onclick="refresh()">Обновить данные</button><span class="status" id="status"></span></header><div class="graph"><div class="columns"><span>Bitbucket: проекты / репозитории</span><span>TeamCity: build configurations</span><span>Билды / артефакты</span></div><div id="cy"></div></div><div id="detail"></div>
<script>
const colors={bb_project:'#8250df',repository:'#2f81f7',tc_project:'#d29922',build_configuration:'#f0883e',build:'#8b949e',container_image:'#3fb950',sbom:'#f778ba'};
let cy;
async function load(){
  const q=document.getElementById('q').value;
  const g=await fetch('/api/graph?q='+encodeURIComponent(q)).then(r=>r.json());
  const byId=Object.fromEntries(g.nodes.map(n=>[n.id,n]));
  const els=[...g.nodes.map(n=>({data:n,position:g.positions[n.id]})),...g.edges.map((e,i)=>({data:{id:'e'+i,...e,label:e.relation},classes:byId[e.source]?.kind==='tc_project'?'tc-containment':''}))];
  if(cy)cy.destroy();
  cy=cytoscape({container:document.getElementById('cy'),elements:els,style:[
    {selector:'node',style:{'background-color':e=>colors[e.data('kind')]||'#8b949e','label':'data(label)','color':'#e6edf3','font-size':11,'text-valign':'bottom','text-margin-y':7,'width':32,'height':32}},
    {selector:'edge',style:{'width':1.5,'line-color':'#484f58','target-arrow-color':'#484f58','target-arrow-shape':'triangle','curve-style':'straight','label':'data(label)','font-size':8,'color':'#8b949e','text-background-color':'#0d1117','text-background-opacity':1}},
    {selector:'edge.tc-containment',style:{'display':'none'}},
    {selector:':selected',style:{'border-width':3,'border-color':'#fff'}}
  ],layout:{name:'preset',fit:true,padding:70},minZoom:.25,maxZoom:2.5,wheelSensitivity:.18});
  cy.on('tap','node',e=>{const d=e.target.data(),box=document.getElementById('detail');box.style.display='block';box.innerHTML='<b>'+esc(d.label)+'</b><pre>'+esc(JSON.stringify(d,null,2))+'</pre>'});
  status();
}
async function status(){const s=await fetch('/api/status').then(r=>r.json());document.getElementById('status').textContent=s.mode+' · '+(s.lastScan?.message||'нет скана')}
async function refresh(){await fetch('/api/refresh',{method:'POST'});setTimeout(load,1200)}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')load()});load();
</script></body></html>'''
