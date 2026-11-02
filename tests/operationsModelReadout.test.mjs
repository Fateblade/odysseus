import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const context = vm.createContext({});
vm.runInContext(readFileSync(new URL('../static/js/operationsModelReadout.js', import.meta.url), 'utf8').replace(/^export /gm, ''), context);
test('readouts distinguish decode, end-to-end, unmeasured and unavailable', () => {
  assert.equal(vm.runInContext('measurementSuffix({})', context), '(unmeasured)');
  assert.equal(vm.runInContext('measurementSuffix({measurements_available:false})', context), '(measurement unavailable)');
  assert.equal(vm.runInContext("measurementSuffix({last_measurement:{value:15.4,metric:'end_to_end_tps'}})", context), '(15.40 end-to-end TPS)');
  assert.equal(vm.runInContext("measurementSuffix({last_measurement:{value:26.9,metric:'decode_tps'}})", context), '(26.90 decode TPS)');
});
