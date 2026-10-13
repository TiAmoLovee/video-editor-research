interface Rate { num: number; den: number }
function validRate(rate: Rate) {
  return Number.isSafeInteger(rate.num) && rate.num > 0 && Number.isSafeInteger(rate.den) && rate.den > 0;
}
/** Decimal seconds or HH:MM:SS.ffffff, rounded to the nearest frame, half up. */
export function timecodeToFrame(text: string, rate: Rate): number | null {
  if (!validRate(rate) || text.length > 32) return null;
  const value = text.trim();
  const match = /^(?:(\d{1,10}):([0-5]\d):([0-5]\d)|(\d{1,10}))(?:\.(\d{1,6}))?$/.exec(value);
  if (!match) return null;
  const seconds = match[4] !== undefined ? BigInt(match[4])
    : BigInt(match[1]) * 3600n + BigInt(match[2]) * 60n + BigInt(match[3]);
  const scale = 1000000n;
  const ticks = seconds * scale + BigInt((match[5] ?? '').padEnd(6, '0') || '0');
  const denominator = scale * BigInt(rate.den);
  const result = (2n * ticks * BigInt(rate.num) + denominator) / (2n * denominator);
  return result <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(result) : null;
}
export function frameToTimecode(frame: string | number, rate: Rate): string {
  const number = typeof frame === 'string' && /^\d+$/.test(frame) ? Number(frame) : frame;
  if (typeof number !== 'number' || !Number.isSafeInteger(number) || number < 0 || !validRate(rate)) return '';
  const rateNum = BigInt(rate.num);
  const ticks = (2n * BigInt(number) * BigInt(rate.den) * 1000000n + rateNum) / (2n * rateNum);
  const seconds = ticks / 1000000n;
  return `${String(seconds / 3600n).padStart(2, '0')}:${String(seconds / 60n % 60n).padStart(2, '0')}:${String(seconds % 60n).padStart(2, '0')}.${String(ticks % 1000000n).padStart(6, '0')}`;
}
