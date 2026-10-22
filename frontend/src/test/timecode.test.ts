import { describe, expect, it } from 'vitest';
import { frameToTimecode, timecodeToFrame } from '../utils/timecode';

describe('frame aligned timecode input', () => {
  it('rounds half up and supports seconds and hour/minute/second notation', () => {
    expect(timecodeToFrame('0.050000', { num: 30, den: 1 })).toBe(2);
    expect(timecodeToFrame('01:02:03.100000', { num: 30, den: 1 })).toBe(111693);
    expect(timecodeToFrame('10.01', { num: 30000, den: 1001 })).toBe(300);
  });
  it('preserves frames through display and parse with rational rates', () => {
    for (const num of [24, 30, 30000]) {
      const rate = { num, den: num === 30000 ? 1001 : 1 };
      for (const frame of [0, 1, 31, 1234, 71983, 10800000]) {
        expect(timecodeToFrame(frameToTimecode(frame, rate), rate)).toBe(frame);
      }
    }
  });
  it('rejects partial, negative, nonfinite, unsafe or invalid timecodes', () => {
    for (const text of ['', '-1', 'NaN', '1e3', '00:60:00', '00:00:60', '1.', '0.1234567']) {
      expect(timecodeToFrame(text, { num: 30, den: 1 })).toBeNull();
    }
    expect(timecodeToFrame('1', { num: 0, den: 1 })).toBeNull();
    expect(frameToTimecode('invalid', { num: 30, den: 1 })).toBe('');
  });
});
