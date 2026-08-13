import { act, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { useApi } from "../../src/hooks/useApi";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function Probe({ dependencyKey, loader }: { dependencyKey: string; loader: () => Promise<string> }) {
  const result = useApi(loader, dependencyKey);
  return <div>{result.loading ? "loading" : result.data}</div>;
}

it("ignores a stale response after the dependency changes", async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const view = render(<Probe dependencyKey="first" loader={() => first.promise} />);

  view.rerender(<Probe dependencyKey="second" loader={() => second.promise} />);
  await act(async () => second.resolve("new result"));
  expect(await screen.findByText("new result")).toBeInTheDocument();

  await act(async () => first.resolve("stale result"));
  expect(screen.getByText("new result")).toBeInTheDocument();
  expect(screen.queryByText("stale result")).not.toBeInTheDocument();
});
