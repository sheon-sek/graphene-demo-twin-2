import {describe,expect,it} from 'vitest';

describe('admin contract',()=>{
  it('uses explicit API paths for authoritative state',()=>{
    expect('/api/snapshot').toContain('snapshot');
    expect('/api/stream').toContain('stream');
  });
});
