export type ViewportBounds = { width: number; height: number; top: number; left: number; bottom: number; right: number };

// Browser chrome and IME can shrink/pan the visual viewport without resizing
// the layout viewport used by position:fixed. Insets are CSS pixels, not a
// hard-coded percentage or an assumed browser toolbar/keyboard height.
export function viewportBounds(width: number, height: number, visual?: Pick<VisualViewport, 'width' | 'height' | 'offsetTop' | 'offsetLeft'> | null): ViewportBounds {
  const valid = visual && [visual.width, visual.height].every(n => Number.isFinite(n) && n > 0);
  const w = valid ? visual.width : width, h = valid ? visual.height : height;
  const top = valid ? Math.max(0, visual.offsetTop || 0) : 0;
  const left = valid ? Math.max(0, visual.offsetLeft || 0) : 0;
  return { width: w, height: h, top, left, bottom: Math.max(0, height - top - h), right: Math.max(0, width - left - w) };
}

export function observeViewport(win: Window, update: (bounds: ViewportBounds) => void) {
  const visual = win.visualViewport;
  let frame = 0;
  const measure = () => {
    frame = 0;
    // Preserve native pinch zoom: do not shrink/reposition the app to undo the
    // user's magnification. Resume fitting when they return to normal scale.
    if (visual && Math.abs(visual.scale - 1) > 0.02) return;
    update(viewportBounds(win.innerWidth, win.innerHeight, visual));
  };
  const schedule = () => { if (!frame) frame = win.requestAnimationFrame(measure); };
  win.addEventListener('resize', schedule);
  win.addEventListener('pageshow', schedule);
  win.document.addEventListener('fullscreenchange', schedule);
  visual?.addEventListener('resize', schedule);
  visual?.addEventListener('scroll', schedule);
  measure();
  return () => {
    win.cancelAnimationFrame(frame);
    win.removeEventListener('resize', schedule);
    win.removeEventListener('pageshow', schedule);
    win.document.removeEventListener('fullscreenchange', schedule);
    visual?.removeEventListener('resize', schedule);
    visual?.removeEventListener('scroll', schedule);
  };
}
