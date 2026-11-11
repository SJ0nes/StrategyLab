import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useLoad } from "../src/useLoad";

afterEach(cleanup);

/** A request the test answers by hand, so it can choose the order answers arrive in. */
function pending(): { promise: Promise<string>; answer: (value: string) => void } {
  let answer: (value: string) => void = () => undefined;
  const promise = new Promise<string>((resolve) => {
    answer = resolve;
  });
  return { promise, answer };
}

function Shows({ name, request }: { name: string; request: () => Promise<string> }) {
  const load = useLoad(name, request);
  return <p>{load.state === "ready" ? load.value : load.state}</p>;
}

describe("useLoad", () => {
  it("drops an answer that arrives after a newer request was made", async () => {
    const slow = pending();
    const fast = pending();
    const { rerender } = render(<Shows name="first" request={() => slow.promise} />);
    rerender(<Shows name="second" request={() => fast.promise} />);

    await act(async () => fast.answer("second strategy"));
    expect(screen.getByText("second strategy")).toBeInTheDocument();

    await act(async () => slow.answer("first strategy"));
    expect(screen.getByText("second strategy")).toBeInTheDocument();
    expect(screen.queryByText("first strategy")).toBeNull();
  });

  it("shows loading again while a new request is out", async () => {
    const first = pending();
    const second = pending();
    const { rerender } = render(<Shows name="first" request={() => first.promise} />);
    await act(async () => first.answer("first strategy"));

    rerender(<Shows name="second" request={() => second.promise} />);

    expect(screen.getByText("loading")).toBeInTheDocument();
  });
});
