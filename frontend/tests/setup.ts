import '@testing-library/jest-dom/vitest';

// Recharts' ResponsiveContainer measures its parent, which jsdom reports as
// zero. Stubbing the observer with a fixed size lets charts render in tests.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
  configurable: true,
  // Deliberately DIFFERENT from useElementSize's initial guess (900x520) so a
  // test can prove the component actually measured its container rather than
  // silently keeping the default.
  value: () => ({
    width: 1200,
    height: 600,
    top: 0,
    left: 0,
    right: 1200,
    bottom: 600,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  }),
});
