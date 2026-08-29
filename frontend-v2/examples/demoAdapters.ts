import type { AuthenticatedSession, AuthenticationPort } from "../src/auth/session";

const houses = [
  { id: "house-a", label: "1 栋 · 2 单元 · 1203", resolved: true, building: "1 栋", unit: "2 单元", roomNo: "1203" },
  { id: "house-b", label: "6 栋 · 1 单元 · 802", resolved: true, building: "6 栋", unit: "1 单元", roomNo: "802" },
];

function sessionFor(account: string): AuthenticatedSession {
  if (account.toLowerCase() === "manager") {
    return { status: "authenticated", accessToken: "demo-token", actor: { id: "actor-manager", displayName: "林经理", roles: ["MANAGER"], communityId: "community-demo", communityName: "桂语社区" }, houses, currentHouseId: "house-a" };
  }
  if (account.toLowerCase() === "resident") {
    return { status: "authenticated", accessToken: "demo-token", actor: { id: "actor-resident", displayName: "张晓雨", roles: ["RESIDENT"], communityId: "community-demo", communityName: "桂语社区" }, houses, currentHouseId: "house-a" };
  }
  throw new Error("请输入 resident 或 manager 进入对应预览。");
}

export const demoAuthentication: AuthenticationPort = {
  async signIn({ username, password }) {
    if (password !== "preview") throw new Error("预览口令为 preview。");
    return sessionFor(username);
  },
  async selectHouse(houseId) {
    const house = houses.find((item) => item.id === houseId);
    if (!house) throw new Error("未知 Demo 房屋。");
    return { houseId, building: house.building, unit: house.unit, roomNo: house.roomNo };
  },
};
