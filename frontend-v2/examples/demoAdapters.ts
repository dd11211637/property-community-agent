import type { AuthenticationPort, SessionState } from "../src/auth/session";

const houses = [
  { id: "house-a", label: "1 栋 2 单元 1203", address: "桂语社区东区" },
  { id: "house-b", label: "6 栋 1 单元 802", address: "桂语社区西区" },
];

function sessionFor(account: string): SessionState {
  if (account.toLowerCase() === "manager") {
    return { status: "authenticated", actor: { id: "actor-manager", displayName: "林经理", roles: ["MANAGER"], communityName: "桂语社区" }, houses, currentHouseId: "house-a" };
  }
  if (account.toLowerCase() === "resident") {
    return { status: "authenticated", actor: { id: "actor-resident", displayName: "张晓雨", roles: ["RESIDENT"], communityName: "桂语社区" }, houses, currentHouseId: "house-a" };
  }
  throw new Error("请输入 resident 或 manager 进入对应预览。");
}

export const demoAuthentication: AuthenticationPort = {
  async signIn({ account, password }) {
    if (password !== "preview") throw new Error("预览口令为 preview。");
    return sessionFor(account);
  },
};
