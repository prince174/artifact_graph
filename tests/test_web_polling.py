import shutil
import subprocess

import pytest

from app.web import PAGE


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js required for JavaScript behavior test")
def test_minute_poll_reloads_running_and_completed_scans_and_retries():
    code = PAGE.split("let lastScanFinished=", 1)[1].split("async function refresh()", 1)[0]
    script = r"""
const assert=require('node:assert/strict');
let state={mode:'live',lastScan:{status:'running'}},loads=0,fail=false,hold=null;
const document={getElementById:()=>({textContent:'',classList:{toggle(){}}})};
const check=x=>x;
async function fetch(url){
  if(hold)await hold;
  if(fail)throw Error('offline');
  return url==='/api/status'?state:{bbProjects:[],tcProjects:[]};
}
function fillOptions(){}
async function loadSnapshots(){}
async function load(reset,checkStatus){assert.equal(reset,false);assert.equal(checkStatus,false);loads++;}
""" + "let lastScanFinished=" + code + r"""
(async()=>{
 await status();assert.equal(loads,0);
 await status(true);await status(true);assert.equal(loads,2);
 state.lastScan={status:'success',finishedAt:'done'};
 await status(true);assert.equal(loads,3);
 await status(true);assert.equal(loads,3);
 state.lastScan={status:'running'};fail=true;
 await assert.rejects(status(true));fail=false;
 await status(true);assert.equal(loads,4);
 let release;hold=new Promise(resolve=>release=resolve);
 const pending=status(true);await status(true);release();hold=null;await pending;
 assert.equal(loads,5);
})().catch(error=>{console.error(error);process.exit(1)});
"""
    subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)
