import { useEffect, useState } from 'react';

const key = 'clipforge:actor:v1';
const changed = 'clipforge:actor-changed';
function read(): string {
  try { const value = localStorage.getItem(key); return value && value.length <= 80 ? value : ''; }
  catch { return ''; }
}

// This is a user-supplied name for the operation record, not an authentication identity.
export function useActor(): [string, (value: string) => void] {
  const [actor, setActor] = useState(read);
  useEffect(() => {
    const update = () => setActor(read());
    const storage = (event: StorageEvent) => { if (event.key === key || event.key === null) update(); };
    window.addEventListener(changed, update); window.addEventListener('storage', storage);
    return () => { window.removeEventListener(changed, update); window.removeEventListener('storage', storage); };
  }, []);
  function save(value: string) {
    setActor(value);
    try { localStorage.setItem(key, value); window.dispatchEvent(new Event(changed)); }
    catch { /* The form remains usable when browser storage is unavailable. */ }
  }
  return [actor, save];
}
