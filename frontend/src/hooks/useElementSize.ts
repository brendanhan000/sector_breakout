import { useCallback, useRef, useState } from 'react';

export interface Size {
  width: number;
  height: number;
}

/**
 * Observe an element's rendered size.
 *
 * Returns a CALLBACK ref, not a RefObject, and that is the whole point. A
 * component that early-returns a placeholder while its data loads does not
 * render the measured element on first paint, so a `useEffect(..., [])` fires
 * against a null ref and — having no dependencies — never runs again once the
 * real element mounts. The chart then stays frozen at its initial guess: too
 * narrow to fill its panel, and tall enough to overflow into the section below.
 *
 * A callback ref is invoked whenever the node attaches or detaches, which is
 * exactly the signal needed. Measuring in a ref callback and calling setState
 * unconditionally would risk a render/measure/render loop, so the observer
 * reports out of band and state only changes when the size really moved.
 */
export function useElementSize<T extends HTMLElement>(
  initial: Size = { width: 900, height: 520 },
): [(node: T | null) => void, Size] {
  const [size, setSize] = useState<Size>(initial);
  const observerRef = useRef<ResizeObserver | null>(null);

  const ref = useCallback((node: T | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;

    if (!node) return;

    const apply = (width: number, height: number) => {
      if (width <= 0 || height <= 0) return;
      setSize((current) =>
        Math.abs(current.width - width) < 1 && Math.abs(current.height - height) < 1
          ? current // identical object: no re-render
          : { width, height },
      );
    };

    const rect = node.getBoundingClientRect();
    apply(rect.width, rect.height);

    // jsdom has no ResizeObserver unless the test setup provides one.
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) apply(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(node);
    observerRef.current = observer;
  }, []);

  return [ref, size];
}
