import assert from 'node:assert/strict';
import {after, test} from 'node:test';
import {createServer} from 'vite';
const server = await createServer({server:{middlewareMode:true},appType:'custom'});
after(()=>server.close());
const {buildGraphModel, dotMixed}=await server.ssrLoadModule('/src/graphDot.ts');
const entries=[
  {id:'input',title:'Input',kind:'theorem',lean_status:'sorry',deps:[]},
  {id:'proof',title:'Proof',kind:'theorem',lean_status:'lean_ok',deps:[{id:'input',type:'uses'}]},
  {id:'library',title:'Library',kind:'theorem',lean_status:'mathlib_ok',deps:[]},
  {id:'unbound',title:'Unbound',kind:'theorem',lean_status:'empty',deps:[]},
];
const data={entries, loc:{input:0,proof:1,library:1,unbound:1},chapters:[{num:'1',title:'Inputs'},{num:'2',title:'Deductions'}]};
test('local chapter progress counts bindings while preserving admitted prerequisite states',()=>{
  const closure=buildGraphModel(data);
  const local=buildGraphModel({...data,graphProgress:'local'});
  assert.equal(closure.chapters[1].done,1);
  assert.equal(local.chapters[1].done,2);
  assert.deepEqual(local.nodes.map(n=>n.proof),closure.nodes.map(n=>n.proof));
  assert.equal(local.nodes[1].proof,'local');
  assert.match(dotMixed(local,new Set(),2),/3 statements · 67% local/);
  assert.match(dotMixed(closure,new Set(),2),/3 statements · 33%/);
  assert.equal(dotMixed(closure,new Set(),2),dotMixed(buildGraphModel({...data,graphProgress:'closure'}),new Set(),2));
  assert.equal(dotMixed(local,new Set([1]),2),dotMixed(closure,new Set([1]),2));
});
