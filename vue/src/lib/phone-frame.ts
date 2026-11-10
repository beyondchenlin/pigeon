/** Same-origin authenticated, bounded single-frame reads. No native encoder. */
export async function phoneFrame(deviceId: string, short: number, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(`/api/v1/device/${encodeURIComponent(deviceId)}/frame?short=${short}`, {
    credentials:'include', cache:'no-store', signal
  });
  if (!response.ok || !response.headers.get('content-type')?.startsWith('image/')) throw new Error('Frame unavailable');
  const blob=await response.blob();
  if (blob.size>2*1024*1024) throw new Error('Oversized frame');
  return blob;
}

export function pollFrames(read: (signal: AbortSignal)=>Promise<Blob>, draw: (image: ImageBitmap)=>void,
                           failed: ()=>void, interval=1200) {
  let stopped=false, timer:ReturnType<typeof setTimeout>|undefined, abort:AbortController|undefined;
  async function next() {
    if(stopped)return;
    abort=new AbortController();const deadline=setTimeout(()=>abort?.abort(),9000);
    try {
      const image=await createImageBitmap(await read(abort.signal));
      try { if(!stopped)draw(image); } finally { image.close(); }
    } catch { if(!stopped)failed(); }
    finally { clearTimeout(deadline);if(!stopped)timer=setTimeout(next,interval+Math.random()*250); }
  }
  void next();
  return ()=>{stopped=true;clearTimeout(timer);abort?.abort();};
}
