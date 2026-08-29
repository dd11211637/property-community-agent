import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import styles from "../styles/ui.module.css";
import { Button } from "./ui";

export function Dialog({ trigger, title, description, children, actions }: { trigger: ReactNode; title: string; description?: string; children: ReactNode; actions?: ReactNode }) {
  return <DialogPrimitive.Root><DialogPrimitive.Trigger asChild>{trigger}</DialogPrimitive.Trigger><DialogPrimitive.Portal><DialogPrimitive.Overlay className={styles.dialogOverlay} /><DialogPrimitive.Content className={styles.dialogContent}><div className={styles.dialogHeader}><div><DialogPrimitive.Title>{title}</DialogPrimitive.Title>{description ? <DialogPrimitive.Description>{description}</DialogPrimitive.Description> : null}</div><DialogPrimitive.Close asChild><Button iconOnly tone="ghost" aria-label="关闭"><X size={18} /></Button></DialogPrimitive.Close></div>{children}{actions ? <div className={styles.dialogActions}>{actions}</div> : null}</DialogPrimitive.Content></DialogPrimitive.Portal></DialogPrimitive.Root>;
}

export function Drawer({ trigger, title, children }: { trigger: ReactNode; title: string; children: ReactNode }) {
  return <DialogPrimitive.Root><DialogPrimitive.Trigger asChild>{trigger}</DialogPrimitive.Trigger><DialogPrimitive.Portal><DialogPrimitive.Overlay className={styles.dialogOverlay} /><DialogPrimitive.Content className={styles.drawerContent}><div className={styles.dialogHeader}><DialogPrimitive.Title>{title}</DialogPrimitive.Title><DialogPrimitive.Close asChild><Button iconOnly tone="ghost" aria-label="关闭导航"><X /></Button></DialogPrimitive.Close></div><DialogPrimitive.Description className="sr-only">移动端主导航</DialogPrimitive.Description>{children}</DialogPrimitive.Content></DialogPrimitive.Portal></DialogPrimitive.Root>;
}

export function Menu({ trigger, children }: { trigger: ReactNode; children: ReactNode }) {
  return <DropdownMenu.Root><DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className={styles.dropdown} align="end" sideOffset={8}>{children}</DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>;
}

export function MenuItem({ children, onSelect }: { children: ReactNode; onSelect?(): void }) { return <DropdownMenu.Item className={styles.menuItem} onSelect={onSelect}>{children}</DropdownMenu.Item>; }

export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return <TooltipPrimitive.Provider delayDuration={350}><TooltipPrimitive.Root><TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger><TooltipPrimitive.Portal><TooltipPrimitive.Content className={styles.tooltip} sideOffset={6}>{label}<TooltipPrimitive.Arrow /></TooltipPrimitive.Content></TooltipPrimitive.Portal></TooltipPrimitive.Root></TooltipPrimitive.Provider>;
}
