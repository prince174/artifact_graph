import shutil
import subprocess

import pytest

from app.web import PAGE


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js required")
def test_incremental_update_keeps_existing_elements_and_clears_removed_data():
    code = PAGE.split("function syncElements", 1)[1].split("function render", 1)[0]
    script = r'''
const assert=require('node:assert/strict');
const elements=new Map();let added=0;
function add(item){const data={...item.data};const node={length:1,id:()=>data.id,
 data:value=>value?Object.assign(data,value):data,removeData:key=>delete data[key],
 position(value){this.pos=value}};elements.set(data.id,node);added++;return node;}
const cy={batch:f=>f(),add,getElementById:id=>elements.get(id)||{length:0},
 elements:()=>({filter:predicate=>({remove(){for(const [id,n] of elements)if(predicate(n))elements.delete(id)}})})};
const original=add({data:{id:'a',status:'RUNNING',stale:true}});
add({data:{id:'removed'}});
''' + "function syncElements" + code + r'''
syncElements([{data:{id:'a',status:'SUCCESS'},position:{x:1,y:2}},{data:{id:'new'}}]);
assert.equal(elements.get('a'),original);assert.equal(original.data().stale,undefined);
assert.equal(original.data().status,'SUCCESS');assert.equal(elements.has('removed'),false);
assert.equal(added,3);assert.deepEqual(original.pos,{x:1,y:2});
syncElements([{data:{id:'a',status:'SUCCESS'}},{data:{id:'new'}}]);assert.equal(added,3);
'''
    subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)
