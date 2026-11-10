// Focused real-handler tests with an isolated bridge and no game/browser input.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../client/web/app.js'), 'utf8');
const handlers = source.slice(source.indexOf('  const tourCall='), source.indexOf('  function currentBondControls()'));
function harness(call, nativeMode=true) {
  const timers=[], messages=[], state={tour:{index:1},onboarding:{required:true,steps:['accounts']},config:{keep:'choices'}};
  const context={state,nativeMode,N:{call},D:{clone:x=>structuredClone(x)},tourSteps:Array.from({length:6},(_,i)=>[String(i)]),
    render(){}, main:{scrollTop:99}, toast:m=>messages.push(m), localStorage:{setItem(){throw Error('unexpected settings write');}},
    withTimeout:(promise)=>Promise.race([promise,new Promise((_,reject)=>timers.push(()=>reject(Error('响应超时'))))])};
  vm.createContext(context);vm.runInContext(handlers+';globalThis.skip=skipTour;globalThis.next=advanceTour;',context);
  return {context,state,timers,messages};
}
(async()=>{
  let calls=0;
  for(let step=0;step<6;step++) {
    calls=0;
    const success=harness(async method=>{calls++;assert.equal(method,'skip_onboarding');return {ok:true,required:false,steps:['accounts']};});
    success.state.tour.index=step;
    await success.context.skip();assert.equal(success.state.tour,null);assert.equal(success.state.config.keep,'choices');assert.equal(calls,1);
  }
  for (const response of [undefined,{ok:false,error:'disk failed'},{ok:true,required:true}]) {
    const h=harness(async()=>response);await h.context.skip();assert.equal(h.state.tour.busy,false);assert.equal(h.state.onboarding.required,true);
  }
  for (const action of ['skip','next']) {
    let resolve;calls=0;
    const h=harness(()=>{calls++;return new Promise(r=>resolve=r);});
    const pending=h.context[action]();await h.context[action]();assert.equal(calls,1);
    h.timers[0]();await pending;assert.equal(h.state.tour.busy,false);assert.equal(h.state.onboarding.required,true);
    resolve({ok:true,required:false});await new Promise(r=>setImmediate(r));
    assert.equal(h.state.tour.index,1);assert.equal(h.state.onboarding.required,true);assert.equal(h.messages.length,1);
    h.context.N.call=async()=>({ok:true,required:false});await h.context.skip();assert.equal(h.state.tour,null);
  }
  const demo=harness(()=>{throw Error('unexpected native call');},false);await demo.context.skip();assert.equal(demo.state.tour,null);
  assert(source.includes('data-action="tour-skip"'));
  assert(source.includes('一键跳过全部引导'));
  console.log('Onboarding UI: skip, failure, timeout, duplicate click, late response, retry, demo passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
