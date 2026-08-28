import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Dialog, Menu, MenuItem } from "../../src/shared/overlays";

describe("overlay semantics", () => {
  it("traps modal dialog focus, closes with Escape and returns focus", async () => {
    const user = userEvent.setup();
    render(<Dialog trigger={<button>打开确认</button>} title="确认操作"><button>主要操作</button><button>次要操作</button></Dialog>);
    const trigger = screen.getByRole("button", { name: "打开确认" });
    await user.click(trigger);
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
    await user.tab();
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("uses non-modal menu keyboard behavior", async () => {
    const user = userEvent.setup();
    render(<Menu trigger={<button>打开菜单</button>}><MenuItem>第一项</MenuItem><MenuItem>第二项</MenuItem></Menu>);
    const trigger = screen.getByRole("button", { name: "打开菜单" });
    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
