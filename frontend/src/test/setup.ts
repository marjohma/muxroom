import "@testing-library/jest-dom/vitest";

// jsdom lacks APIs that Radix popovers (Select) call.
Element.prototype.scrollIntoView ??= function scrollIntoView() {};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= function releasePointerCapture() {};
