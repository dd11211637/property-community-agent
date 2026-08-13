import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "../../src/components/ConfirmDialog";

describe("ConfirmDialog", () => {
  it("uses the cancellation callback only for explicit cancellation", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        title="确认操作"
        summary={<span>摘要</span>}
        onConfirm={vi.fn()}
        onClose={onClose}
        onCancel={onCancel}
      />,
    );

    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(onCancel).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes after one successful confirmation without invoking cancellation", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onCancel = vi.fn();
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        title="确认操作"
        summary={<span>摘要</span>}
        onConfirm={onConfirm}
        onClose={onClose}
        onCancel={onCancel}
      />,
    );

    await user.click(screen.getByRole("button", { name: "确认提交" }));

    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
  });
});
