import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { type ReactElement, useRef } from "react";

import { useCardAnchor } from "./useCardAnchor";

const CARD_HEIGHT = 100;

let markMoveDown: (taskId: string) => () => void = () => () => {};
let heightAboveCards = 0;

function Board({ order, scope = "board" }: { order: string[]; scope?: string }) {
  const root = useRef<HTMLDivElement>(null);
  markMoveDown = useCardAnchor(root, order, scope);
  return (
    <div ref={root} data-testid="root">
      {order.map((id) => (
        <article key={id} data-task-id={id} tabIndex={-1} />
      ))}
    </div>
  );
}

/** Lays the cards out 100 px apart from the root's top, less the root's scroll position. */
function layOut(root: HTMLElement): void {
  let scrollTop = 0;
  Object.defineProperty(root, "scrollTop", {
    configurable: true,
    get: () => scrollTop,
    set: (value: number) => {
      scrollTop = value;
    },
  });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const cards = [...root.querySelectorAll<HTMLElement>("[data-task-id]")];
    const top =
      this === root ? 0 : heightAboveCards + cards.indexOf(this) * CARD_HEIGHT - scrollTop;
    const height = this === root ? 3 * CARD_HEIGHT : CARD_HEIGHT;
    return {
      top,
      bottom: top + height,
      left: 0,
      right: 100,
      width: 100,
      height,
      x: 0,
      y: top,
      toJSON: () => ({}),
    };
  });
}

function scrollTo(root: HTMLElement, top: number): void {
  root.scrollTop = top;
  fireEvent.scroll(root);
  act(() => vi.advanceTimersByTime(20));
}

/** Shows cards a to d, scrolled so that card b shows first, 50 px of it above the view. */
function renderBoard(): { root: HTMLElement; rerender: (board: ReactElement) => void } {
  const { rerender } = render(<Board order={["a", "b", "c", "d"]} />);
  const root = screen.getByTestId("root");
  layOut(root);
  scrollTo(root, 150);
  return { root, rerender };
}

beforeEach(() => {
  heightAboveCards = 0;
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame"] });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("keeps the card at the top of the view in place when a card comes in above it, or it moves", () => {
  const { root, rerender } = renderBoard();

  rerender(<Board order={["new", "a", "b", "c", "d"]} />);
  const afterNewCard = root.scrollTop;
  rerender(<Board order={["b", "new", "a", "c", "d"]} />);

  expect([afterNewCard, root.scrollTop]).toEqual([250, 50]);
});

it("keeps the top card in place after what shows above it changed height without a scroll", () => {
  const resized: ResizeObserverCallback[] = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        resized.push(callback);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  heightAboveCards = 100;
  const { rerender } = render(<Board order={["a", "b", "c", "d"]} />);
  const root = screen.getByTestId("root");
  layOut(root);
  scrollTo(root, 0);

  heightAboveCards = 0;
  act(() => resized.at(-1)?.([], {} as ResizeObserver));
  rerender(<Board order={["new", "a", "b", "c", "d"]} />);

  expect(root.scrollTop).toBe(CARD_HEIGHT);
});

it("puts the next card in place of a card that the user moves down, and focuses it", () => {
  const { root, rerender } = renderBoard();

  markMoveDown("b");
  rerender(<Board order={["new", "a", "c", "d", "b"]} />);

  expect(root.scrollTop).toBe(250);
  expect(document.activeElement).toBe(root.querySelector('[data-task-id="c"]'));
});

it("moves nothing for a new scope, and a failed move leaves the view to the top card", () => {
  const { root, rerender } = renderBoard();

  rerender(<Board order={["new", "a", "b", "c", "d"]} scope="search" />);
  const afterNewScope = root.scrollTop;
  markMoveDown("b")();
  rerender(<Board order={["x", "new", "a", "b", "c", "d"]} scope="search" />);

  expect([afterNewScope, root.scrollTop]).toEqual([150, 250]);
});
