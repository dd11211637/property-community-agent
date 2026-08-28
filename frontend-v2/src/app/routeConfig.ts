import type { Capability } from "../auth/capabilities";

export type RouteCapability = {
  path: string;
  label: string;
  public: boolean;
  requiredCapability?: Capability;
  navigation: "resident" | "operations" | "both" | "none";
};

export const routeCapabilities: RouteCapability[] = [
  { path: "/login", label: "登录", public: true, navigation: "none" },
  { path: "/", label: "首页", public: false, navigation: "both" },
  { path: "/repairs", label: "报修", public: false, navigation: "both" },
  { path: "/billing", label: "账单", public: false, navigation: "resident" },
  { path: "/community", label: "社区", public: false, navigation: "both" },
  { path: "/operations", label: "运营", public: false, requiredCapability: "operations", navigation: "operations" },
  { path: "/messages", label: "消息", public: false, navigation: "both" },
  { path: "/admin", label: "管理", public: false, requiredCapability: "admin", navigation: "operations" },
];
