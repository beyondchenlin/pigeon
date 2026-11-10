import { onBeforeUnmount, onMounted, ref, type Directive } from 'vue';

export function fitDisplay(width: number, height: number, availableWidth: number, availableHeight: number) {
  if (![width, height, availableWidth, availableHeight].every(n => Number.isFinite(n) && n > 0)) return { width: 0, height: 0 };
  const scale = Math.min(availableWidth / width, availableHeight / height);
  return { width: width * scale, height: height * scale };
}
export function galleryMediaHeight(height: number, compact: boolean, single: boolean, landscape = false) {
  const rows = single || landscape ? 1 : 2;
  const chrome = compact ? 60 : 66;
  return Math.max(24, Math.floor((height - (rows - 1) * 8 - (single ? 48 : 4)) / rows - chrome));
}
export function useMobileViewport() {
  const mobile = ref(window.innerWidth < 768);
  function update() {
    mobile.value = window.innerWidth < 768;
    document.documentElement.style.setProperty('--bk-viewport-height', `${window.visualViewport?.height || window.innerHeight}px`);
  }
  onMounted(() => { update(); window.addEventListener('resize', update); window.visualViewport?.addEventListener('resize', update); });
  onBeforeUnmount(() => { window.removeEventListener('resize', update); window.visualViewport?.removeEventListener('resize', update); });
  return mobile;
}

// Reuse real table headings as mobile labels; do not maintain a second, divergent set of fields.
const observers = new WeakMap<HTMLElement, () => void>();
export const responsiveTables: Directive<HTMLElement> = {
  mounted(root) {
    let frame = 0;
    const label = () => {
      frame = 0;
      root.querySelectorAll('table').forEach(table => {
        if (table.closest('[data-native-table]')) return;
        const headings = [...(table.tHead?.rows[0]?.cells || [])].map(h => h.textContent?.trim() || '');
        if (!headings.length) return;
        table.classList.add('bk-mobile-table');
        [...table.tBodies].forEach(body => [...body.rows].forEach(row => [...row.cells].forEach((cell, i) => {
          cell.dataset.mobileLabel = headings[i] || '';
          cell.classList.toggle('bk-wide-cell', cell.colSpan > 1);
        })));
      });
    };
    const observer = new MutationObserver(() => { if (!frame) frame = requestAnimationFrame(label); });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    label();
    observers.set(root, () => { observer.disconnect(); cancelAnimationFrame(frame); });
  },
  unmounted(root) { observers.get(root)?.(); observers.delete(root); }
};
