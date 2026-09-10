// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { claimEvening, eveningEligible, episodeKey, isEvening } from './evening';
import type { ThemePreference } from './preference';
const preference:ThemePreference={version:1,known:true,theme:'light',installation_id:'a'.repeat(32),owner:'test',base_path:'/c/test',revision:'r1',evening:{disabled:false,snooze_until:0}};
beforeEach(()=>{localStorage.clear();vi.restoreAllMocks();Object.defineProperty(navigator,'locks',{configurable:true,value:{request:async(_name:string,callback:()=>Promise<boolean>)=>callback()}});});
it.each([[18,59,false],[19,0,true],[23,59,true],[0,0,false]])('local %i:%i eligibility is %s',(hour,minute,expected)=>{expect(isEvening(new Date(2026,8,9,Number(hour),Number(minute)))).toBe(expected);});
it('requires known light state, respects durable opt-out and fourteen-day snooze',()=>{const now=new Date(2026,8,9,20);expect(eveningEligible(preference,now)).toBe(true);expect(eveningEligible(null,now)).toBe(false);expect(eveningEligible({...preference,theme:'dark'},now)).toBe(false);expect(eveningEligible({...preference,evening:{disabled:true,snooze_until:0}},now)).toBe(false);expect(eveningEligible({...preference,evening:{disabled:false,snooze_until:now.getTime()/1000+14*86400}},now)).toBe(false);});
it('deduplicates reloads and scope-isolates another cabinet',async()=>{const now=new Date(2026,8,9,20);expect(await claimEvening(preference,now)).toBe(true);expect(await claimEvening(preference,now)).toBe(false);expect(await claimEvening({...preference,base_path:'/c/other'},now)).toBe(true);});
it('does not repeat after backward clock changes',async()=>{expect(await claimEvening(preference,new Date(2026,8,9,20))).toBe(true);expect(await claimEvening(preference,new Date(2026,8,8,20))).toBe(false);});
it.each(['{',JSON.stringify({at:'invalid',day:'bad'})])('skips malformed episode storage %s',async(raw)=>{localStorage.setItem(episodeKey(preference),raw);expect(await claimEvening(preference,new Date(2026,8,9,20))).toBe(false);});
it('skips without storage capacity or atomic cross-tab locking', async () => {
  // Node 26's test setup replaces its unavailable storage with MemoryStorage.
  const storagePrototype = Object.getPrototypeOf(localStorage) as Storage;
  const setItem = vi.spyOn(storagePrototype, 'setItem').mockImplementation(() => {
    throw new DOMException('full', 'QuotaExceededError');
  });
  try {
    expect(await claimEvening(preference, new Date(2026, 8, 9, 20))).toBe(false);
  } finally {
    setItem.mockRestore();
  }
  Object.defineProperty(navigator, 'locks', {value: undefined});
  expect(await claimEvening(preference, new Date(2026, 8, 9, 20))).toBe(false);
});
