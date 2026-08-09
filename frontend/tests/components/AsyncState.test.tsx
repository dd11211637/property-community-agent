import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../../src/api/client";
import { Empty, ErrorState } from "../../src/components/AsyncState";

describe("shared async states", () => {
  it("shows empty content without treating it as a failure", () => {
    render(<Empty title="暂无账单" />);
    expect(screen.getByText("暂无账单")).toBeInTheDocument();
  });

  it.each([[401, "登录已失效"], [403, "无权查看"], [404, "不存在"], [409, "已被更新"], [422, "有误"], [503, "尚未装配"]])("renders the %i state", (status, copy) => {
    render(<ErrorState error={new ApiError(status, "TEST", "server", "req-test")} />);
    expect(screen.getByRole("alert")).toHaveTextContent(copy);
    expect(screen.getByText(/req-test/)).toBeInTheDocument();
  });
});
